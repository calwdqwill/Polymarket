# План проекта по фазам

План описывает поэтапную реализацию MVP крипто-дашборда.

## Фаза 0 - исследование источников

Статус: завершено.

Цели:

- Проверить, какие данные реально доступны через Polymarket.
- Не смешивать Polymarket CLOB prices и spot-price BTC/ETH/SOL.
- Проверить Chainlink как основной источник historical/realtime price data.
- Выбрать источник historical backfill и realtime.

Результат:

- Polymarket оставлен как market context.
- Основной MVP source - Chainlink.
- Historical source - Chainlink Candlestick API.
- Timeframe MVP - только `5m`.
- DB для MVP - SQLite.

## Фаза 1 - проектный скелет

Статус: завершено.

Цели:

- Создать структуру проекта.
- Добавить backend/frontend skeleton.
- Спроектировать базовые таблицы.
- Подготовить миграции.

Сделано:

- `apps/web` - Next.js skeleton.
- `services/api` - FastAPI backend.
- SQLAlchemy models.
- Alembic migration `0001_initial`.
- `.env.example`.
- `README.md`, `docs`.

Критерий готовности:

- Python-файлы парсятся.
- JSON-конфиги frontend валидны.

## Фаза 2 - runnable backend baseline

Статус: завершено.

Цели:

- Поднять backend локально.
- Создать SQLite DB.
- Засидить BTC/ETH/SOL.
- Проверить базовые endpoints.

Сделано:

- Создан `.venv`.
- Установлены backend dependencies.
- Создан `services/api/.env`.
- Выполнен `alembic upgrade head`.
- Выполнен `python -m app.scripts.seed_assets`.
- Проверены:
  - `/api/status`;
  - `/api/assets`.

Критерий готовности:

- API отвечает `ok`.
- BTC/ETH/SOL есть в БД.

## Фаза 3 - Chainlink historical backfill foundation

Статус: завершено.

Цели:

- Реализовать Chainlink client.
- Реализовать backfill CLI.
- Реализовать storage/upsert свечей.
- Реализовать read endpoints для свечей и анализа.

Сделано:

- `ChainlinkClient`:
  - `/api/v1/authorize`;
  - `/api/v1/history/rows`;
  - parsing candle rows.
- `backfill.py`:
  - `--asset`;
  - `--days`;
  - `--from`;
  - `--to`;
  - `--chunk-days`;
  - `--dry-run`.
- `storage.py`:
  - query builder;
  - serializers;
  - upsert Chainlink candles.
- Реальные endpoints:
  - `/api/candles`;
  - `/api/candles/{id}/drilldown`;
  - `/api/ticks`;
  - `/api/analysis/window`;
  - `/api/imbalances`.

Критерий готовности:

- `backfill --dry-run --asset all --days 90` показывает план чанков.
- API endpoints корректно отвечают на пустой базе.

## Фаза 4 - smoke backfill на реальном Chainlink API

Статус: частично заблокировано.

Цели:

- Добавить реальные Chainlink credentials.
- Проверить формат ответа на аккаунте.
- Загрузить 1 день BTC.
- Проверить масштаб цены.
- Проверить записи в `candles`.

Шаги:

1. Заполнить `services/api/.env`:

```text
CHAINLINK_USER_ID=
CHAINLINK_API_KEY=
CHAINLINK_PRICE_DECIMALS=18
```

2. Запустить:

```powershell
python -m app.scripts.backfill --asset BTC --days 1
```

3. Запустить API:

```powershell
uvicorn app.main:app --reload
```

4. Проверить:

```text
http://127.0.0.1:8000/api/candles?asset=BTC&timeframe=5m
```

Критерий готовности:

- В `candles` появились BTC-свечи.
- Цены выглядят реалистично.
- `timestamp_start` идет по 5 минут.
- Дубликаты не создаются при повторном запуске.

Текущий результат:

- Candlestick API `/api/v1/authorize` доступен, но текущие Streams credentials дают `401 Unauthorized`.
- Добавлена smoke-команда `python -m app.scripts.smoke_backfill --asset BTC --days 1` с проверками DB, price scale, 5m step и idempotency.
- Для продолжения historical backfill нужны отдельные/разрешенные credentials для Chainlink Candlestick API.

## Фаза 4B - Chainlink Streams realtime bootstrap

Статус: завершено.

Цели:

- Проверить HMAC credentials Chainlink Data Streams.
- Подключить latest report для BTC/USDT, ETH/USDT и SOL/USD.
- Декодировать `fullReport` Data Streams report v3.
- Писать realtime observations в `price_ticks`.
- Агрегировать observations в локальные 5m candles.

Сделано:

- `ChainlinkStreamsClient`:
  - HMAC headers `Authorization`, `X-Authorization-Timestamp`, `X-Authorization-Signature-SHA256`;
  - `GET /api/v1/reports/latest`;
  - decoder `fullReport` -> `price`, `bid`, `ask`, `observations_timestamp`.
- `realtime_storage.py`:
  - upsert stream ticks в `price_ticks`;
  - update/create 5m candles в `candles`;
  - `source = chainlink_streams`.
- CLI:
  - `python -m app.scripts.check_chainlink_streams_auth --asset BTC`;
  - `python -m app.scripts.poll_chainlink_streams --once --asset all`;
  - `python -m app.scripts.poll_chainlink_streams --asset all --interval 10`.
- Проверены новые feedID:
  - BTC/USDT;
  - ETH/USDT;
  - SOL/USD.

Критерий готовности:

- One-shot polling пишет BTC/ETH/SOL ticks в `price_ticks`.
- 5m candles создаются/обновляются с `source = chainlink_streams`.
- Повторный polling обновляет текущую свечу и увеличивает `tick_count`.

## Фаза 4A - проверка качества сборки

Статус: завершено.

Цели:

- Сделать frontend-команды проверяемыми.
- Проверить локальный старт backend/frontend.
- Проверить миграции с нуля.

Сделано:

- Установлены npm-зависимости frontend.
- Добавлен `package-lock.json`.
- `lint` переведен с устаревшего `next lint` на `eslint .`.
- Добавлен `test` script как TypeScript typecheck:
  - `tsc --noEmit`.
- Добавлен `eslint.config.mjs` для Next.js + ESLint 9.
- Проверены:
  - `npm run lint`;
  - `npm run test`;
  - `npm run build`;
  - fresh Alembic migration на отдельной SQLite-базе;
  - локальный старт backend;
  - локальный старт frontend.

Критерий готовности:

- Все обязательные команды проходят.
- Backend отвечает `/api/status`.
- Frontend возвращает HTTP 200 на `http://127.0.0.1:3000`.

## Фаза 5 - полный historical backfill

Статус: завершено через Binance fallback; Chainlink historical остается заблокирован Candlestick credentials.

Цели:

- Загрузить 90 дней по BTC/ETH/SOL.
- Проверить gaps/duplicates.
- Подготовить данные для dashboard.

Шаги:

1. Запустить:

```powershell
python -m app.scripts.backfill --asset all --days 90
```

Fallback при блокировке Chainlink Candlestick API:

```powershell
python -m app.scripts.backfill_binance --asset all --days 90
```

2. Реализовать/доработать `validate_candles.py` - сделано.
3. Проверить:
  - gaps;
  - duplicates;
  - OHLC consistency;
  - source/timestamp.

Текущий результат:

- Реализована CLI-команда `python -m app.scripts.validate_candles --asset all --days 90`.
- Валидатор проверяет gaps, duplicates, OHLC consistency, `source`, `timestamp_start/timestamp_end` и 5m alignment.
- `python -m app.scripts.backfill --dry-run --asset all --days 90` показывает 18 чанков на каждый актив.
- Реальный `python -m app.scripts.backfill --asset all --days 90` доходит до Chainlink, но `/api/v1/authorize` возвращает `401 Unauthorized`.
- Добавлен Binance historical fallback `python -m app.scripts.backfill_binance --asset all --days 90`.
- Загружено по 25 920 5m candles за 90 дней по BTC/ETH/SOL в `candles` с `source = binance_klines`.
- Повторный Binance backfill за тот же диапазон дал `inserted = 0`, `updated = 25920` по каждому активу.
- `python -m app.scripts.validate_candles --asset all --source binance_klines --from 2026-02-17T20:00:00Z --to 2026-05-18T20:00:00Z` проходит без ошибок и warnings.
- Historical candles `source = chainlink_candlestick` за 90 дней сейчас отсутствуют; realtime candles `source = chainlink_streams` продолжаем копить локально.

Критерий готовности:

- 90 дней 5m candles загружены по всем активам.
- Повторный запуск не плодит дубликаты.
- Валидатор не показывает критических ошибок.

Примечание:

- Chainlink Candlestick historical backfill пока заблокирован `401 Unauthorized` на `/api/v1/authorize`.
- Binance historical fallback хранится как spot-like OHLCV свечи с отдельным `source = binance_klines`; это не Polymarket outcome-token prices.

## Фаза 6 - imbalance detection

Статус: завершено.

Цели:

- Реализовать detection engine.
- Сохранять события в `imbalance_events`.
- Дать API для фильтрации событий.

Задачи:

- Percent move за N свечей.
- Green/red streaks.
- Body anomaly.
- Wick anomaly.
- Body/range imbalance.
- Rolling mean/median deviation.
- Z-score threshold.
- Percentile threshold.

Критерий готовности:

- После backfill можно пересчитать imbalance events.
- `/api/imbalances` возвращает найденные события.

Текущий результат:

- Реализован полный detection engine для задач фазы 6:
  - percent move за N свечей;
  - green/red streaks;
  - body anomaly;
  - wick anomaly;
  - body/range imbalance;
  - rolling mean/median deviation;
  - z-score threshold;
  - percentile threshold.
- Добавлен CLI пересчета:

```powershell
python -m app.scripts.recalculate_imbalances --asset all --source binance_klines --from 2026-02-17T20:00:00Z --to 2026-05-18T20:00:00Z
```

- События сохраняются в `imbalance_events`; повторный пересчет с заменой не накапливает дубли.
- `GET /api/imbalances` поддерживает фильтры `asset`, `timeframe`, `source`, `direction`, `event_type`, `min_severity`, `window_size`, `from`, `to`, `limit`.
- По 90-дневному Binance fallback окну записано 12 048 событий:
  - BTC: 4 580;
  - ETH: 4 232;
  - SOL: 3 236.
- Контрольная проверка после закрытия фазы 6:
  - `npm run lint` - успешно;
  - `npm run test` - успешно;
  - `npm run build` - успешно при повторе вне sandbox после `spawn EPERM`;
  - fresh Alembic migration применена на `migration_phase6_fresh_check.db`;
  - backend стартует локально на `http://127.0.0.1:8000` и `/api/status` возвращает HTTP 200;
  - frontend production-start стартует на `http://127.0.0.1:3000` и возвращает HTTP 200.

## Фаза 7 - realtime worker

Статус: завершено.

Цели:

- Получать свежую цену из Chainlink.
- Сохранять ticks.
- Формировать активную 5m свечу.
- Финализировать свечу после закрытия окна.
- Пересчитывать свежие imbalance events.

Задачи:

- Polling или streaming, в зависимости от доступности API.
- Retry/backoff.
- Логирование ошибок.
- Защита от пропусков.
- Active candle state.
- Finalization.

Критерий готовности:

- После запуска worker появляются `price_ticks`.
- Текущая свеча обновляется.
- Закрытая свеча попадает в `candles`.

Текущий результат:

- Реализован hardened worker `app.workers.realtime_worker`.
- Совместимая CLI-команда сохранена:

```powershell
python -m app.scripts.poll_chainlink_streams --asset all --interval 10
```

- Polling interval MVP - 10 секунд.
- Добавлены retry/backoff-настройки для Chainlink Streams:
  - `CHAINLINK_STREAMS_RETRY_ATTEMPTS`;
  - `CHAINLINK_STREAMS_RETRY_INITIAL_SECONDS`;
  - `CHAINLINK_STREAMS_RETRY_MAX_SECONDS`.
- Добавлены structured polling results и логирование ошибок/повторных попыток.
- Worker держит active candle state по каждому asset.
- При переходе в новое 5m окно предыдущая свеча считается закрытой и остается в `candles`.
- Добавлена диагностика пропусков 5m окон между realtime observations.
- Добавлен автоматический пересчет свежих `imbalance_events` по закрытым realtime-свечам `source = chainlink_streams`.
- Добавлена настройка глубины свежего пересчета:
  - `REALTIME_IMBALANCE_LOOKBACK_CANDLES`.
- Добавлены unit-тесты для retry/backoff и gap detection.
- Контрольный one-shot polling с внешним доступом получил HTTP 200 по BTC/ETH/SOL, записал новые realtime ticks и обновил `chainlink_streams` candles.

## Фаза 8 - frontend dashboard

Статус: завершено в MVP-объеме.

Цели:

- Сделать удобный dark UI.
- Отобразить свечи и события.
- Добавить фильтры и drill-down.

Задачи:

- Overview cards BTC/ETH/SOL - сделано.
- Candlestick chart через `lightweight-charts` - сделано.
- Таблица последних свечей - сделано.
- Window metrics panel - сделано.
- Imbalance table - сделано.
- Heatmap дисбалансов - сделано.
- Drill-down по клику на свечу/строку таблицы - сделано.
- Export CSV/JSON - сделано.

Текущий результат:

- Реализован рабочий Next.js dashboard в стиле плотного dark UI по визуальному референсу.
- Главная карточка сделана в спокойной graphite/teal/indigo-палитре вместо яркого розового акцента.
- Frontend подключен к текущим backend endpoints:
  - `GET /api/assets`;
  - `GET /api/status/sources`;
  - `GET /api/candles`;
  - `GET /api/analysis/window`;
  - `GET /api/imbalances`;
  - `GET /api/candles/{id}/drilldown`.
- Добавлены фильтры asset, source, period, direction, event type, severity и опциональный фильтр event window.
- Candlestick chart показывает 5m candles и компактные markers для наиболее сильных imbalance events.
- Таблицы последних свечей и imbalance events обновляются от выбранных фильтров.
- Drill-down показывает OHLC-признаки выбранной свечи и tick count.
- CSV/JSON export выгружает текущую выборку candles/events/window metrics.
- Добавлена диагностика источников в UI:
  - `Chainlink Candlestick API` явно показывает блокировку `HTTP 401` и отсутствие historical candles;
  - `Chainlink accumulated realtime` показывает локально накопленную историю и предупреждает, что пропущенные окна не восстанавливаются через latest-report endpoint;
  - для realtime-источника включен авто-refresh каждые 30 секунд.
- Для локального накопления Chainlink realtime добавлен Windows Task Scheduler watchdog:
  - `ops/windows/run-chainlink-worker.ps1`;
  - `ops/windows/register-chainlink-worker-task.ps1`;
  - `ops/windows/status-chainlink-worker-task.ps1`;
  - `ops/windows/unregister-chainlink-worker-task.ps1`.
- Уточнено поведение накопления live-истории:
  - текущая машина не дала зарегистрировать Task Scheduler задачу, поэтому используется Startup fallback;
  - worker держится hidden watchdog-процессом и пишет лог в `services/api/logs`;
  - при старте worker пересобирает `chainlink_streams` candles из сохраненных `price_ticks`;
  - пропущенные интервалы, когда worker не получал Chainlink reports, не заполняются искусственными свечами.
- Проверено в браузере на локальных backend/frontend: данные загружаются, runtime error отсутствует.

Критерий готовности:

- Пользователь может выбрать asset, window, period.
- Видит candles и anomalies.
- Может открыть свечу и посмотреть признаки/drill-down.

## Фаза 9 - hardening

Статус: завершено в MVP-объеме.

Цели:

- Сделать MVP устойчивее.
- Подготовить переход на PostgreSQL/TimescaleDB.
- Добавить тесты.

Задачи:

- Unit tests - сделано.
- API tests - сделано.
- Backfill/realtime retry tests - сделано для текущего MVP.
- Ruff/formatter config - сделано.
- Единая локальная check-команда - сделано.
- PostgreSQL compatibility pass - подготовлен как следующий production-шаг, без миграции с SQLite в рамках локального MVP.
- Optional Docker - оставлено на серверный этап, если понадобится.

Текущий результат:

- Добавлены API-тесты для ключевых endpoints dashboard:
  - `GET /api/status`;
  - `GET /api/assets`;
  - `GET /api/candles`;
  - `GET /api/analysis/window`;
  - `GET /api/candles/{id}/drilldown`;
  - `GET /api/imbalances`;
  - `GET /api/status/sources`.
- Backend test suite расширен до 24 тестов.
- Добавлен `pyproject.toml` с базовой конфигурацией Ruff.
- Добавлен `services/api/requirements-dev.txt` для dev-зависимостей backend.
- Добавлен единый локальный predeploy-check:
  - `ops/check-local.ps1`.
- Добавлен server/deployment runbook:
  - `docs/deployment-runbook.md`.
- Добавлены systemd-шаблоны:
  - `ops/linux/poly-crypto-api.service.example`;
  - `ops/linux/poly-crypto-chainlink-worker.service.example`.
- `npm audit --audit-level=moderate` показал `0 vulnerabilities`.
- Полный `ops/check-local.ps1` прошел успешно вне sandbox: backend compile, 24 tests, frontend lint/typecheck/build, API health.

Критерий готовности:

- Основная логика покрыта тестами - выполнено для MVP.
- Проект можно стабильно запускать после fresh clone - команды зафиксированы в README и runbook.
- Есть понятные команды для backfill, worker и frontend - выполнено.

Оставшиеся внешние блокеры вне локального MVP:

- Chainlink Candlestick historical credentials.
- VPS/server для 24/7 Chainlink worker.
- Git remote и серверные доступы для фактической выкладки.
