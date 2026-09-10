# Changelog

## 10 сентября 2026 — локальный shadow engine, Iteration 1

- Создана ветка `codex/prediction-live-shadow`; существующие research и параллельные collector-изменения сохранены.
- Добавлены immutable config/events, forward-only state machine, independent 100/250 ms ledgers, attempt/window journal и append-only local storage. Денежные функции V1 переиспользованы.
- Зафиксированы строгий below-threshold retry, four-book validity gate, causal arrival/ACK и сохранение previous losses. Пустые окна также записываются.
- Добавлены 30 synthetic tests, historical replay CLI, независимый verifier и объяснение parity. 122 окна / 6 890 599 receive rows проверены дважды; 194 attempts/388 legs прошли independent verification. Общий backend suite 158/158, Ruff и compileall PASS. 183 общие попытки имеют точный net V1; 13 отвергнутых старых signals объяснены safety/crossing/cap. [Design и результаты](LIVE_SHADOW_ENGINE_DESIGN.md).
- Production collector/VPS/основная БД/API/frontend этим треком не изменялись. Iteration 2/3 не начаты; после отчёта STOP.

## 2026-09-10 — Linux shared staging Iteration 2A

Изолированный релиз `229aff0` прошёл 30m smoke и 2h capacity на общем VPS. Раннее снятие VALID перед async close, ограниченный receive backlog, incremental metrics, fsync и systemd resource/disk guards проверены. Итог **PASS_WITH_LIMITS** для отдельного 24h collector-only.

В 2h: 99,19% coverage, CPU 41,71% mean / 58,48% max sample, minimum MemAvailable 4,98 GB, без OOM/overflow. SHA-256 всех 1661 файлов и CRC 1656 gzip подтверждены; 2 575 247 raw frames, 11 043 checkpoints и 30 618 461 priced rows без расхождений. Отмечены lag/neighbor-health ограничения и 4 conservative strategy exclusions. Collector/status/health остановлены, новых запусков нет. [Отчёт A–K](LIVE_SHADOW_DEPLOYMENT_REPORT.md).

Все заметные изменения проекта фиксируем здесь.

## 2026-05-20

### Выполнено: серверная выкладка MVP

- Ветка `poly_crypto/V1.0` развернута на VPS `155.212.183.185`.
- Внешний dashboard доступен на:
  - `http://155.212.183.185:8080`.
- Серверный контур запущен без конфликта с существующим сайтом `mo-ex.online`:
  - `poly-crypto-api.service` -> `127.0.0.1:18000`;
  - `poly-crypto-web.service` -> `127.0.0.1:13000`;
  - `poly-crypto-chainlink-worker.service` -> Chainlink Streams polling каждые 10 секунд;
  - Nginx proxy -> `0.0.0.0:8080`.
- Локальная SQLite-база перенесена на сервер через консистентный backup, чтобы сохранить уже накопленную историю.
- Chainlink worker на VPS проверен:
  - service активен;
  - Chainlink Streams возвращает HTTP 200;
  - `GET /api/status/sources` показывает `chainlink_streams.is_live = true`;
  - ticks продолжают расти.
- Добавлен ежечасный SQLite backup на сервере:
  - `poly-crypto-db-backup.timer`;
  - копии хранятся в `/opt/poly_crypto/backups/sqlite`;
  - сохраняются последние 48 копий.
- Добавлены серверные шаблоны:
  - `ops/linux/poly-crypto-web.service.example`;
  - `ops/linux/poly-crypto-nginx-8080.conf.example`.

### Проверено

- Внешний `GET http://155.212.183.185:8080` возвращает HTTP 200.
- Внешний `GET http://155.212.183.185:8080/api/status` возвращает:
  - `status = ok`;
  - `environment = prod`;
  - `database = sqlite`.
- Внешний `GET http://155.212.183.185:8080/api/status/sources` показывает:
  - `binance_klines.total_candles = 77761`;
  - `chainlink_streams.total_candles = 690`;
  - `chainlink_streams.total_ticks = 13470`;
  - `chainlink_streams.is_live = true`.

### Осталось

- Настроить внешний backup: текущие ежечасные backups лежат на том же VPS.
- Разобрать свежий `npm audit` warning на сервере: `postcss < 8.5.10` через `next@15.5.18`, auto-fix не применялся из-за риска breaking change.
- Получить Chainlink Candlestick historical credentials и дозагрузить `source = chainlink_candlestick`.

## 2026-05-19

### Завершено: фаза 9 hardening в MVP-объеме

- Добавлены API-тесты `tests/test_api_routes.py` для основных endpoints dashboard:
  - `GET /api/status`;
  - `GET /api/assets`;
  - `GET /api/candles`;
  - `GET /api/analysis/window`;
  - `GET /api/candles/{id}/drilldown`;
  - `GET /api/imbalances`;
  - `GET /api/status/sources`.
- Backend test suite расширен с 18 до 24 тестов.
- Добавлен единый локальный predeploy-check:
  - `ops/check-local.ps1`.
- Добавлен `pyproject.toml` с базовой Ruff-конфигурацией.
- Добавлен `services/api/requirements-dev.txt` для backend dev-зависимостей.
- Добавлен runbook для Git/server deploy:
  - `docs/deployment-runbook.md`.
- Добавлены systemd-шаблоны:
  - `ops/linux/poly-crypto-api.service.example`;
  - `ops/linux/poly-crypto-chainlink-worker.service.example`.
- Проверено `npm audit --audit-level=moderate`: `0 vulnerabilities`.
- Полный `ops/check-local.ps1` успешно прошел вне sandbox: backend compile, 24 tests, frontend lint/typecheck/build, API health.

### Усилено: непрерывное накопление Chainlink live-истории

- Добавлен repair-механизм `rebuild_stream_candles_from_ticks`: realtime-свечи `source = chainlink_streams` могут быть честно пересобраны из уже сохраненных `price_ticks` без подмешивания mock/fake данных.
- `poll_chainlink_streams` при старте пересобирает Chainlink realtime candles из накопленных ticks, чтобы восстановить свечи, если раньше tick был записан, а candle-агрегация не успела обновиться.
- `ops/windows/run-chainlink-worker.ps1` получил single-instance mutex `Local\PolyCryptoChainlinkWorkerWatchdog`, чтобы повторный запуск launcher-а не плодил несколько одинаковых watchdog-процессов.
- Выполнен ручной repair текущей базы: BTC/ETH/SOL realtime candles пересобраны из сохраненных live ticks.
- Подтверждено текущее live-состояние `chainlink_streams`: BTC - 20 свечей, ETH - 20 свечей, SOL - 21 свеча; последние ticks продолжают обновляться.

### Добавлено: Windows supervisor для Chainlink Streams worker

- Добавлены PowerShell-скрипты:
  - `ops/windows/run-chainlink-worker.ps1`;
  - `ops/windows/register-chainlink-worker-task.ps1`;
  - `ops/windows/status-chainlink-worker-task.ps1`;
  - `ops/windows/unregister-chainlink-worker-task.ps1`.
- `run-chainlink-worker.ps1` запускает `python -m app.scripts.poll_chainlink_streams --asset all --interval 10`, пишет логи в `services/api/logs` и перезапускает worker после падения.
- `register-chainlink-worker-task.ps1` регистрирует Windows Task Scheduler задачу `PolyCrypto Chainlink Worker` при входе пользователя.
- Добавлен гайд:
  - `docs/chainlink-worker-service-guide.md`.
- В `.gitignore` добавлены `logs/` и `*.log`.

### Добавлено: гайд по индикаторам dashboard

- Добавлен подробный Markdown-гайд:
  - `docs/dashboard-indicators-guide.md`.
- В гайд вынесены пояснения по:
  - верхней карточке;
  - карточкам BTC/ETH/SOL;
  - source status;
  - фильтрам;
  - candlestick chart;
  - window metrics;
  - latest candles;
  - imbalance events;
  - heatmap;
  - drill-down;
  - CSV/JSON export.
- В dashboard добавлена раскрываемая вкладка `Гайд по индикаторам` с короткими пояснениями прямо в интерфейсе.

### Добавлено: диагностика Chainlink sources в dashboard

- Добавлен `GET /api/status/sources`.
- Endpoint возвращает по каждому источнику:
  - количество свечей и тиков;
  - первый/последний timestamp;
  - свежесть `chainlink_streams`;
  - последний `data_source_runs`;
  - явный `blocked_reason` для `chainlink_candlestick`, если Candlestick API падает с `401`.
- Dashboard теперь показывает source health banner:
  - `Chainlink Candlestick API` явно помечается как пустой и заблокированный `HTTP 401`;
  - `Chainlink accumulated realtime` объясняет, что это локальная история только за время работы worker-а;
  - пропущенные 5m окна не маскируются и не выдаются за доступную историческую Chainlink-выборку.
- Для `chainlink_streams` добавлен авто-refresh dashboard каждые 30 секунд.
- Запущен long-running Chainlink Streams worker:
  - worker получил HTTP 200 от Chainlink Streams;
  - обнаружил gap на 25 пропущенных 5m окон после предыдущего локального запуска;
  - продолжил накопление свежих realtime ticks и 5m candles.

### Проверено

- `python -m unittest discover -s tests` - успешно, 18 тестов.
- `npm run lint` - успешно.
- `npm run test` - успешно.
- `npm run build` - успешно.
- `GET /api/status/sources` возвращает:
  - `chainlink_streams.is_live = true`;
  - `chainlink_candlestick.total_candles = 0`;
  - `chainlink_candlestick.blocked_reason = Chainlink Candlestick API authorization failed with HTTP 401`.
- Browser-проверка на `http://localhost:3000` подтвердила warning для `Chainlink Candlestick API` и данные для `Chainlink accumulated realtime`.

### Добавлено: frontend dashboard для фазы 8

- Реализован рабочий Next.js dashboard в `apps/web`.
- Подключен frontend к backend API:
  - `GET /api/assets`;
  - `GET /api/candles`;
  - `GET /api/analysis/window`;
  - `GET /api/imbalances`;
  - `GET /api/candles/{id}/drilldown`.
- Добавлены overview cards BTC/ETH/SOL.
- Добавлен candlestick chart через `lightweight-charts`.
- Добавлены таблицы последних свечей и imbalance events.
- Добавлены фильтры:
  - asset;
  - source;
  - period;
  - direction;
  - event type;
  - severity;
  - optional event window.
- Добавлена heatmap дисбалансов по asset и event type.
- Добавлен candle drill-down с OHLC-признаками и tick count.
- Добавлен экспорт текущей выборки в CSV/JSON.
- Визуальный стиль адаптирован под референс: плотный dark dashboard; главная карточка сделана спокойнее, в graphite/teal/indigo-палитре вместо яркого розового акцента.
- Для `lightweight-charts` markers событий ограничены сильнейшими событиями и сортируются по времени, чтобы не ломать runtime ordering и не перегружать график текстом.

### Проверено

- `npm run lint` - успешно.
- `npm run test` - успешно.
- `npm run build` - успешно при запуске вне sandbox после Windows `spawn EPERM` в sandbox.
- Backend local start:
  - `uvicorn app.main:app --host 127.0.0.1 --port 8000`;
  - API отвечает на запросы dashboard.
- Frontend dev start:
  - `npm run dev -- -p 3000`;
  - dashboard доступен на `http://localhost:3000`.
- Браузерная проверка:
  - данные свечей и imbalance events загружаются;
  - runtime overlay отсутствует;
  - chart, таблицы и drill-down отображаются;
  - главная карточка визуально спокойнее розового референса.

### Добавлено: hardened realtime worker для фазы 7

- Реализован `app.workers.realtime_worker` как основной long-running worker для Chainlink Streams.
- Сохранена совместимость старой CLI-команды:
  - `python -m app.scripts.poll_chainlink_streams --asset all --interval 10`.
- Добавлены retry/backoff-настройки:
  - `CHAINLINK_STREAMS_RETRY_ATTEMPTS`;
  - `CHAINLINK_STREAMS_RETRY_INITIAL_SECONDS`;
  - `CHAINLINK_STREAMS_RETRY_MAX_SECONDS`.
- Добавлены structured polling results и логирование временных ошибок Chainlink Streams.
- Worker хранит active candle state по каждому asset.
- При переходе в новое 5m окно предыдущая свеча считается закрытой и остается в `candles`.
- Добавлена диагностика пропусков 5m окон между realtime observations.
- Добавлен автоматический пересчет свежих `imbalance_events` по закрытым realtime-свечам `source = chainlink_streams`.
- Добавлена настройка:
  - `REALTIME_IMBALANCE_LOOKBACK_CANDLES`.
- Добавлены unit-тесты `tests/test_realtime_worker.py` для retry/backoff и gap detection.

### Проверено

- `python -m compileall app` - успешно.
- `python -m unittest discover -s tests` - успешно, 18 тестов.
- `python -m app.scripts.poll_chainlink_streams --help` показывает новые параметры worker-а.
- Sandbox-запуск real polling без внешней сети корректно отработал retry/backoff и вернул structured errors.
- Повтор с разрешенным внешним доступом:
  - `python -m app.scripts.poll_chainlink_streams --once --asset all --no-recalculate-imbalances --retry-attempts 2 --retry-initial-delay 1 --retry-max-delay 2`;
  - получил HTTP 200 от Chainlink Streams по BTC/ETH/SOL;
  - записал новые `price_ticks`;
  - создал/обновил realtime candles `source = chainlink_streams`;
  - обнаружил gap после предыдущего локального запуска и вывел `missing_candle_count = 153`.
- Текущее количество локальных realtime rows после проверки:
  - BTC: 3 ticks, 2 candles;
  - ETH: 3 ticks, 2 candles;
  - SOL: 5 ticks, 3 candles.

### Добавлено: imbalance detection для фазы 6

- Расширен `app.analysis.imbalance_engine`:
  - percent move за N свечей;
  - green/red streaks;
  - body anomaly;
  - wick anomaly;
  - body/range imbalance;
  - rolling mean/median deviation;
  - z-score threshold;
  - percentile threshold.
- Добавлен storage helper `app.analysis.imbalance_storage`:
  - сериализация `imbalance_events`;
  - удаление старых событий выбранного `source` перед пересчетом;
  - запись найденных событий в `imbalance_events`.
- Добавлен CLI:
  - `python -m app.scripts.recalculate_imbalances --asset all --source binance_klines --from 2026-02-17T20:00:00Z --to 2026-05-18T20:00:00Z`.
- `GET /api/imbalances` получил фильтры:
  - `source`;
  - `event_type`;
  - `min_severity`;
  - `window_size`.
- Пересчитаны события по Binance fallback candles за окно `2026-02-17T20:00:00Z..2026-05-18T20:00:00Z`:
  - BTC: 4 580 событий;
  - ETH: 4 232 события;
  - SOL: 3 236 событий;
  - всего: 12 048 событий.
- Повторный пересчет BTC за то же окно удаляет 4 580 старых событий и вставляет 4 580 новых, не накапливая дубли.

### Проверено

- `python -m compileall app` - успешно.
- `python -m unittest discover -s tests` - успешно, 14 тестов.
- `GET /api/imbalances?asset=BTC&source=binance_klines&event_type=window_percent_move&limit=3` возвращает HTTP 200 и найденные события.
- `npm run lint` - успешно.
- `npm run test` - успешно.
- `npm run build` - в sandbox упал на `spawn EPERM`, повтор вне sandbox прошел успешно.
- Fresh migration с нуля применена на SQLite DB `migration_phase6_fresh_check.db`.
- Backend local start:
  - `uvicorn app.main:app --host 127.0.0.1 --port 8000`;
  - `http://127.0.0.1:8000/api/status` вернул HTTP 200.
- Frontend production local start:
  - `npm run start -- -p 3000`;
  - `http://127.0.0.1:3000` вернул HTTP 200.
- После проверки backend/frontend процессы на портах `8000` и `3000` остановлены.

## 2026-05-18

### Добавлено: Binance historical fallback для фазы 5

- Добавлен публичный Binance Spot kline client:
  - `app.feeds.binance.BinanceClient`;
  - `GET /api/v3/klines`;
  - parsing OHLCV rows;
  - retry/backoff и увеличенный timeout для длинного historical backfill.
- Добавлен CLI:
  - `python -m app.scripts.backfill_binance --asset all --days 90`;
  - `python -m app.scripts.backfill_binance --asset all --years 5`;
  - параметры `--from`, `--to`, `--days`, `--years`, `--limit`, `--request-sleep`.
- Добавлен source:
  - `source = binance_klines`.
- `upsert_candles` ускорен bulk lookup по timestamp внутри чанка, чтобы 2-5 лет истории не упирались в lookup на каждую свечу.
- `GET /api/candles` и `GET /api/analysis/window` получили query-фильтр `source`.
- В `.env.example` добавлены Binance-настройки:
  - `BINANCE_BASE_URL`;
  - `BINANCE_SYMBOL_BTC_USDT`;
  - `BINANCE_SYMBOL_ETH_USDT`;
  - `BINANCE_SYMBOL_SOL_USDT`;
  - `BINANCE_KLINES_LIMIT`;
  - `BINANCE_REQUEST_SLEEP_SECONDS`;
  - `BINANCE_RETRY_ATTEMPTS`;
  - `BINANCE_RETRY_SLEEP_SECONDS`;
  - `BINANCE_TIMEOUT_SECONDS`.
- Загружено:
  - BTC: 25 920 свечей в окне `2026-02-17T20:00:00Z..2026-05-18T20:00:00Z`;
  - ETH: 25 920 свечей в окне `2026-02-17T20:00:00Z..2026-05-18T20:00:00Z`;
  - SOL: 25 920 свечей в окне `2026-02-17T20:00:00Z..2026-05-18T20:00:00Z`.
- Проверено:
  - `python -m app.scripts.backfill_binance --asset all --days 90 --dry-run`;
  - `python -m app.scripts.backfill_binance --asset BTC --years 5 --dry-run` показывает 526 запросов;
  - реальный `python -m app.scripts.backfill_binance --asset all --days 90`;
  - повторный backfill за тот же диапазон: `inserted = 0`, `updated = 25920` по каждому активу;
  - `python -m app.scripts.validate_candles --asset all --source binance_klines --from 2026-02-17T20:00:00Z --to 2026-05-18T20:00:00Z` проходит без ошибок и warnings.

### Добавлено: validator для фазы 5

- Реализован `python -m app.scripts.validate_candles --asset all --days 90`.
- Валидатор проверяет:
  - gaps внутри серии свечей;
  - duplicate groups по `asset_id/timeframe/timestamp_start/source`;
  - OHLC consistency;
  - положительный масштаб OHLC;
  - `timestamp_end`, 5m alignment и `source`.
- Добавлены режимы:
  - `--source all` для проверки всех локальных источников;
  - `--strict-window` для превращения неполного покрытия периода в error;
  - `--allow-empty` для диагностических запусков на пустой БД;
  - `--json` для машинно-читаемого отчета.
- Проверено:
  - `python -m compileall app`;
  - `python -m app.scripts.backfill --dry-run --asset all --days 90`;
  - `python -m app.scripts.validate_candles --asset all --days 90`;
  - `python -m app.scripts.validate_candles --asset all --days 90 --source all --allow-empty`.
- Реальный `python -m app.scripts.backfill --asset all --days 90` был запущен с внешним сетевым доступом, но Chainlink Candlestick API `/api/v1/authorize` вернул `401 Unauthorized`.
- Текущее состояние данных:
  - `source = chainlink_candlestick`: 0 historical candles за 90 дней по BTC/ETH/SOL;
  - `source = chainlink_streams`: 4 локальные realtime candles;
  - в `data_source_runs` есть failed `chainlink_candlestick/backfill_5m` runs из-за `401`.

### Добавлено: Chainlink Streams realtime polling

- Добавлен HMAC-клиент Chainlink Data Streams:
  - `app.feeds.chainlink_streams.ChainlinkStreamsClient`;
  - поддержка signed headers `Authorization`, `X-Authorization-Timestamp`, `X-Authorization-Signature-SHA256`.
- Добавлен decoder latest `fullReport` для Data Streams report v3:
  - `price`;
  - `bid`;
  - `ask`;
  - `observations_timestamp`.
- Добавлен realtime storage:
  - запись Chainlink reports в `price_ticks`;
  - агрегация ticks в 5m candles с `source = chainlink_streams`;
  - dedup по `asset_id + observations_timestamp + source`.
- Добавлены CLI:
  - `python -m app.scripts.check_chainlink_streams_auth --asset SOL`;
  - `python -m app.scripts.poll_chainlink_streams --asset all --interval 10`;
  - `python -m app.scripts.poll_chainlink_streams --once --asset SOL`.
- В `.env` добавлены feedID-переменные:
  - `CHAINLINK_FEED_BTC_USDT`;
  - `CHAINLINK_FEED_ETH_USDT`;
  - `CHAINLINK_FEED_SOL_USD`.
- Проверено:
  - HMAC credentials работают;
  - BTC/USDT, ETH/USDT и SOL/USD latest reports доступны;
  - one-shot polling записывает BTC/ETH/SOL в `price_ticks`;
  - realtime observations агрегируются в 5m `candles` с `source = chainlink_streams`;
  - повторный polling обновляет текущую 5m свечу и увеличивает `tick_count`.
- Исправлена нормализация timestamp для SQLite при повторном обновлении realtime candle.
- Текущий остаточный блокер: Chainlink Candlestick API `/api/v1/authorize` возвращает `401 Unauthorized`, поэтому historical Chainlink backfill не выполнен.
- Проверено в этой итерации:
  - `npm run lint`;
  - `npm run test`;
  - `npm run build`;
  - backend unit tests;
  - fresh Alembic migration на отдельной SQLite DB;
  - backend local start и `/api/status`;
  - frontend production start и HTTP 200.

### Добавлено: smoke-проверка фазы 4

- Добавлен `python -m app.scripts.smoke_backfill --asset BTC --days 1`.
- Smoke CLI выполняет 1-day BTC backfill, повторный запуск для проверки idempotency и проверяет:
  - наличие свечей в `candles`;
  - реалистичный масштаб цены;
  - OHLC consistency;
  - шаг `timestamp_start` по 5 минут;
  - отсутствие дублей.
- `backfill` и `smoke_backfill` теперь падают до записи в БД, если в `.env` не заполнены `CHAINLINK_USER_ID` и `CHAINLINK_API_KEY`.
- Добавлены unit-тесты на Chainlink row parsing/price scaling и smoke-check validation.

### Добавлено: проверки frontend/backend и документация итерации

- Установлены npm-зависимости frontend.
- Добавлен `package-lock.json`.
- Исправлен frontend `lint` script:
  - было `next lint`;
  - стало `eslint .`.
- Добавлен `test` script:
  - `tsc --noEmit`.
- Добавлен `eslint.config.mjs` для Next.js + ESLint 9.
- В ESLint config исключены generated artifacts:
  - `.next/**`;
  - `node_modules/**`;
  - `out/**`;
  - `next-env.d.ts`.
- Добавлен `ReactNode` type import в `app/layout.tsx`.
- Созданы документы:
  - `ARCHITECTURE.md`;
  - `PROJECT_PLAN.md`.
- Обновлены:
  - `README.md`;
  - `BACKLOG.md`;
  - `ARCHITECTURE.md`;
  - `PROJECT_PLAN.md`;
  - `CHANGELOG.md`.

### Проверено: обязательные команды

- `npm run lint` - успешно.
- `npm run test` - успешно.
- `npm run build` - успешно после повторного запуска вне sandbox из-за `spawn EPERM`.
- Миграции применяются с нуля на отдельной SQLite-базе:
  - `fresh migration ok`.
- Backend стартует локально и `/api/status` возвращает `ok`.
- Frontend стартует локально через `npm run start -- -p 3000` и возвращает HTTP 200.

### Риски/заметки

- `npm install` сообщил о 2 moderate vulnerabilities. Автофикс не применялся, потому что `npm audit fix --force` может обновить зависимости с breaking changes.
- `npm run test` сейчас проверяет TypeScript-типы, но не является полноценным unit-test suite.
- В sandbox `next build` падал на `spawn EPERM`; вне sandbox build проходит.

### Добавлено: Chainlink/backfill и read endpoints

- Обновлена конфигурация Chainlink:
  - `CHAINLINK_BASE_URL=https://priceapi.dataengine.chain.link`;
  - `CHAINLINK_USER_ID`;
  - `CHAINLINK_API_KEY`;
  - `CHAINLINK_PRICE_DECIMALS=18`;
  - `CHAINLINK_SYMBOL_BTC_USD=BTCUSD`;
  - `CHAINLINK_SYMBOL_ETH_USD=ETHUSD`;
  - `CHAINLINK_SYMBOL_SOL_USD=SOLUSD`.
- Реализован `ChainlinkClient`:
  - auth через `/api/v1/authorize`;
  - загрузка истории через `/api/v1/history/rows`;
  - нормализация Chainlink candle row `[time, open, high, low, close, volume]`;
  - масштабирование цены через `CHAINLINK_PRICE_DECIMALS`.
- Добавлен helper `feeds/symbols.py` для маппинга BTC/ETH/SOL на Chainlink symbols.
- Добавлен storage-слой для свечей:
  - сериализация candle/tick;
  - query builder;
  - upsert Chainlink candles без дублей.
- Реализован backfill CLI:
  - `python -m app.scripts.backfill`;
  - параметры `--asset`, `--from`, `--to`, `--days`, `--timeframe`, `--chunk-days`, `--dry-run`;
  - запись прогресса в `data_source_runs`;
  - checkpoint по последнему обработанному чанку.
- Реализованы реальные read endpoints:
  - `GET /api/candles`;
  - `GET /api/candles/{id}/drilldown`;
  - `GET /api/ticks`;
  - `GET /api/analysis/window`;
  - `GET /api/imbalances`.

### Проверено: Chainlink/backfill и read endpoints

- Проверен импорт FastAPI app:
  - `app import ok`.
- Проверен dry-run backfill:
  - `python -m app.scripts.backfill --dry-run --asset BTC --days 1`.
- Повторно выполнен seed assets для обновления `source_config`.
- FastAPI запускался локально на `http://127.0.0.1:8000`.
- Проверены endpoints:
  - `/api/status`;
  - `/api/assets`;
  - `/api/candles?asset=BTC&timeframe=5m`;
  - `/api/analysis/window?asset=BTC&window=3`.
- На пустой базе candles endpoint корректно возвращает пустой список.

### Добавлено

- Создана директория проекта `C:\Users\viach\OneDrive\Desktop\poly_crypto`.
- Добавлена базовая структура:
  - `apps/web` для Next.js frontend.
  - `services/api` для FastAPI backend.
  - `docs` для проектной документации.
- Добавлен backend на FastAPI:
  - app factory в `services/api/app/main.py`;
  - роуты `/api/status`, `/api/assets`;
  - заглушки будущих роутов `/api/candles`, `/api/ticks`, `/api/analysis/window`, `/api/imbalances`.
- Добавлена SQLite-first БД через SQLAlchemy:
  - `assets`;
  - `price_ticks`;
  - `candles`;
  - `imbalance_events`;
  - `data_source_runs`.
- Добавлена Alembic-миграция `0001_initial`.
- Добавлен seed-скрипт BTC/ETH/SOL.
- Добавлена граница интеграции с Chainlink:
  - `feeds/chainlink.py`;
  - `feeds/normalizer.py`.
- Добавлены начальные модули анализа:
  - расчет цвета свечи;
  - расчет window metrics;
  - базовый percent-move imbalance detector;
  - candle drilldown features;
  - базовая OHLC/gap validation.
- Добавлен минимальный Next.js frontend skeleton с русским интерфейсом.
- Добавлены документы:
  - `docs/data-sources.md`;
  - `docs/assumptions.md`;
  - `docs/implementation-plan.md`.
- Добавлен `.env.example` без API-ключей.
- Проектные тексты, README, UI и backend-сообщения переведены на русский там, где это не ломает технические контракты.

### Проверено

- Создан Python venv в `services/api/.venv`.
- Установлены backend-зависимости из `services/api/requirements.txt`.
- Создан локальный `services/api/.env` из `.env.example`.
- Прогнана миграция:
  - `python -m alembic upgrade head`.
- Выполнен seed:
  - `python -m app.scripts.seed_assets`.
- Создана локальная SQLite-база:
  - `services/api/poly_crypto.db`.
- FastAPI запускался локально на `http://127.0.0.1:8000`.
- Проверены endpoints:
  - `/api/status` вернул `ok`;
  - `/api/assets` вернул BTC, ETH, SOL.
- Проверено, что русские названия активов в БД сохранены корректно:
  - `Биткоин`;
  - `Эфириум`;
  - `Солана`.
- Проверен синтаксис Python-файлов:
  - `parsed 35 python files`.
- Проверены JSON-конфиги frontend:
  - `json ok`.

### Ограничения текущего состояния

- Chainlink endpoint еще не подключен к реальному API.
- Backfill пока является заглушкой.
- Realtime worker пока является заглушкой.
- Endpoints свечей, тиков, анализа и дисбалансов пока возвращают `501`.
- Frontend пока является визуальным skeleton без загрузки данных из API.
