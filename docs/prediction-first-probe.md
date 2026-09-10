# BTC 5m cross-venue: первый probe

Дата: 9 сентября 2026 года. Финальный снимок: 13:32:00 UTC.

**Результат: UNKNOWN / BLOCKED_SETTLEMENT. Арбитраж не доказан и не опровергнут.** Найдены рынки Polymarket и Limitless на одинаковые окна; полного доказательства settlement equivalence нет. По STEP 4 задания L2 разрешён только после EXACT, поэтому WS не подключался, несколько полных окон книг не собирались. Итерация выполнена в части discovery, проверяемого расчётного ядра и отчёта о блокировке; цель доказательства edge остаётся открытой.

## A. Polymarket API

Фактически получены HTTP 200 от Gamma `GET /markets?slug=btc-updown-5m-<timestamp>` для текущего и следующего окон. Дополнительно проверен `GET /events/slug/<slug>`: недостающие strike/fallback он не раскрыл в полученном ответе.

| Окно UTC | Market ID | Slug |
|---|---|---|
| 13:30–13:35 | 4364373 | btc-updown-5m-1788960600 |
| 13:35–13:40 | 4364436 | btc-updown-5m-1788960900 |

Доступны condition ID, оба outcome token ID, rules, resolutionSource, eventStartTime, endDate, orderMinSize=5 и tick size=0.01. YES нормализован как Up, NO как Down по фактическому массиву outcomes; порядок токенов не предполагается. `startDate` — дата создания/активации заранее, не начало 5m окна. Strike в проверенных Gamma payload отсутствует.

Публичный канал `wss://ws-subscriptions-clob.polymarket.com/ws/market`, подписка по `assets_ids`, snapshots `book` и изменения `price_change` документированы официально. Сетевое подключение в этой итерации не проверялось из-за matching gate. [Market channel](https://docs.polymarket.com/api-reference/wss/market).

## B. Limitless API

Фактически доступны без credentials: `/markets/active?limit=25`, `/markets/timeline?symbol=BTC&frequency=minutely&subFrequency=minutes_5&before=0&after=1`, `/markets/<slug>`. Первоначальный запрос `limit=100` получил HTTP 400: максимум 25. Рабочий probe использует timeline, поскольку active-list предназначен для открытого slot; это не надёжный источник следующего окна. [Timeline](https://docs.limitless.exchange/api-reference/markets/timeline).

| Окно UTC | Market ID | Strike на момент снимка |
|---|---|---|
| 13:30–13:35 | 391992 | 79602.581889713950425088 |
| 13:35–13:40 | 391994 | Не установлен; API возвращал `---` |

Metadata подтверждает `tradeType=clob`, condition ID, YES/NO token IDs, startAt, expirationTimestamp, USDC collateral, правила и Chainlink feed. `---` сохраняется в raw, но нормализуется как null. Неизвестные trading minimum и tick size не подменены reward-параметрами `settings.minSize`/`metadata.minSize`.

Socket.IO: `wss://ws.limitless.exchange`, namespace `/markets`, событие подписки `subscribe_market_prices`, `marketSlugs`. Публичный `orderbookUpdate` не требует ключей. Это документированная возможность, а не результат live handshake. [WebSocket](https://docs.limitless.exchange/developers/quickstart/websocket).

## C. Settlement equivalence

Обе проверенные пары имеют статус **UNKNOWN**, а не EXACT и не установленный NOT_EQUIVALENT.

| Проверка | Polymarket | Limitless | Вывод |
|---|---|---|---|
| BTC, 300 секунд, start/end | Подтверждено | Подтверждено | Совпадают |
| Reference | BTC/USD TWAP 60s | BTC/USD TWAP 60s | Совпадает URL |
| Lookback | cryptoMarketConfig=60 | twapWindowSeconds=60 | Совпадает |
| Равенство | Up при `>=` | Up при `>=` | Совпадает |
| Strike | Не раскрыт в ответах | Число текущего окна | Сравнение невозможно |
| Feed ID | Нет в полученных metadata | Указан ниже | Идентичность не доказана |
| Выбор начального report | Недостаточно данных | Есть captured/selected timestamps | Полный общий контракт не доказан |
| Выбор конечного report/fallback | Не раскрыт | Точный boundary, затем первая observation в следующие 5 секунд | Общий fallback не доказан |
| Precision/rounding | Не подтверждены для settlement | priceDecimals=18, rounding не подтверждён | Недостаточно |
| Cancellation | Не подтверждён полный контракт | Не подтверждён полный контракт | Недостаточно |
| Payout | Нужна привязка collateral/redemption к рынку | Metadata указывает USDC | Полная экономическая эквивалентность не доказана |

Limitless feed ID: `0x0002ee6757e8822c00d273bc340fc24c9cafe123a4ff2ea1dbdb31944bc7d95f`.

Общий URL: `https://data.chain.link/streams/btc-usd-twap-60s-streams`. Прямое чтение каталога в проверке вернуло HTTP 429 с security checkpoint; это не доказательство недоступности самого feed. Новые Chainlink ticks не запрашивались.

Limitless rules говорят: при отсутствии report в пятисекундном fallback-окне автоматического разрешения не будет. Текст Polymarket не позволяет доказать идентичное поведение. Документация Polymarket о TWAP описывает передачу подписанных значений, но не даёт полного market-specific контракта выбора boundary/fallback; внутренние параметры расчёта нельзя самостоятельно восстанавливать по потоку. [Chainlink TWAP](https://docs.polymarket.com/market-data/chainlink-twap).

Нормализатор сохраняет только уверенно извлечённые поля с provenance. Часть утверждений в описании Limitless ещё не переведена в общий машинный контракт, поэтому список missing шире одного лишь неизвестного strike. Это консервативная блокировка, а не утверждение о различии исходов. Совпавшие фактические победители нескольких окон также не докажут EXACT для пограничных случаев.

## D. L2 comparison

**Реальные книги в этой итерации не получены.** Сравнение ниже относится к официальным протоколам и тестируемым обработчикам, не к live sample.

| Площадка | Форма книги | Обновление | Ограничение |
|---|---|---|---|
| Polymarket | Отдельные bids/asks по YES и NO token ID | Snapshot + абсолютный size на изменённом уровне, size=0 удаляет уровень | После разрыва нужен свежий snapshot; timestamp не является непрерывным sequence |
| Limitless | Объединённая YES-книга | Каждый WS frame заменяет полную книгу; изменения могут объединяться | Version не обязана расти на 1; может сбрасываться после failover |

Limitless официально описывает преобразование: NO asks = `1 − YES bids`, NO bids = `1 − YES asks`, sizes сохраняются. Нативная NO-ликвидность уже включена в YES-книгу; её нельзя повторно прибавлять. [Orderbook и преобразование NO](https://docs.limitless.exchange/api-reference/trading/orderbook).

Для Limitless нет публичного WS-потока каждой отдельной сделки; книга содержит объединённое состояние, поэтому frames нельзя считать числом trades. [Market data](https://docs.limitless.exchange/developers/websocket/market-data).

## E. First live opportunities

Не измерены. `opportunities_observed=null`, не ноль возможностей на рынке. Все 28 строк двух направлений × семи размеров × двух окон имеют `BLOCKED_MATCH`. Файл ложных opportunities не создаётся.

## F. Executable size

Live capacity неизвестна для всех Q=10/25/50/100/250/500/1000. Ядро проходит пример из задания: на синтетических уровнях Q=50 даёт Cost=0.89 и gross PnL=5.50; Q=500 — VWAP YES=0.467, NO=0.472, gross PnL=30.50; Q=1000 не исполним по depth. **Эти значения — только тестовые, не рыночная находка.**

Gross считается до taker fees, задержки обеих ног, settlement/redemption и других издержек. Даже положительный gross сам по себе не доказывает положительный net результат или гарантированное исполнение двух ног.

## G. Opportunity lifetime

Live median/p95 и buckets неизвестны. В ядре duration считается локальным monotonic clock между первым и последним положительным наблюдением. Это наблюдаемая нижняя граница; closed timestamp сохранён отдельно. Stale/disconnect/expiry/stop цензурируют эпизод и не продлевают duration до момента обнаружения обрыва. Границы buckets: [0,25), [25,50), [50,100), [100,250), [250,500), [500,1000), [1000,5000), [5000,+∞) ms.

Coalescing Limitless и отсутствие точного общего времени получения обеих площадок ограничивают будущие выводы о коротких эпизодах. Duration не называется network latency или market latency.

## H. Data volume

Финальный одноразовый metadata probe занял 1.313 секунды до записи summary:

| Поток | Строки | Байты |
|---|---:|---:|
| raw_http | 5 | 25 092 |
| markets | 4 | 10 473 |
| matches | 2 | 1 556 |
| Итого до summary | 11 | 37 121 |

MB/hour, rows/hour, messages/sec и projected GB/day для L2 — **не измерены**. Краткий metadata burst нельзя экстраполировать в постоянный поток. В итоговом JSON rate-поля равны null. Summary и HTML — дополнительные артефакты, не включённые в эту таблицу.

Исходные данные: `services/api/data/prediction/20260909-final-probe/`; каталог исключён из Git. Реальная пара metadata 13:25–13:30 сохранена также в `services/api/tests/fixtures/prediction_metadata.json` для воспроизводимого теста UNKNOWN. Ранние каталоги probe сохранены отдельно; их пробные throughput-экстраполяции не использовать — финальная реализация от них отказалась.

PostgreSQL/Parquet не выбраны и SQLite не мигрирована. Следующий storage выбор возможен после нескольких часов реального L2.

## I. DexSport

Официальный сайт показывает BTC Up/Down 5m, а обзор продукта описывает YES/NO и перепродажу контрактов. Однако эти страницы не подтверждают независимый CLOB, публичный L2 endpoint, единицы depth, механизм executable quotes или требования auth для market data. [Сайт](https://dexsport.io/), [описание prediction markets](https://dexsport.io/academy-articles/dexsport-prediction-market-review/).

Вердикт подключения аналогичным adapter: **UNKNOWN, пока нельзя подтвердить**. Adapter не написан, order book не имитировался. Нужны официальный протокол и фактический read-only snapshot/stream с price/size, а затем отдельная проверка settlement.

## J. Next Phase

1. Закрыть перечисленные settlement-пробелы официальными спецификациями/market-specific evidence. Для каждого окна сравнивать реальные strikes, а не заимствовать цену соседней площадки. Уточнить minimum/tick size Limitless.
2. Только после EXACT подключить сетевые read-only WS adapters и lifecycle окон. Протокольные обработчики сейчас есть, но reconnect, subscription heartbeat, контроль отставания и integration live runner ещё не реализованы.
3. Собрать минимум три полных 5m окна, затем несколько часов. Сохранять raw WS, reconstructed books, статусы и события; измерить частоту, edge distribution, capacity, censored lifetime и объём хранения. До этого вывод о venue lag не делать.
4. После доказательства gross edge проверить комиссии, доступность обеих ног и устойчивость результата. Выбор хранения и дальнейшие действия определять по измерениям; execution остаётся вне этой итерации.

## Реализация и проверка

Новые файлы: `app/prediction/{models,discovery,books,observer,storage,screen}.py`, CLI `app/scripts/probe_prediction.py`, `tests/test_prediction.py` и metadata fixture. В README описан запуск, обновлены план/backlog/архитектура/changelog. Старые Binance/Chainlink/candles/SQLite/dashboard не изменены.

45 backend-тестов прошли (24 прежних + 21 новый). Scoped Ruff check и format check прошли. Отдельных frontend-изменений нет; Next.js build повторно не запускался. HTML проверен в браузере на читаемость таблиц и явную блокировку расчётов. Runtime-зависимости не добавлены; установлен уже предусмотренный `requirements-dev.txt` Ruff 0.8.6.

Остановка на неподтверждённом settlement предусмотрена STEP 4 пользовательского задания, не требованием дополнительного разрешения. Ордера, кошельки, private keys, maker execution и backtester не использовались.
