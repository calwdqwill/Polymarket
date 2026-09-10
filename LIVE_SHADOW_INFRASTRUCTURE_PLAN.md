# LIVE_SHADOW_INFRASTRUCTURE_V1 — аудит и план переноса

Дата: 10 сентября 2026. Выполнена **Iteration 1 / Phase 1**. Решение: подготовить отдельный Linux-контур; текущий collector ещё не готов к подтверждённому режиму 24/7. Этот документ не разрешает запуск следующей фазы. На сервер ничего не установлено, новый collector/shadow не запущен, торговая логика не изменена.

## A. Текущее состояние

### Основание и восстановленная хронология

Приоритет: текущий код и машинные результаты, затем соответствующий итоговый отчёт, затем исторические планы. Пути ниже относительно корня проекта.

| Этап | Проверенный источник | Результат и ограничения |
|---|---|---|
| Первый инфраструктурный аудит | `docs/prediction-cross-venue-audit.md` | Существовал candle MVP, prediction-слоя ещё не было; это историческое состояние |
| Первый BTC 5m probe | `docs/prediction-first-probe.md`; `services/api/data/prediction/20260909-final-probe/` | Discovery, matcher, расчётное ядро; UNKNOWN блокировал тогдашний L2 запуск |
| Live L2 | `docs/prediction-live-report.md`; `services/api/data/prediction/live-20260909T141038Z-080c6260/` | Три полных окна, WS обеих площадок; старый формат полных normalized snapshots |
| Стабилизация | `docs/prediction-stabilization-report.md`; `services/api/data/prediction/stabilization-diagnostic/REPORT.md` | Schema v2, 60s gzip, checkpoints; короткий sample с 99,219% coverage не доказывает 24/7 |
| Finalizer | `docs/prediction-finalization-fix.md`, `docs/prediction-collector-runbook.md` | Барьер завершения процесса, закрытые сегменты, lock и I/O retry |
| Recovery | `docs/prediction-recovery.md`; `services/api/data/prediction/recovery-20260910/recovery-report.md` | Прерывание при Windows Update, оригинал сохранён, хвост отделён |
| Итоговый многочасовой research | `services/api/data/prediction/recovery-20260910/REPORT.md`, `analysis.json`, `quality.json`, `replay.json` | 124 календарно полных окна; 122 пригодных, 10 ч 10 мин; MORE DATA REQUIRED |
| Execution Simulator V1 | `execution-sim-v1/REPORT.md`, `summary.json`, `validation.json` | 880 конфигураций, 1 150 363 записи основной симуляции; CONTINUE RESEARCH |
| Target Profit V1 | `target-profit-research-v1/REPORT.md`, `summary.json`, `selected_validation.json` | 1 260 конфигураций, 226 860 попыток; 24 подтверждающих конфигурации; рекомендация PROCEED TO LIVE SHADOW |
| Settlement / fees | `docs/prediction-settlement-update.md`, `target-profit-research-v1/FEE_RESEARCH.md`, каталог `target-profit-research-v1/sources/` | UNKNOWN / FEE_UNKNOWN; сохранённые исследования не являются новой live-проверкой условий площадок |

Git: HEAD `a3d74b2` — `Document VPS deployment`; предыдущий `f8ef96c` — `Prepare poly crypto MVP v1.0`. Prediction-related commits отсутствуют. Перед аудитом изменены `.gitignore`, пять основных документов и `services/api/requirements.txt`; prediction modules, scripts, tests, отчёты и `AGENTS.md` находятся среди untracked. Обычный clone HEAD потеряет всю prediction-реализацию. Существующие изменения сохранены; commit/push не выполнялись.

### Dataset и актуальные артефакты

Оригинал: `services/api/data/prediction/research-20260909T151749Z/` — **9 044 файла, 1 284 909 762 байта** по текущей инвентаризации. Recovery bundle: `services/api/data/prediction/recovery-20260910/` — **9 120 файлов, 1 292 546 046 байт**, включая evidence и отчёты. Закрытые сегменты находятся в `recovery-20260910/dataset/`; восстановленные хвосты — отдельно в `repaired-tail/`.

Закрытый интервал: 2026-09-09 15:17:49.582026 — 2026-09-10 01:40:48.693299 UTC. Последний включённый рынок заканчивается в 01:40 UTC. Планировалось 12 часов; фактически пригодны 122 окна / 10,1667 часа. 623 закрытых сегмента; восемь незавершённых gzip-файлов сегмента 623 не входят в основной анализ. Protocol-небезопасные окна `1788976500`, `1788976800` исключены.

Сохранённый replay: 6 917 584 Polymarket frames / 29 650 checkpoints и 157 659 Limitless frames / 4 168 checkpoints, mismatch=0. Дополнительная проверка: 80 327 355 ценовых строк. Это результаты существующих verifier-артефактов, прочитанных в аудите; полный многочасовой replay и повтор всех SHA-256 в этой итерации не запускались.

| Запрошенный файл | Фактическое расположение / проверка |
|---|---|
| `REPORT.md` | Итог recovery, execution и target-profit находятся в соответствующих каталогах выше |
| `summary.json` | Есть в `execution-sim-v1/` и `target-profit-research-v1/`; у корня recovery его нет, итог представлен `analysis.json`/`quality.json` |
| `quality.json`, `analysis.json` | Корень `recovery-20260910/`; внутри `dataset/` также имеются производные результаты, их scope отличается |
| `replay.json` | Корень recovery содержит дополнительно observations; `dataset/replay.json` — checkpoint replay |
| `window_results.csv` | `target-profit-research-v1/`, 153 720 строк = 1 260 × 122 |
| `target_matrix.csv` | Там же, 1 260 строк |
| `hourly_results.csv` | Там же, 13 860 строк; неполный час не приравнивать к полному |
| `failures.csv` | В обоих research-каталогах; target-profit — 5 527 строк |
| `robustness.csv` | В обоих research-каталогах; target-profit — 2 520 строк |
| `selected_robustness.csv` | `target-profit-research-v1/`, 72 строки |

CSV открыты и пересчитаны как таблицы; это проверка структуры и наличия, а не повтор финансовой верификации всех строк.

### Стратегия и противоречия

Кандидат из `selected_shadow_profile`: Q10, target $0,50, `0.10 < edge <= 0.15`, TTE>30s, conservative fee, friction 0.0025, crossing, максимум три попытки. Исторически около 68% успешных окон и условные +$3,49/ч при 100ms / +$2,65/ч при 250ms. Это in-sample simulated PnL, не realized PnL. `shadow_started=false`.

В `target_profit.gross_request` Q — целевой удержанный размер; gross-заявка Limitless увеличивается для удержания комиссии. При 3% Q10 требует примерно 10,309279 gross shares. `quote` считает VWAP по этим запросам, поэтому его edge не всегда равен обычному gross Q10 VWAP observer. Capture в историческом коде требует full fills, residual≤0,01, net последней попытки≥target **и накопленный net окна≥target**. Успешная последняя попытка сама по себе не компенсирует предыдущие потери. Это существенные неоднозначности будущего frozen profile: до Phase 4 зафиксировать точную семантику Q, signal edge, rounding, capture, повторного crossing, времени ACK и общего лимита попыток A/B. Не менять историческую модель под новую трактовку молча. Два latency-сценария должны иметь независимые ledger/attempt/capture state.

Другие расхождения:

- Старые документы говорят о продолжающемся collector/PID и 12h запуске; актуальны recovery и завершение при reboot. При проверке процессов аудит не обнаружил `collect_prediction`, finalizer или target/shadow runner.
- 99,219% короткого sample уступает приоритетом **88,1558%** основной многочасовой выборки. `analysis.cross_venue_valid_coverage` содержит иной, номинальный scope; для стратегии использовать `strategy_cross_venue_coverage`.
- UNKNOWN больше не блокирует публичный research. Он продолжает блокировать утверждения о гарантированной общей выплате.
- Старый VPS `155.212.183.185`, порты и работающие сервисы описаны в `docs/deployment-runbook.md`, но в этом аудите сервер не подключался. Его актуальное состояние и выбор целевого хоста неизвестны.
- В master plan есть группировка Phase 2–3 в Iteration 2 и одновременно STOP после каждой фазы. Применяется более строгая граница: Phase 2 → отчёт/STOP → отдельное подтверждение → Phase 3. Объём обеих частей описан в N.

### Проверки этой итерации

- Backend: `.venv/Scripts/python.exe -m unittest discover -s tests` — **120/120**, 15,982s; локальные synthetic fixtures и тестовые сетевые сценарии, без нового live dataset.
- Prediction Ruff: `app/prediction`, prediction/target-profit scripts и tests — **PASS**.
- Общий Ruff `app tests` — **FAIL**, семь I001 в старом MVP: `app/db/migrations/env.py`, `app/db/models.py`, `app/feeds/binance.py`, `app/scripts/poll_chainlink_streams.py`, `app/scripts/validate_candles.py`, `app/workers/realtime_worker.py`, `tests/test_api_routes.py`. Автоисправление вне scope аудита.
- Frontend `npm run test` — **PASS** (`tsc --noEmit`, не unit suite); `npm run lint` — **PASS**.
- Linux startup/reboot, нагрузочный тест VPS, restore внешнего backup и production build на Linux ещё не проверены. Windows-тесты не заменяют эти проверки.

## B. Повторно используемые компоненты

Все пути модулей далее начинаются с `services/api/app/`.

| Компонент | Код | Что переиспользовать |
|---|---|---|
| CLI collector | `scripts/collect_prediction.py` | Discovery каждые 10s плюс время запросов, current+next, ротация, supervision tasks, STOP, live view |
| Metadata и matcher | `prediction/discovery.py`, `models.py` | BTC/5m/slug validation, outcome mapping, settlement evidence, NOT_EQUIVALENT gate |
| Adapters | `prediction/books.py` | Poly snapshots/absolute deltas/BBO checks; Limitless full YES и зеркальный NO |
| Transport | `prediction/live_transport.py` | Public WS, Engine.IO, raw перед parsing/apply, connection/session, reconnect 2–15s, checkpoints |
| Observer | `prediction/observer.py` | Decimal VWAP, A/B, размеры 10–1000, quality и episodes; не live trade engine |
| Storage | `prediction/storage.py`, `live_storage.py` | JSON Decimal-строками, gzip level 1, 60s segments, atomic views |
| Replay/recovery | `scripts/replay_prediction.py`, `verify_prediction_observations.py`, `freeze_prediction.py`, `recover_prediction.py` | Воспроизводимость, отдельные копии, corruption/exclusion evidence |
| Finalizer | `scripts/finish_prediction_research.py`, `prediction_finalization_io.py` | Lock, validation → replay → analysis → report, ошибки с traceback |
| Аналитика | `scripts/analyze_prediction*.py`, `prediction/research_statistics.py` | Duration-weighted coverage, цензура, disk SQLite temporary cache; основная БД не используется |
| Offline fills/fees | `prediction/execution_simulator.py`, `execution_fees.py`, `target_profit.py` | Чистые расчёты fill/accounting после фиксации семантики; не переносить offline scanner целиком в daemon |
| Offline timeline | `prediction/execution_replay.py`, `target_replay.py` | Причинный порядок и проверки; multi-session timeline требует явного выравнивания |
| Представление | `prediction/live_screen.py`, `screen.py` | Локальные HTML/JSON; в Next.js prediction-раздела нет |

FastAPI/SQLAlchemy/Alembic и Next.js/React/TypeScript остаются стеком. `apps/web/app/page.tsx` — candle dashboard. `app/api/routes/status.py` не знает prediction; `/api/status` — статическая liveness-сводка, `/api/status/sources` относится к старым источникам. Prediction API/operational tables/shadow journal отсутствуют. Есть Linux example units для старых API/web/Chainlink, но нет prediction units. Runtime requirements содержат `websockets==16.0`; Ruff настроен на Python 3.12; frontend воспроизводить через существующий `package-lock.json`.

## C. Текущие слабые места

| Приоритет | Факт из кода/артефактов | Минимальное последующее действие |
|---|---|---|
| P0 | Invalidation в `stream_market.finally` выполняется после выхода из `async with`; close может ждать. Recovery зафиксировал ~3,08s ложного VALID | Инвалидировать до первого await закрытия при ошибке, синхронно сообщать observer; тест медленного close |
| P0 | 88,16% valid, Poly DESYNC 9,67%; 417 Poly reconnects / 5 Limitless | Разобрать backlog и причины, сохранить BBO safety; >95% пока не достигнуто |
| P0 | `LiveJournal.metrics()` каждую секунду сортирует bins от начала сессии и делает glob/stat по всем stream files | Инкрементальные bytes/counts, bounded rolling bins, статистика старых сегментов в manifest; benchmark роста 1/10/24h |
| P0 | Gzip/JSON, observer и file I/O синхронны в event loop; `max_queue=4096`, прямой queue telemetry нет | Профилирование, bounded writer queue с byte limit и явной политикой overflow; raw принят writer до применения, никаких silent drops |
| P0 | Нет SIGTERM handler, boot identity, единственного daemon lock и continuous режима; существующий output отвергается | Graceful SIGTERM/SIGINT, новый каталог на каждый старт, долгоживущий lifecycle, lock; не имитировать 24/7 ежедневным обрывом |
| P0 | `flush()` не делает fsync; `segments.json` означает закрытие gzip, не доказанную power-loss durability | Закрытие → fsync data → atomic manifest + fsync directory на Linux; отдельный durable watermark |
| P0 | Binary WS frame отклоняется до записи raw; receive time отмечается после `ws.recv`, а не у сетевого сокета | Сохранение непредвиденного transport frame до отказа; явно считать receive-time proxy и измерять backlog |
| P1 | Linux finalizer использует только `os.kill(pid,0)`, без проверки PID reuse/boot | Run identity = session + boot ID + process start; PID сам по себе не разрешает анализ |
| P1 | Finalizer ждёт терминальную сессию, replay равенство не включает отдельный protocol exclusion gate автоматически | Очередь только закрытых partitions, protocol verifier обязателен, idempotent статус; не запускать finalizer на live-каталоге |
| P1 | Reports recovery имеют привязку к историческому incident; RawTimeline отвергает несколько clock sessions | Отдельный общий crash report; manifest с boundaries/gaps, per-session replay, без склейки monotonic clocks |
| P1 | Нет ресурсных/queue metrics, external watchdog, off-host backup | Добавить узкий health endpoint и независимый monitor до длительного сбора |

В `quality.json` записано 22 219 событий timer lag>100ms: median 1335ms, p95 2805ms, max 3802ms. Это распределение только превышений, не всех событий и не network latency. Причинная связь с `metrics()` пока гипотеза, проверяемая профилированием. Даже 32 logical CPUs локальной машины не устраняют блокировку одного event loop.

## D. Требования к VPS

**Предварительный минимальный кандидат: 4 vCPU, 8 GB RAM, 100 GB SSD для ограниченной технической проверки. Рабочий план: 4 vCPU, 8 GB RAM, 160 GB SSD для семи суток с запасом на рост.** Это инженерный бюджет по сохранённому потоку, а не подтверждённая производительность конкретного VPS. Выбор провайдера/региона/оплата не выполнялись.

На локальном хосте 32 logical CPUs, 16,78 GB RAM; свободно около 62,85 GB на C: во время аудита. Исторические CPU/RSS collector не измерялись, текущий collector остановлен. Перенос этих характеристик на VPS как benchmark недопустим.

Наблюдаемый поток current+next: Poly в среднем 185 frames/s, p95 464 frames/s; Limitless 4,22 / 12; observations 172,39 / 446. Raw application payload ~131,6 kB/s, ~11,37 GB/сутки без TCP/TLS. Проверить пиковую пропускную способность и месячную квоту; среднее не задаёт upper bound. Выбирать регион после публичного connectivity/RTT теста обеих venues, без обещания соответствия 100ms matching latency.

Бюджет памяти для первого теста: collector+writer 2 GB, API/web 1,5 GB, ОС/cache 1,5 GB, reserve 3 GB; будущие shadow/DB займут часть reserve и требуют нового замера. Тяжёлые replay/grid/Next build не запускать одновременно со сбором на минимальном хосте. Swap не считать нормальным способом выдерживать live-нагрузку.

| Хранение | Наблюдаемая линейная проекция |
|---|---:|
| Все gzip/sidecars исходного dataset | 123,705 MB/час |
| 24 часа | 2,969 GB |
| 72 часа | 8,907 GB |
| 7 суток | 20,783 GB |
| 30 суток | 89,068 GB |
| 7 суток при условном 3× потоке | 62,347 GB |

Здесь GB=10^9 bytes. Формула планирования диска: `(20 GB ОС/releases + 10 GB operational/logs + 10 GB scratch + дни × 2,969 GB × коэффициент) / 0,8`. Семь суток при 3× требуют ~128 GB, округление до 160 GB. Для 30 суток при 3× — ~384 GB, ориентир 400 GB. На 100 GB после reserve и прочих 40 GB остаётся ~4,5 суток при 3×. Коэффициент 3 — запас, а не измеренный пик. Backup на том же диске в расчёт не включён; полный staging bundle требует дополнительного места.

Перед признанием sizing достаточным: измерить CPU/RSS, cgroup throttling/steal, дисковые write/fsync latency, свободные inode, throughput, event-loop и writer lag. Предварительный gate: host CPU<70% устойчиво, свободно ≥25% RAM, нет OOM/sustained swap, p99 event-loop lag<50ms и writer lag<50ms на всех событиях. Эти пороги — цель проверки для будущих 100ms сценариев, не текущий результат. При насыщении сначала устранять подтверждённые блокировки; дополнительные CPU не исправляют последовательный hot path.

## E. Целевая архитектура

Текущая схема: один bounded Python collector → gzip/JSON/HTML → offline finalizer/replay → offline simulator. Candle API/SQLite/Next.js существуют отдельно. Слабое место: нет непрерывного supervisor/lifecycle и operational read model; file-only отчёт не обслуживает историю trade/window с фильтрами.

Вариант 1: collector + сегменты + status API, позднее отдельный shadow-процесс и PostgreSQL. Минимум зависимостей сейчас, постепенное переиспользование функций. Вариант 2: брокер сообщений и отдельные ingestion/analytics services сразу. Даёт независимое масштабирование, но добавляет deployment/ordering/recovery complexity без подтверждённой необходимости. **Выбран вариант 1.** Архитектура в коде в Phase 1 не меняется.

```text
Linux VPS
  prediction-collector.service --> raw segments + metadata + checkpoints
              |                   durable manifest / run registry
              +--> live read model --> prediction-api.service
              |
              +--> будущий ordered local event channel --> prediction-shadow.service
                                                             |
                                                        operational DB
                                                             |
  Nginx (TLS / закрытый доступ) --> API + web-dashboard.service
  health-monitor.timer --> health/readiness/disk/backup status
  prediction-finalizer@.service --> только закрытые partitions / replay
  prediction-backup.timer --> внешний storage + checksum receipts
```

Phase 2–3: collector, health/status, raw, backup; shadow отсутствует. Phase 4–8: typed ordered local channel (например Unix socket), bounded buffer, ACK/watermark и reconnect протокол, затем shadow/DB. Не читать `live.json` раз в секунду или 60s закрытые gzip для симуляции arrival 100ms. Channel передаёт ordered event IDs и snapshots/invalidations; при gap shadow останавливает сигналы до восстановления. Consumer lag не сдвигает фактический arrival назад: delayed/degraded результат маркируется отдельно, не подменяется будущей книгой. Новый транспорт должен пройти причинный live/replay parity test до forward test.

На reboot pending hypothetical trades не исполняются задним числом: зафиксировать INTERRUPTED/причину, восстановить попытки и capture state из durable journal; свежие snapshots обязательны. Один experiment config неизменяем; фактический end timestamp дописывается отдельным lifecycle event, не переписыванием config. Параметры micro_arb_v0 в этой фазе не создаются и не меняются.

## F. Хранение

| Каталог назначения | Содержимое и права |
|---|---|
| `/opt/poly-crypto-prediction/releases/<release-id>/` | Код, Linux venv, frontend build; root-owned, service users без записи |
| `/opt/poly-crypto-prediction/current` | Symlink на проверенный release; rollback только кода |
| `/etc/poly-crypto-prediction/` | Env/config, root:service-group 0640; secrets отдельно |
| `/var/lib/poly-crypto-prediction/raw/<run-id>/` | Append-only 60s gzip; collector — единственный writer |
| `/var/lib/poly-crypto-prediction/state/` | Run registry, boot identity, heartbeats, атомарные read models |
| `/var/lib/poly-crypto-prediction/analysis/<run-id>/` | Производные replay/quality/report, заново воспроизводимы |
| `/var/lib/poly-crypto-prediction/quarantine/<run-id>/` | Manifest аварии и восстановленные копии; оригиналы не переписываются |
| `/var/lib/poly-crypto-prediction/backup-state/` | Upload receipts, hashes; не вторая полная копия raw |
| `/run/poly-crypto-prediction/` | Volatile locks/socket; после reboot пересоздаются |
| `/var/log/poly-crypto-prediction/` | Только при необходимости отдельных файлов; основной stdout/stderr → journald |

Каталог data вне release, OneDrive и Git. Полный старый dataset и старую SQLite на сервер по умолчанию не переносить. Для acceptance достаточно согласованного frozen fixture; полная исходная история остаётся отдельным архивом.

Raw сохраняет исходный frame, UTC receive, monotonic, session/connection/ordinal, market metadata с outcome mapping/sequence там, где они есть. Manifest должен содержать имя/bytes/SHA-256, first/last event IDs, schema/code/config hashes, факт durable close. Minute rotation не тождественна daily session restart; в Phase 3 нужны bounded partitions и начальные checkpoints для независимого replay, без намеренного разрыва соединений ради каталогов.

Operational layer сейчас — маленькие sidecar JSON, raw уже не находится в SQLite. Для Phase 8 предлагается отдельная PostgreSQL DB: несколько writers/readers, transactional ledger, уникальные ключи trade/window/event, индексы по timestamp, canonical_market, strategy_version, latency_scenario, status, direction. Raw deltas туда не складывать. До Phase 8 PostgreSQL/driver/migrations не устанавливать и основную SQLite schema не менять. API получает read-only role, shadow — узкую write role. Альтернатива SQLite для одного writer дешевле, но хуже соответствует независимым daemon/API и будущим concurrent journals.

Никакой автоматической очистки raw. Retention 7/30 суток — расчёт ёмкости, не разрешение удаления. При исчерпании места остановить сбор с явным DISK_PRESSURE и алертом; для продолжения увеличить диск или отдельно согласовать перенос/удаление проверенных копий.

## G. Сервисы

| Unit | Phase / назначение | Запуск и завершение |
|---|---|---|
| `prediction-collector.service` | 2: bounded staging; 3: continuous | Python collector, один owner lock; fresh run на restart; SIGTERM закрывает сегменты |
| `prediction-api.service` | 2: узкий read-only status; 8+: operational queries | Отдельный ASGI entrypoint, loopback 18010; не импортирует старую DB для статуса |
| `web-dashboard.service` | 2: существующий UI только при необходимости; 9+: Prediction Arb | Next production, loopback 13010; новый prediction UI пока отсутствует |
| `prediction-shadow.service` | Только 4–8 | Не устанавливать/enable как фиктивную заглушку в Phase 2 |
| `prediction-finalizer@.service` | 3 | Oneshot для closed partition; lock и verifier gate; CPU/I/O ниже collector |
| `health-monitor.service` + `.timer` | 2–3 | Oneshot каждые 30s, сохраняет health; внешний monitor проверяет недоступность всего VPS |
| `prediction-backup.service` + `.timer` | 2–3 | Каждые 5 минут только durable closed segments, без удаления |

Нужны отдельный Linux user `prediction` без интерактивного входа и отдельный read-only user/group для API. Старый `polycrypto` и его units не переиспользовать вслепую. Базовые настройки: `Type=exec`, `WorkingDirectory` на release, `UMask=0027`, `NoNewPrivileges=yes`, `PrivateTmp=yes`, `ProtectSystem=strict`, `ProtectHome=yes`, узкие `ReadWritePaths`. Collector получает network и только свои state/raw paths; API — чтение state. Проверить поддерживаемость директив на выбранном Linux. Семантика service и sandbox описана в официальных [systemd.service](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.service.xml) и [systemd.exec](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.exec.xml).

Для bounded Phase 2 — `Restart=on-failure`: нормальное окончание sample не запускает новый сбор. Для continuous Phase 3 — `Restart=always`, `RestartSec=10s`, лимит 5 стартов/300s; ручной `systemctl stop` остаётся остановкой. До enable добавить graceful SIGTERM и `TimeoutStopSec=60s`; forced kill оставляет quarantine, не STOPPED. Exit code для disk pressure должен запрещать автоматический restart через `RestartPreventExitStatus`; конкретное значение фиксируется вместе с реализацией. Не включать `WatchdogSec` без реализации heartbeat/notify.

Конкретная существующая команда для будущего bounded staging из release `services/api`:

```bash
.venv/bin/python -m app.scripts.collect_prediction --seconds 1800
```

Сегодня она пишет относительный `data/prediction`, поэтому **не является готовым ExecStart**. В Phase 2 launcher должен создать уникальный путь persistent data и передать существующий `--output`. Он также связывает PID/session и STOP; не использовать постоянный `--output`, который упадёт при втором старте. Continuous flag и server wrapper ещё предстоит реализовать; не выдавать их за существующие команды.

## H. Сеть, окружение и доступ

| Порт | Назначение |
|---|---|
| 22/tcp | SSH только с административных IP/VPN, key auth |
| 443/tcp | Будущий HTTPS dashboard/API с ограничением доступа |
| 80/tcp | Только если нужен ACME/redirect; на существующем VPS не занимать без проверки |
| 127.0.0.1:18010 | Prediction API, наружу закрыт |
| 127.0.0.1:13010 | Web, наружу закрыт |
| 5432 | Только локальный/приватный PostgreSQL в Phase 8 |

На Phase 2 допустим SSH tunnel без публичного сайта и без покупки домена. Старые 18000/13000/8080 и порт 8000 не менять. Перед назначением портов выполнить `ss -lntup`, инвентаризацию units и Nginx; схема старого runbook может устареть.

Исходящие public endpoints из кода: `gamma-api.polymarket.com`, `ws-subscriptions-clob.polymarket.com`, `api.limitless.exchange`, `ws.limitless.exchange`, HTTPS/WSS 443; DNS и выбранный NTP source также нужны. Позднее backup endpoint. Не фиксировать CDN IP как постоянные адреса; сохранять TLS verification и retry/429 handling. HTTP probing из будущего VPS обязателен из-за региональных ограничений, но не является отправкой orders.

| Переменная | Статус и использование |
|---|---|
| `PYTHONUNBUFFERED=1`, `PYTHONUTF8=1` | Стандартные runtime settings, journald/UTF-8 |
| `PREDICTION_DATA_DIR`, `PREDICTION_STATE_DIR` | **Предлагаемые**, wrapper ещё должен их прочитать и проверить абсолютный путь |
| `PREDICTION_RUN_SECONDS=1800` | **Предлагаемая** настройка bounded Phase 2; без скрытого continuous default |
| `PREDICTION_API_HOST=127.0.0.1`, `PREDICTION_API_PORT=18010` | **Предлагаемые**, новый status entrypoint |
| `PREDICTION_MIN_FREE_BYTES` | **Предлагаемая**, сейчас код использует hardcoded 2 GiB; новый threshold по разделу I |
| `NEXT_PUBLIC_API_BASE_URL` | Существующая build-time настройка Next.js; не secret. Старому dashboard нужен старый candle API, prediction status не заменяет его |
| `DATABASE_URL` | Существующая для candle MVP; collector её не требует |
| `PREDICTION_DATABASE_URL` | Только будущая Phase 8; отдельная DB |
| Backup credentials | Только backup service, отдельный защищённый файл/credential; collector не получает их |

В локальном `.env` есть заполненные Chainlink credentials; проверено только наличие значений, они не выводились. Git не отслеживает `.env`. Копировать весь `.env` на VPS нельзя по принципу минимального доступа: публичному prediction collector Chainlink keys, wallets и signing не нужны. В env example не добавлять флаги реальной торговли или private keys.

## I. Мониторинг

`/healthz` — процесс API отвечает; `/readyz` — текущая сессия collector, свежий heartbeat, запись data и обе книги пригодны; при деградации 503 с причинами. `/api/prediction/status` — read-only состояние, session/boot/release/config IDs, last raw/processed/durable ordinal, market/window и quality. Это предлагаемые новые endpoints; старый `/api/status` readiness не доказывает.

| Метрика | Предварительная реакция |
|---|---|
| Heartbeat collector старше 10s | Warning; >30s critical и внешний алерт |
| Current/next discovery, snapshot deadline | MISSING/RECOVERING; не показывать старое окно как текущее |
| Book age и connection age по venue/outcome | Отдельные значения; TTL Poly30s/LL60s и LL resubscribe30s не ослаблять |
| Rolling coverage 1h и 24h | <95% warning; публиковать все MISSING/STALE/DESYNC и причины |
| Event-loop lag, queue items/bytes, oldest queued age, processing/durable lag | Все события, p50/p95/p99/max; перегрузка немедленно закрывает readiness |
| Reconnects, schema errors, protocol invalidations, restart count | Алерт на рост/серии, без скрытого restart loop |
| Disk и inode | Warning при ≥70%; critical ≥80% или runway<48h |
| Free bytes | Controlled stop при остатке <max(10 GiB, прогноз 12h записи); это будущий threshold, сейчас 2 GiB |
| CPU/RSS/steal, OOM, swap, fsync duration | Проверять sizing; рост памяти по часам, не только стартовый RSS |
| NTP offset/sync, UTC jumps | При offset>50ms warning, >100ms/untrusted clock degraded; monotonic timing и новый anchor, не переписывать историю |
| Backup receipt age, backlog bytes, last restore | Нет успешной копии >15min — critical; размер очереди входит в disk runway |

Runway вычислять по фактическому росту файлов за последние часы, а не только по 3 GB/day. Системные логи: journald persistent, бюджет 1 GB и 14 суток; ротация отдельных файлов при необходимости. Это не retention raw. Независимый внешний probe нужен для power/network outage — локальный timer сам об этом не сообщит. Канал уведомлений выбрать до enable; сообщения третьим лицам в этой итерации не отправлялись.

## J. Резервирование и восстановление

Raw: каждые 5 минут выгружать закрытые durable segments с manifest и SHA-256 на отдельный storage; подтвердить checksum/receipt. Не копировать активный gzip как готовый объект, не выполнять destructive sync. Защитить внешние объекты от перезаписи; audit identity и права удаления отделить от uploader. Config/releases/tests также архивировать без секретов; секреты резервировать отдельно с шифрованием.

Плановые цели, ещё не достигнутые: off-host raw RPO≤6min (60s segment + 5min interval при исправном uploader); RTO≤60min для возобновления публичного сбора на подготовленном host. Полный historical replay не входит в этот RTO. До fsync нет обещания локального RPO=60s при power loss. При длительной недоступности backup RPO растёт и показывается явно.

Для будущего PostgreSQL начать с ежедневного logical dump и проверки restore (RPO до 24h); если journal должен иметь меньший RPO, до forward test добавить base backup + WAL/PITR. Raw сам по себе не восстанавливает точно потерянные live scheduling decisions. Эти разные варианты резервирования разделены в официальной [документации PostgreSQL](https://www.postgresql.org/docs/current/backup.html). Старую SQLite, если её контур отдельно сохраняется, резервировать SQLite backup API, не копированием открытого файла; prediction-перенос её не затрагивает.

Порядок восстановления:

1. Установить отсутствие writer через lock/boot/process identity. Зафиксировать crash metadata, сохранить исходные bytes/hash; не доверять старому LIVE или повторно использованному PID.
2. Проверить closed manifest, gzip/JSONL/ordinal boundaries. Активный tail оставить quarantine, salvage только в отдельную копию с объяснением исключений.
3. Запустить новую сессию с новым ID, свежими snapshots и gap до первого VALID; старые книги/monotonic origin не продолжать.
4. Проверить восстановленные копии raw/checkpoint и observation/protocol replay. Mismatch блокирует анализ; исключения входят в denominator и отчёт.
5. Восстановить operational state из backup/durable journal, если этот слой уже внедрён. Pending fills пометить interrupted, не придумывать исполнение во время downtime.
6. Проверить endpoint, reconnect/rotation, новый closed segment и внешний receipt. Результат оформить отдельным incident report.

Restore drill — в отдельный пустой каталог/DB; не поверх действующей истории. Повторять после изменения storage format и не реже месяца. Rollback release не откатывает datasets и не удаляет новые события.

## K. Последовательность переноса

1. После отдельного подтверждения Phase 2 определить VPS, OS, регион, SSH-доступ и доступное место; read-only inventory CPU/RAM/disk/inodes/ports/units/Nginx/time sync. На общем сервере reboot требует отдельно согласованного окна из-за соседних сервисов.
2. Сохранить текущую рабочую копию. Собрать проверяемый release из **всего нужного untracked prediction-кода**, tests и requirements; manifest SHA-256 и source revision. Не использовать `git add .` или один HEAD как release. Исключить `.env`, SQLite, venv, node_modules, raw и исследовательские caches. Секреты не включать в manifest values.
3. Локально подготовить только Phase 2 support: graceful lifecycle/identity, persistent launcher, узкий status API, unit/env/monitor/backup templates. Прогнать тесты; production logic и frozen strategy не менять.
4. Создать отдельный user/directories, установить Linux Python 3.12 venv из requirements, Node runtime совместимый с lockfile, выполнить `npm ci`/build отдельно от live collection. Не копировать Windows venv. Versions зафиксировать в release manifest после проверки на выбранной ОС.
5. Настроить firewall/time sync/logs; добавить units без включения shadow/Chainlink/новых активов. Проверить `systemd-analyze verify`, права и port collisions. Data mount должен быть prerequisite service, чтобы при его отсутствии не писать в корневой диск.
6. Запустить bounded public sample 30 минут с persistent `--output`. Проверить current+next, fresh books, запись raw и stats, graceful stop и closed manifest. Этот sample — технический, не forward strategy test.
7. Проверить process restart и согласованный reboot на staging/выделенном VPS; новые run IDs, отсутствие дублей writer, явный gap, закрытие/карантин хвоста. `Restart=on-failure` не продлевает окончившийся sample.
8. Проверить status API и backup/restore закрытого sample; replay и protocol gate, реальные CPU/RSS/lag/storage measurements. Не требовать >95% как уже достигнутого результата до Phase 3 fixes.
9. Выпустить `LIVE_SHADOW_DEPLOYMENT_REPORT.md`, фактические команды/versions/files, проблемы и rollback. **STOP после Phase 2.** Оставить службы остановленными после bounded проверки, если отдельно не разрешено непрерывное продолжение.
10. Только после следующего подтверждения выполнить Phase 3 по разделу N. При невыполнении gates не запускать shadow.

## L. План файлов

### Файлы этой итерации

Создан `LIVE_SHADOW_INFRASTRUCTURE_PLAN.md`; в `PROJECT_PLAN.md`, `BACKLOG.md`, `CHANGELOG.md` добавлен текущий статус и ссылка. README/ARCHITECTURE и production files не требуют изменений: запуск/реализованная архитектура не изменились. Исторические dataset/report файлы не переписываются.

### Phase 2 — создать после подтверждения

| Путь | Задача |
|---|---|
| `ops/linux/prediction-collector.service.example` | Bounded staging, права, restart policy |
| `ops/linux/prediction-api.service.example` | Узкий status API на 18010 |
| `ops/linux/web-dashboard.service.example` | Только если переносится существующий frontend, port 13010 |
| `ops/linux/prediction.env.example` | Только явно реализованные настройки без secrets |
| `ops/linux/prediction-nginx.conf.example` | Изолированный reverse proxy, позднее TLS; без перезаписи старого сайта |
| `ops/linux/prediction-health-monitor.service.example`, `.timer.example` | Health check, disk/time/backup |
| `ops/linux/prediction-backup.service.example`, `.timer.example` | Copy-only closed segments, receipts |
| `services/api/app/scripts/run_prediction_service.py` | Persistent path, owner lock, process/run identity; использовать существующий collector |
| `services/api/app/scripts/check_prediction_health.py` | Read-only check и machine-readable результат |
| `services/api/app/scripts/backup_prediction.py` | Manifest/checksum/upload orchestration для выбранного storage |
| `services/api/app/prediction/status_api.py` | Изолированный ASGI status entrypoint без candle DB dependency |
| `services/api/tests/test_prediction_service.py` | Stop/restart, lock, read-only paths/status |
| `docs/prediction-linux-runbook.md`, `LIVE_SHADOW_DEPLOYMENT_REPORT.md` | Установка, фактические проверки, rollback |

Точечно изменить `scripts/collect_prediction.py` для корректной остановки/идентичности, `scripts/prediction_finalization_io.py` для Linux process identity. Если graceful shutdown требует общей утилиты — минимальный модуль в `prediction/`, без новой архитектуры supervision framework. Env runtime mappings документировать вместе с кодом.

### Phase 3 — отдельное подтверждение

Изменить `prediction/live_transport.py`, `live_storage.py`, `scripts/collect_prediction.py`, связанные replay/verifier и tests: ранняя инвалидация, bounded metrics/writer, durability/partitions/continuous lifecycle. Добавить `ops/linux/prediction-finalizer@.service.example`, `tests/test_prediction_collector_soak.py`, общий crash/finalization dispatcher при необходимости, `PREDICTION_COLLECTOR_24H_REPORT.md`. Обновить runbooks и документацию фазы. Heavy broker и миграция основной БД не нужны.

### За пределами Iteration 2

Phase 4–8: frozen config micro_arb_v0, live scheduler/event channel, journal/domain models, PostgreSQL migrations в отдельном контуре, shadow service. Phase 9–14: prediction routes/components и графики. Имена будущих module/files уточнять по контракту на соответствующей фазе; сейчас их не создавать.

## M. Риски и неизвестные

1. Production-ready 24/7 не доказан: coverage, delayed invalidation и синхронный hot path остаются блокерами Phase 3.
2. CPU/RAM VPS неизвестны; 4/8 — кандидат, 160 GB основаны на проекции и запасе. Точный rate может увеличиться с рыночной активностью и изменениями протокола.
3. UNKNOWN settlement, Limitless minimum/taker delay, историческая привязка fee tier и rounding не закрыты. План не обещает реальную прибыль.
4. Нет сохранённых network-arrival timestamps ниже `ws.recv`; backlog и local receive не равны времени matching engine. 100/250ms пока модельные сценарии.
5. Заимствование offline Scanner с целым timeline в live daemon нарушит модель исполнения/памяти. Нужен отдельный forward scheduler с теми же чистыми расчётами и causality gate.
6. Bootstrap live/replay может совпасть на checkpoints и всё же содержать protocol-небезопасные интервалы: требуются обе проверки.
7. Два старых Git commits не содержат текущей реализации; неполная упаковка — риск потери функциональности и воспроизводимости.
8. Старые VPS credentials/ports/сервисы не проверены. Не менять соседние сервисы и не reboot общий VPS без согласованного окна.
9. Backup location/access policy ещё не выбраны; RPO/RTO являются целями. Автоудаление raw не разрешено.
10. Семь общих Ruff I001 остаются существующим техническим долгом, общий baseline нельзя назвать полностью зелёным.

## N. Точный объём Iteration 2

**Iteration 2 разделена на две последовательные принимаемые части по требованию STOP после каждой фазы.** Подтверждение Phase 2 не трактуется как автоматическое подтверждение Phase 3.

### Часть 2A — Phase 2: Linux staging deployment

Вход: явное подтверждение, выбранный VPS/доступ, проверенный release manifest, согласованный storage/backup path. Результат: отдельные user/venv/runtime/data/logs, systemd bounded collector + status, firewall/NTP, backup receipt и restore sample. Критерии приёмки: старт, controlled stop, restart и reboot подтверждены фактически; каталог сохраняется, новый старт не дописывает старую сессию; status корректно показывает stale/degraded; raw пишет и воспроизводит sample, никакого shadow. Сохранить resource measurements и фактические ошибки. Отчёт и **STOP**.

### Часть 2B — Phase 3: непрерывный collector

После нового подтверждения:

1. Исправить delayed invalidation с сетевым тестом медленного close; parser/error/overflow/disconnect немедленно закрывают VALID.
2. Профилировать hot path, ограничить память/статистику, убрать сканирование всех сегментов с каждого tick. Если нужен writer — bounded bytes/items, overflow как явная потеря continuity, raw-before-apply и durable watermarks.
3. Continuous lifecycle без ежедневного reconnect ради каталогов; 60s segments, независимые partitions/checkpoints, SIGTERM/reboot recovery и явные cross-session gaps.
4. Интегрировать incremental health, closed-partition finalizer, off-host backup/restore. Сохранять raw, проверить rollback и fault injection только на fixtures/staging.
5. Unit/integration tests + Ruff; Linux fault tests: 429/timeout, malformed/binary frame, reconnect, late version, slow writer, disk pressure, SIGTERM/SIGKILL/reboot, PID reuse, backup outage. Frozen fixtures не смешивать с live.
6. Собрать **24 часа collector-only** на неизменной конфигурации после исправлений. Это техническая проверка Phase 3, не Phase 15 shadow validation. Не засчитывать плановые часы заранее.
7. Критерии: >95% duration-weighted cross-venue valid по полному 24h календарному интервалу с downtime/MISSING в знаменателе; per-window breakdown; ноль необъяснённых replay mismatches, все gaps/overflow явны, нет ложного VALID; фактические disk/CPU/RSS/queue/lag и backup restore; повторный старт безопасен.
8. Если gate провален — отчёт с причинами, shadow не разрешать. Если пройден — `PREDICTION_COLLECTOR_24H_REPORT.md`, обновления docs и **STOP**, запрос следующего задания на Phase 4.

Iteration 1 завершает только аудит и план. Phase 2 и последующие фазы не начаты.
