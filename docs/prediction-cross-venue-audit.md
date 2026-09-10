# Аудит prediction cross-venue data layer — Phase 1

Дата: 9 сентября 2026 года. Проверки выполнены приблизительно в 13:05–13:11 UTC.

Объём: аудит существующего `poly_crypto`, фактической локальной SQLite, baseline, вариантов хранения и будущей архитектуры. Phase 2–13 не начаты. Код приложения, рабочая БД, конфигурация и сервисы не изменены. Следующая фаза требует явного подтверждения владельца.

Главный вывод: существующий MVP можно расширить отдельным prediction-слоем. Однако локальный Chainlink collector остановлен, его история прерывиста, а приём сообщений не имеет точного времени получения и полного raw-архива. Сначала нужны контракт данных и восстановление наблюдаемого reference layer. Наличие одинакового BTC 5m settlement на трёх площадках пока не доказано.

## A. Текущее состояние

### Репозиторий и история

- Ветка: `poly_crypto/V1.0`; HEAD: `a3d74b2` (`Document VPS deployment`). Предыдущий коммит: `f8ef96c` (`Prepare poly crypto MVP v1.0`).
- До аудита `git status --short` показывал только пользовательский untracked-файл `PROJECT_REVIEW_FOR_CHATGPT.md`. Он прочитан как контекст и оставлен без изменений.
- Физический `AGENTS.md` в дереве не найден; применены инструкции AGENTS.md из сообщения пользователя.
- Изучены `PROJECT_PLAN.md`, `BACKLOG.md`, `ARCHITECTURE.md`, `README.md`, `CHANGELOG.md`, runbook и документы об источниках.
- Существующие фазы 0–9 относятся к candle-dashboard MVP. Новые Phase 1–13 относятся к prediction-слою и не заменяют историческую нумерацию.
- Backend: Python, FastAPI, SQLAlchemy 2, Alembic, httpx. Frontend: Next.js, React, TypeScript, lightweight-charts. Фактический build использовал Next.js 15.5.18.
- Реализованы Binance historical 5m ingestion, Chainlink latest-report polling, локальная агрегация свечей, imbalance engine, API, dashboard и экспорт.

### Chainlink: где и как работает

| Компонент | Файл / поведение |
|---|---|
| Worker | `services/api/app/workers/realtime_worker.py` |
| Совместимая CLI | `services/api/app/scripts/poll_chainlink_streams.py` |
| HTTP и декодирование | `services/api/app/feeds/chainlink_streams.py` |
| Feed mapping | `services/api/app/feeds/symbols.py`, `app/core/config.py` |
| Запись тиков и свечей | `services/api/app/candles/realtime_storage.py` |
| Модели и соединение | `services/api/app/db/models.py`, `session.py` |
| Состояние источников | `services/api/app/api/routes/status.py` |
| Windows supervisor | `ops/windows/run-chainlink-worker.ps1` и скрипты регистрации/статуса |
| Linux supervisor | `ops/linux/poly-crypto-chainlink-worker.service.example` |

Запуск из `services/api`:

```text
python -m app.scripts.poll_chainlink_streams --asset all --interval 10
```

Это REST polling, а не WebSocket streaming: подписанный HMAC запрос `GET https://api.dataengine.chain.link/api/v1/reports/latest?feedID=...`. BTC, ETH и SOL запрашиваются последовательно. После всей итерации выполняется `sleep(10)`, поэтому реальный период для одного актива включает HTTP, запись, расчёты и retry остальных активов.

Настроенные feed IDs проверены по разрешённым полям локальной конфигурации и фактически сохранённым payload:

| Mapping проекта | Feed ID |
|---|---|
| BTC/USDT | `0x00039d9e45394f473ab1f050a1b963e6b05351e52d71e507509ada0c95ed75b8` |
| ETH/USDT | `0x000362205e10b3a147d02792eccee483dca6c7b44ecce7012cb8c6e0b68b3ae9` |
| SOL/USD | `0x0003b778d3f6b2ac4991302b89cb313f99a42467d6c9c5f96f57c29c0d2bc24f` |

Названия пар здесь отражают mapping проекта. Их идентичность settlement feed конкретного prediction market не проверена. Особенно нельзя приравнивать BTC/USDT к BTC/USD автоматически.

`observationsTimestamp` декодируется как UTC, затем при записи в SQLite timezone удаляется: `astimezone(UTC).replace(tzinfo=None)`. Таким образом `price_ticks.timestamp` — время наблюдения источника, сохранённое как naive UTC. API не всегда добавляет `Z`/offset при сериализации.

Отдельного `received_timestamp` нет. `created_at = CURRENT_TIMESTAMP` фиксирует время вставки в БД с секундной точностью; это не точное время получения по сети. Пример последнего BTC tick: observation `07:50:15`, insert `07:50:17` UTC. Разницу нельзя выдавать за чистую сетевую latency.

`price_ticks.raw_payload` содержит `feedID`, `validFromTimestamp`, `observationsTimestamp`, `expiresAt`, целочисленные строки `price/bid/ask`. Полный ответ, `fullReport`, подписи и исходные байты не сохраняются. В `candles.raw_payload` остаются декодированные поля последнего report окна. Поэтому существующий raw-слой полезен, но не позволяет полностью переиграть декодирование исходного report.

### Reliability, watchdog и downtime

Есть:

- до 5 попыток HTTP; backoff по умолчанию 1, 2, 4, 8 секунд между попытками, ограничение задержки 30 секунд; timeout одного HTTP — 20 секунд;
- обработка ошибок по активам; непрерывный цикл продолжает работу после неуспешной итерации;
- журналы сетевых ошибок, повторов, закрытия свечей и пропущенных 5m окон;
- Windows supervisor с перезапуском через 10 секунд после выхода child-процесса и mutex от повторного launcher;
- Startup launcher `PolyCryptoChainlinkWorker.cmd` в профиле пользователя;
- шаблон systemd: `Restart=always`, `RestartSec=10`, `WantedBy=multi-user.target`;
- восстановление свечей из уже сохранённых ticks при старте.

Нет подтверждённого восстановления отсутствующих исторических ticks. Startup repair заново строит свечи из имеющихся наблюдений; он не выполняет gap backfill. Межтиковые gaps меньше пропуска целого свечного окна штатный detector не учитывает.

`GET /api/status` возвращает статическую сводку и не доказывает доступность БД/collector. `GET /api/status/sources` читает БД и считает свежесть, но `is_live` определяется по самому свежему tick любого актива, с порогом `poll_interval × 6` (60 секунд). Свежий ETH может скрыть остановку BTC. Полного heartbeat, uptime, persisted error/retry counters, алертов и внешнего freshness watchdog нет. `data_source_runs` используется historical jobs, а не как журнал каждой realtime-сессии.

Подтверждённая хронология локальной остановки, UTC:

1. 8 сентября 07:50:10: Windows System Event 1074 фиксирует инициированный пользователем restart через StartMenuExperienceHost.
2. Последний BTC tick: 07:50:15; последние HTTP-запросы в старом worker log возвращали 200.
3. 07:50:30: System Event 13 фиксирует завершение работы ОС; около 07:50:50 — новый старт ОС.
4. 07:52:24: `chainlink-worker-2026-09-08.log` содержит только `starting Chainlink worker`, размер 66 байт. Подтверждения завершённого repair, первого poll или повторного запуска нет.
5. На момент аудита процессы worker/watchdog не найдены, задача Task Scheduler с Chainlink в названии не найдена; Startup launcher существует.
6. Read-only запрос BTC latest report 9 сентября получил HTTP 200 и `fullReport`, observation epoch `1788959444`. Записи в БД этот запрос не выполнял.

Вывод: исходная остановка связана с reboot, а длительный последующий простой — с отсутствием успешно работающего автозапуска. Точная причина исчезновения launcher/child после 07:52:24 не установлена. В Application events за 10:50–11:15 МСК нет найденной ошибки Python, объясняющей этот запуск. Полный startup repair, завершение родительского процесса или особенности shell/автозапуска остаются гипотезами, а не установленной причиной. Истечение credentials текущая проверка BTC не подтверждает.

Существующий repair загружает все ticks актива через `.all()`, группирует их в памяти и обновляет все его свечи. При росте истории это удлиняет старт и создаёт ненаблюдаемую паузу до первого poll. Исключение startup repair находится вне обработчика основного loop.

Документы описывают VPS `155.212.183.185`, systemd worker и ежечасные backup с 48 копиями. В текущем аудите HTTP `:8080/api/status/sources` и SSH `:22` завершились timeout. Состояние удалённой БД, сервисов, backup и факт их остановки не установлены. Недоступность с этой машины не доказывает downtime VPS.

### Фактическая локальная БД

Проверен именно `services/api/poly_crypto.db`; локальный `DATABASE_URL` указывает на него. Соединение SQLite открывалось с `mode=ro` и для основного аудита `query_only=ON`.

| Проверка | Результат |
|---|---:|
| Размер | 526 856 192 байта = 526,86 MB = 502,45 MiB |
| `PRAGMA quick_check` | `ok` |
| `journal_mode` | `delete`, не WAL |
| Alembic revision | `0001_initial` |
| `assets` | 3 |
| `price_ticks` | 1 051 815 |
| `candles` | 128 404 |
| `imbalance_events` | 45 047 |
| `data_source_runs` | 15 |
| Prediction-таблицы | отсутствуют |

`quick_check` подтверждает структурную проверку SQLite, а не финансовую корректность всех значений. `dbstat` недоступен в установленной сборке SQLite; размер отдельных таблиц/индексов не измерен.

| Актив | Chainlink ticks | Первый timestamp UTC | Последний timestamp UTC |
|---|---:|---|---|
| BTC | 350 586 | 2026-05-18 19:08:40 | 2026-09-08 07:50:15 |
| ETH | 350 609 | 2026-05-18 19:08:42 | 2026-09-08 07:50:16 |
| SOL | 350 620 | 2026-05-18 19:02:51 | 2026-09-08 07:50:02 |

Все ticks имеют `source=chainlink_streams`. На 9 сентября около 13:10 UTC последний BTC tick устарел приблизительно на 29 часов 20 минут.

| Свечи по источнику | BTC | ETH | SOL |
|---|---:|---:|---:|
| `binance_klines` | 25 921 | 25 920 | 25 920 |
| `chainlink_streams` | 16 878 | 16 880 | 16 885 |
| `chainlink_candlestick` | 0 | 0 | 0 |

Binance хранит февраль–май 2026, а не актуальные сентябрьские historical candles. Старое сообщение Candlestick HTTP 401 подтверждено документацией, но этот endpoint в аудите повторно не вызывался; оно не относится к проверенному текущему Streams HTTP 200.

### BTC gaps и рост

Методика gaps: сортировка всех BTC `chainlink_streams` по времени наблюдения, затем разность соседних timestamps. Порог строго `>`. Это разрывы между сохранёнными наблюдениями, а не доказанное число потерянных сообщений upstream: latest polling не получает все исходные reports.

| Метрика | Результат |
|---|---:|
| Медианный интервал | 13 секунд |
| p95 / p99 интервала | 20 / 35 секунд |
| Интервалы >30 секунд | 6 388 |
| Интервалы >60 секунд | 606 |
| Интервалы >300 секунд | 78 |
| Интервалы >1 часа | 55 |
| Максимальный gap | 1 316 087 секунд = 15 дней 05:34:47 |
| Группы дубликатов BTC по timestamp/source | 0 |

Крупнейшие gaps:

| Начало UTC | Конец UTC | Секунд |
|---|---|---:|
| 2026-05-20 15:08:20 | 2026-06-04 20:43:07 | 1 316 087 |
| 2026-08-18 14:45:37 | 2026-08-31 14:36:03 | 1 122 626 |
| 2026-08-10 18:10:12 | 2026-08-16 16:17:24 | 511 632 |

Между первой и последней BTC-свечой ожидается 32 410 окон по 5 минут; присутствует 16 878, отсутствует 15 532. Покрытие окон — 52,08%. Наличие свечи не означает полное покрытие её ticks; эту долю нельзя называть uptime.

Физический размер файла при повторных проверках в ходе аудита не изменился: наблюдаемый рост остановленной БД — 0 байт. Это не прогноз роста после восстановления.

За 1–7 сентября все три актива дали в среднем 16 414,71 ticks/сутки; отдельные дни — 12 361–19 488. Суммарный размер JSON `raw_payload` за эти дни — 3,42–5,40 MB/сутки. Это только содержимое поля JSON, без остальных колонок, индексов, свечей и журналов.

Планировочный верхний ориентир при идеальном периоде 10 секунд: 8 640 ticks/сутки на актив, 25 920 на три. При условных 0,5–0,8 kB на запись с индексами это 13–21 MB/сутки только tick-слоя; ещё нужны свечи, события, logs и backup. Это допущение, а не замер физического прироста. После восстановления нужны суточные замеры размера БД/журнала при неизменной retention.

Текущий Windows log от 31 августа вырос до 76 473 926 байт. Ротация по дате происходит при перезапуске child, а не ежедневно внутри непрерывного запуска; лимит объёма/retention в wrapper не задан.

### Что уже есть по Polymarket

Поиск по коду, конфигурации и документации: `Polymarket`, `CLOB`, `prediction`, `YES/NO`, `order book`, `WebSocket`, `Limitless`, `DexSport`.

- В production-коде нет адаптеров Polymarket/Limitless/DexSport, discovery, token ingestion, L2-реконструкции или prediction trades.
- Нет прикладного WebSocket collector для рынков. Транзитивная WS-зависимость сервера не является реализацией market streaming.
- `docs/data-sources.md` и `ARCHITECTURE.md` содержат разграничение spot/oracle и outcome prices.
- `PROJECT_REVIEW_FOR_CHATGPT.md`, разделы 10–11, содержит исследовательские идеи, ссылки Gamma/CLOB и предложение `polymarket_*` таблиц. Это документ, не реализация. Его per-venue schema не следует переносить буквально: новое задание требует единую `prediction_*` модель.
- Имеющиеся imbalance events не доказывают predictive или arbitrage edge.

### Baseline

| Проверка | Результат текущего запуска |
|---|---|
| Python AST всех 47 файлов `app` | успешно |
| `python -m unittest discover -s tests` | 24/24 успешно |
| `python -m ruff check app tests` | не выполнен: модуль Ruff отсутствует в venv |
| `npm run lint` | успешно |
| `npm run test` | успешно; это `tsc --noEmit`, не frontend unit suite |
| `npm run build` | успешно |
| Fresh `alembic upgrade head` | успешно до `0001_initial` на отдельной временной БД |
| SQLite `quick_check` | `ok` |
| BTC Streams read-only HTTP | 200; исходный `fullReport` присутствует |
| VPS HTTP / SSH | timeout; live deployment не проверен |

Baseline не объявляется полностью зелёным из-за отсутствующего Ruff и непроверенного VPS. Тесты API используют изолированную БД; passing tests не означают работающий production collector. Временная миграция не применялась к рабочей БД. Dependencies и lockfile не менялись.

## B. Повторно используемые компоненты

Без изменения существующего поведения можно сохранить:

- FastAPI app/router composition, API dependency patterns и Next.js shell;
- существующие Binance ingestion, `candles`, `price_ticks`, imbalance engine и dashboard;
- SQLAlchemy/Alembic как инструменты; новый prediction engine/session должен быть отдельным;
- UTC 5m helpers для группировки, после проверки их применения к границам конкретного market;
- retry helpers и isolated API tests как заготовки проверок;
- systemd-шаблоны как основу конфигурации отдельных новых workers;
- CSV/JSON UX-подход для research export.

Chainlink decoder, storage upsert, supervisor и health-route можно развивать, но они не готовы без изменений к точным latency/reference требованиям. `PriceTick` и `Candle` не следует использовать как универсальные модели outcome-token data. Imbalance engine не является готовым order-book scanner.

## C. Что исправить до prediction ingestion

Приоритетные изменения следующей разрешённой итерации:

1. Выбрать и проверить реально доступный 24/7 host для reference collector; выяснить сбой автозапуска после reboot. Успешный restart service и freshness после него должны проверяться отдельно.
2. Добавить heartbeat по collector/asset, last received/observed/persisted, last success/error, process start и stale alert. Сохранить существующие API-контракты, расширить status либо добавить отдельный route.
3. Захватывать UTC `received_timestamp` до decode/storage и сохранять полный исходный payload. Старые отсутствующие receive timestamps оставить NULL; `created_at` не переименовывать в receive time задним числом.
4. Ограничить startup repair диапазоном/checkpoint; добавить progress и замер времени старта. Исторические отсутствующие ticks не синтезировать.
5. Изолировать задержки BTC от ETH/SOL, контролировать cadence и возраст самого report. Повторение одного report внутри текущего 5m окна сейчас не считается `stale_report`.
6. Проверить rollback/транзакции: `_poll_asset` ловит исключение без явного `db.rollback()`, а Session общая на итерацию; DB failure может повлиять на следующие активы. В `imbalance_storage.py` удаление старых событий и вставка новых выполняют отдельные commits: ошибка между ними может оставить окно без прежних событий. Это риск неатомарной замены, а не отсутствие commit.
7. Проверить idempotency: dedup ticks выполнен SELECT перед INSERT, но UNIQUE `(asset_id, source, timestamp)` отсутствует. Конкурирующие writers могут создавать дубли. Возможную миграцию согласовать в Phase 3, предварительно проверив существующие данные.
8. Проверить поздние reports: candle.close обновляется ценой поступившего report до проверки `stale_report`; порядок arrival может исказить OHLC старого окна. Нужна отдельная проверка порядка observation timestamps.
9. Ввести retention журналов и disk monitoring, проверить доступный объём и внешний backup. Рабочую SQLite в OneDrive не использовать как новое high-frequency хранилище.

Текущий median polling 13 секунд не позволяет измерять реакцию BTC в buckets 25–100 ms. Для точного strike нужен подтверждённый settlement reference, правило выбора граничного observation и более подходящий cadence/stream, если источник предоставляет его. Ближайший локальный tick нельзя автоматически считать strike.

## D. Рекомендация по хранению

### Модель объёма, не замер площадок

Пока реального L2 sample нет. Не подтверждено даже наличие L2 у всех трёх venues. Для capacity planning используем условные 3 venues × 2 outcome books = 6 книг активного BTC 5m окна. Предварительная подписка на следующее окно увеличит нагрузку отдельно.

Пусть `r` — среднее число событий в секунду на книгу, `B` — средние байты raw event с envelope. Тогда:

```text
events/day = 6 × r × 86 400
event rows/day = events/day          # одно событие на строку
raw GB/day = events/day × B / 1e9
raw GB/month = raw GB/day × 30
```

Для таблицы `B = 1 000 байт`. Здесь события уже приведены к уровню книги; WS-frame с несколькими updates нельзя дополнительно посчитать как независимую полную копию каждого update без учёта фактических байтов.

| Сценарий | r / книгу / сек | Всего событий/сек | Events и raw rows/сутки | Raw GB/сутки | Raw GB/30 дней |
|---|---:|---:|---:|---:|---:|
| Низкая активность | 1 | 6 | 518 400 | 0,52 | 15,55 |
| Плановый | 10 | 60 | 5 184 000 | 5,18 | 155,52 |
| Высокая активность | 50 | 300 | 25 920 000 | 25,92 | 777,60 |

При хранении всех событий в row-store с условным overhead ×1,5–3 плановый сценарий потребует 7,78–15,55 GB/сутки или 233–467 GB/месяц до backup/реплик. Snapshot payload может быть намного больше 1 kB; показатель нужно заменить замером.

Если материализовать каждый из 40 уровней каждой книги на каждом событии, плановый сценарий даст ещё 207 360 000 level rows/сутки. Вместе с event rows — 212 544 000 строк/сутки. При условных 50–100 байтах на level row это дополнительные 10,37–20,74 GB/сутки ещё до индексов. Полную книгу на каждый delta сохранять не следует.

Практичнее raw deltas + периодические восстановимые snapshots. Например, один snapshot на книгу каждые 5 секунд — 103 680 snapshots/сутки; при 4 kB каждый — 0,415 GB/сутки (12,44 GB/30 дней). Это пример storage checkpoint, не разрешение прореживать входящие события или сканировать раз в 5 секунд.

Для compressed Parquet предположим коэффициент ×3–6: плановые 5,18 raw GB превращаются примерно в 0,86–1,73 GB/сутки (25,92–51,84 GB/месяц), без snapshot/derived-слоя. Коэффициент не измерен; точные значения зависят от payload и сортировки. Byte-exact raw хранить отдельным полем; нормализация не должна уничтожать исходное сообщение.

Размер trades, metadata, контрольных событий, retry duplicates и producer logs добавляется отдельно. Backup, временная конвертация и burst/spool требуют отдельного запаса диска. При шестичасовом spool для планового raw-потока нужно около 1,30 GB только на raw; при высоком — 6,48 GB. Средняя скорость не заменяет нагрузочный тест p99 bursts.

### Сравнение

| Вариант | Что подходит | Ограничение / цена эксплуатации | Решение для MVP |
|---|---|---|---|
| SQLite | существующий reference/dashboard, локальные тесты | один writer даже с WAL; текущая БД использует DELETE journal, online analytics конкурирует с записью | сохранить существующую БД; не добавлять в неё бесконечный raw L2 |
| PostgreSQL | metadata, mappings, constraints, manifests, quality, opportunities; короткий hot event buffer | нужно обслуживать сервер, retention/partitioning и backup; JSON+индексы увеличивают объём | рекомендуемый новый operational store |
| PostgreSQL + TimescaleDB | time-series chunks, retention, гибридное row/column хранение | дополнительное extension, версии, обслуживание и проверка совместимости | отложить до подтверждённой потребности |
| ClickHouse | аналитические scan/агрегации больших append-only массивов | отдельный движок; batch ingestion, part/merge management; metadata удобнее оставить в transactional store | отложить до измеренного узкого места аналитики |
| Compressed Parquet | долговременный raw/replay/research архив, эффективное чтение колонок | формат файлов, не transactional DB и не live queue; нужны manifests, атомарная публикация, обработка неполных файлов | рекомендуемый cold archive |

Основания: [SQLite WAL и единственный writer](https://www.sqlite.org/wal.html), [PostgreSQL partitioning](https://www.postgresql.org/docs/current/ddl-partitioning.html), [Timescale architecture](https://assets.tigerdata.com/docs/downloads/Timescale_Architecture_for_Real-time_Analytics.pdf), [ClickHouse insert strategy](https://clickhouse.com/docs/concepts/best-practices/selecting-an-insert-strategy), [Apache Parquet overview](https://parquet.apache.org/docs/overview/). Это свойства технологий; выбор архитектуры ниже — инженерная рекомендация, не benchmark этого проекта.

Нельзя утверждать, что SQLite не выдержит 60 событий/сек вообще: batching и диск могут обеспечить такую запись. Проблема — непрерывное накопление миллионов raw/level rows, общий writer с действующим MVP, воспроизводимый replay, retention и параллельная аналитика. Только смена journal mode не решает весь набор задач.

**Рекомендация:** существующая SQLite остаётся на месте. Для нового слоя — отдельный PostgreSQL для небольших operational/derived данных и append-only raw segments с переводом в Parquet+ZSTD. Kafka, Redis, TimescaleDB и ClickHouse на первом MVP не нужны. Более простой краткий probe может писать только локальные raw segments, но полноценному cross-venue слою потребуется operational metadata store.

Не выполнять слепую двойную запись raw в два независимых хранилища. Первичен восстановимый raw journal с локальным offset; materializers записывают в PostgreSQL idempotently. Финализация Parquet через временный файл, проверку, atomic rename и manifest с checksum/границами offsets. Удаление spool — только после подтверждённого архивирования и checkpoint. Несовпадение состояния manifest и файла после crash должно исправляться reconciliation.

## E. Предлагаемая минимальная архитектура

Это эскиз для следующей фазы, а не реализованная canonical schema.

```text
Существующий слой:
Binance / Chainlink -> существующие SQLite tables -> существующие API / dashboard
                              |
                   read-only reference access
                              |
Новый слой:                   v
Venue adapters -> raw journal -> normalizer -> валидные книги в памяти
                     |              |                    |
              Parquet archive       v                    v
                              metadata / matcher -> observer
                                     |                    |
                                     +---- PostgreSQL ----+
                                              |
                                  отдельные prediction API / UI
```

- Один новый async collector-процесс на MVP, независимые connection tasks по venues и bounded queues. Reference worker остаётся отдельным процессом. Сетевой приём не должен ждать аналитического запроса или пересчёта свечей.
- Общий adapter boundary: `discover_markets`, `subscribe_market`, `get_snapshot`, `process_book_event`, `process_trade`, `normalize_market`; capabilities фиксируют наличие L2/trades/sequence/exchange timestamp. Unsupported capability возвращает явное отсутствие поддержки.
- Общие логические сущности: `prediction_venues`, `prediction_markets`, `prediction_tokens`, `prediction_book_events`, snapshots/levels, `prediction_trades`, `canonical_markets`, versioned match decisions, collection sessions и quality events. Физическое размещение raw — архив, а не обязательная бесконечная SQL-таблица.
- Envelope: venue/market/token, event type, nullable exchange timestamp, UTC received timestamp с достаточной точностью, session ID, local arrival ordinal, nullable venue sequence, schema version и ссылка на исходные bytes. Local ordinal не заменяет sequence venue и не доказывает отсутствие сетевой потери.
- Preserve arrival order per connection; snapshot/delta bootstrap только по проверенному протоколу. После reconnect или возможного desync книга получает INVALID до успешной синхронизации. Не придумывать checksum/sequence, если площадка их не предоставляет.
- Scanner работает по каждому принятому событию валидной книги. Checkpoints, SQL batching и UI refresh имеют отдельную частоту; их нельзя использовать как частоту измерения lifetime.
- Overflow, потеря raw persistence, stale и desync явно делают соответствующий интервал непригодным для исследования. Не silently drop и не forward-fill.
- Canonical window ID — ключ временного окна, а не доказательство эквивалентности. Нужны нормализованные settlement fields, исходные правила, provenance, версии и причины EXACT/NEAR_EXACT/NOT_EQUIVALENT. Неизвестные критичные поля исключают EXACT; попарная совместимость важнее общего ярлыка.
- DexSport без подтверждённого CLOB не получает выдуманные bids/asks/depth. Его реальное состояние может отображаться и архивироваться отдельно; depth-based observer допускает venue лишь при сопоставимом payout и достаточной информации об исполнимой цене/объёме.
- Reference join использует доступные на момент решения данные (`received_timestamp <= decision_time`), возраст reference и качество strike. Не выбирать задним числом более близкий будущий tick.
- SQL prices/sizes должны сохранять точные значения по Decimal/целым scale; не переносить молча SQLite NUMERIC-поведение на финансовые расчёты.

## F. План файлов

В Phase 1 добавлен этот отчёт и обновлены только проектные Markdown-статусы. Ниже — предложение будущих файлов; создавать их сейчас не требуется.

| Фаза | Новые файлы/модули | Существующие файлы для ограниченных изменений |
|---|---|---|
| 2 | `services/api/app/prediction/domain.py`, `schemas.py`, `db/models.py`, `db/session.py`, отдельная prediction Alembic configuration/migrations; `docs/prediction-data-contract.md` | `app/core/config.py`, `.env.example`, requirements для явно выбранного storage; `ARCHITECTURE.md` |
| 3 | reference health/checkpoint tests; согласованная additive reference migration | `app/workers/realtime_worker.py`, `app/feeds/chainlink_streams.py`, `app/candles/realtime_storage.py`, `app/db/models.py`, `app/api/routes/status.py`, Windows/systemd scripts |
| 4–5 | `app/prediction/adapters/base.py`, `polymarket.py`, `limitless.py`; replay/protocol fixtures и tests | регистрация settings; новая worker CLI |
| 6 | `docs/dexsport-reconnaissance.md`; `adapters/dexsport.py` только после подтверждения источника/механизма | capability mapping |
| 7 | `app/prediction/matching.py`, tests settlement rules | prediction models/metadata versioning |
| 8 | `app/prediction/books.py`, `raw_store.py`, `replay.py`, `collector.py`; `app/scripts/collect_prediction.py`; отдельный systemd unit | deployment runbook, environment example |
| 9–10 | `app/prediction/observer.py`, `opportunities.py`, tests VWAP/lifetime/as-of | prediction storage и read queries |
| 11 | `app/api/routes/prediction.py`, `apps/web/app/prediction/page.tsx`, `components/prediction/*`, `lib/prediction-api.ts` | `app/main.py` для нового router; существующий dashboard сохранить |
| 12 | `app/prediction/quality.py`, `app/scripts/report_prediction_quality.py`, quality tests | prediction status/UI и ops monitoring |
| 13 | `docs/prediction-research-protocol.md` | README, roadmap, backlog, changelog |

Не дробить пустые файлы ради структуры: модули появляются по мере реализации фазы. Отдельные prediction migrations не должны запускаться против существующей SQLite. Единственная возможная migration существующего слоя — отдельно проверенная additive работа Phase 3.

## G. Дорожная карта и зависимости

| Фаза | Зависимости | Проверяемый результат / критерий перехода |
|---|---|---|
| 2. Canonical model | подтверждение после этого аудита | общий контракт, физическая storage strategy, nullable timestamps, точность чисел, provenance и test fixtures; отсутствие смешения с candles |
| 3. Chainlink reference | Phase 2 для reference contract | устранён сбой автозапуска; контролируемый restart; BTC freshness, gaps/heartbeat/errors; как минимум 24h наблюдения с отчётом и без искусственных ticks |
| 4. Polymarket | 2 + стабильный 3 | заново проверены официальные endpoints/protocol/rate limits; discovery ротации 5m, snapshot+updates, reconnect и replay tests |
| 5. Limitless | общий контракт 2 и опыт 4 | проверен реальный BTC 5m market, outcomes/oracle/strike; общий adapter interface и protocol tests |
| 6. DexSport reconnaissance | 2; до включения DexSport в 7–9 | источники frontend/официальные документы/публичные контракты, механизм выплат и capabilities доказаны; adapter только после исследования |
| 7. Matcher | metadata/rules из 4–6 | решения с причинами и версиями; тесты `>`/`>=`, strike, oracle/feed, округления, отмены и fallback; UNKNOWN не становится EXACT |
| 8. Синхронный сбор BTC 5m | 3–7 для подключаемых venues | непрерывный raw/replay архив, вращение markets, UTC timestamps, boot/reconnect/desync tests; DexSport только по фактическим capabilities |
| 9. Observer | 7–8 | обе стороны каждой допущенной пары, только EXACT и fresh books; VWAP для 10/25/50/100/250/500/1000 shares; insufficient depth исключается |
| 10. Opportunity dataset | 9 | start/end/duration, edge quantiles, размеры, PnL и reference features; ценовые/lifetime buckets; censored periods при gap/stale |
| 11. Минимальный UI | 9–10 | отдельный BTC 5m экран, bid/ask/age/depth/matching quality, состояния отсутствия данных; отсутствие зависимости scanner от UI |
| 12. Quality report | базовые метрики с 3–4, итог после 8–11 | uptime, messages/hour, gaps/reconnects/stale/malformed, unmatched, missing L2, latency, disk growth, duplicates и archival lag |
| 13. Research only | ограничения с самого начала | воспроизводимый dataset и исследовательский протокол; никаких orders, wallets, private keys или auto execution |

Если DexSport недоступен или несовместим, не блокировать честный сбор Polymarket/Limitless и не объявлять трёхстороннее покрытие. Если нет EXACT pairs, корректный результат scanner — отсутствие подходящих пар, а не искусственная возможность.

Quality нельзя откладывать целиком до Phase 12: без неё уже Phase 8 будет накапливать неразличимые валидные и повреждённые интервалы. Phase 12 оформляет полный отчёт, а базовые контрольные события собираются с первого adapter.

## H. Риски и неизвестные

1. **Settlement equivalence:** одинаковые underlying/window/название Chainlink недостаточны. Нужны strike, конкретный feed, выбираемый report на границе, правило равенства, precision/rounding, cancellation/refund/fallback, dispute/finality и одинаковая единица payout. Сейчас EXACT между любыми venues не доказан.
2. **DexSport:** наличие BTC 5m продукта, CLOB/L2, источник ликвидности, публичные quotes/trades/history, auth, rate limits и правила программного доступа не установлены. Подробный frontend/network/contract аудит относится к Phase 6 и здесь не подменяется догадками об API.
3. **Polymarket/Limitless:** ссылки в старом review не подтверждают актуальные schemas, доступность BTC 5m, публичность trades или rate limits. Перед adapter нужна новая проверка официальной документации и read-only samples.
4. **Временная точность:** observation precision, receive jitter, clock sync и порядок между разными venue connections ограничивают интерпретацию lifetime <25 ms. Нужен монотонный локальный timer для длительности плюс UTC для хранения; microsecond поле само по себе не доказывает microsecond accuracy.
5. **Недостающая история:** из существующей БД нельзя восстановить старые L2 и периоды отсутствия Chainlink. Для strike при недоступном точном reference сохранять неизвестность.
6. **Исполнимость:** публичный book и VWAP показывают наблюдаемую depth, но не гарантируют одновременное исполнение двух ног. Fees, payout conversion, min size, price increments, latency и изменение книги могут убрать gross edge. Dataset должен различать gross theoretical PnL и фактически исполненный результат; торгового результата пока нет.
7. **Механизм payout:** при нефиксированном/несопоставимом payout нельзя применять `1 - (YES ask + NO ask)` напрямую. Запрещено создавать synthetic L2 для иной модели рынка.
8. **Persistence:** compression/GB estimates требуют пилотного raw sample и burst test. Возможны потеря неперсистированных сообщений, queue overflow, disk full и незавершённые segments; интервалы потери должны быть видимыми.
9. **Действующий проект:** нельзя добавлять L2 writer и тяжёлые аналитические scans в существующую SQLite без изоляции; расчёты текущих imbalance/PnL не меняются в рамках нового слоя.
10. **Production доступ:** VPS недоступен из текущего окружения; systemd deployment, logs, backup timer, capacity и clock sync на нём остаются непроверенными. Локальный reboot доказан; root cause сбоя повторного запуска пока нет.

## Как воспроизвести ключевые проверки

Из `services/api`: `python -m unittest discover -s tests`. Из `apps/web`: `npm run lint`, `npm run test`, `npm run build`. Для Ruff сначала требуется существующая dev-зависимость из `requirements-dev.txt`; в ходе аудита окружение не менялось.

Для DB использовать только read-only connection:

```python
import sqlite3
from pathlib import Path

path = Path("poly_crypto.db").resolve()
db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
db.execute("PRAGMA query_only=ON")
print(db.execute("PRAGMA quick_check").fetchall())
print(db.execute("""
SELECT a.symbol, p.source, COUNT(*), MIN(p.timestamp), MAX(p.timestamp)
FROM price_ticks p JOIN assets a ON a.id = p.asset_id
GROUP BY a.symbol, p.source
""").fetchall())
print(db.execute("""
WITH ordered AS (
  SELECT timestamp,
         LAG(timestamp) OVER (ORDER BY timestamp) AS previous_timestamp
  FROM price_ticks
  WHERE asset_id = (SELECT id FROM assets WHERE symbol = 'BTC')
    AND source = 'chainlink_streams'
)
SELECT previous_timestamp, timestamp,
       unixepoch(timestamp) - unixepoch(previous_timestamp) AS gap_seconds
FROM ordered WHERE previous_timestamp IS NOT NULL
ORDER BY gap_seconds DESC LIMIT 10
""").fetchall())
db.close()
```

SQL `unixepoch` здесь допустим для нынешних секундных observations; для будущих subsecond событий использовать точный integer epoch с нужной единицей. Worker `--once` не является read-only проверкой: он запускает repair и запись, поэтому в Phase 1 не использовался.

Результат Phase 1: аудит завершён; ограничения доступа и неизвестная причина неудачного автозапуска явно сохранены. Остановиться здесь. Phase 2 начинается только после явного подтверждения владельца.
