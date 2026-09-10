# Крипто-дашборд

## Локальный shadow engine и журнал

Добавлен отдельный локальный consumer для `micro_arb_v0`: BTC 5m, Q10 retained, edge 10–15c, цель $0,50, независимые 100/250 ms. [Полный контракт, результаты и ограничения](LIVE_SHADOW_ENGINE_DESIGN.md). Он не подключён к VPS и не читает `live.json` как источник событий. Orders и dashboard этой итерацией не запускаются.

Из `services/api`, только на локальных historical данных, в новый output-каталог:

```powershell
.venv/Scripts/python.exe -m app.scripts.replay_prediction_shadow ../../target-profit-research-v1 data/prediction/recovery-20260910/dataset ../../shadow-engine-v1-new --workers 3
.venv/Scripts/python.exe -m app.scripts.verify_prediction_shadow ../../shadow-engine-v1-new
.venv/Scripts/python.exe -m app.scripts.audit_prediction_shadow ../../target-profit-research-v1 ../../shadow-engine-v1-new
```

Каждую следующую команду выполнять после успешного завершения предыдущей. Для smoke — `--limit 1`; это не полный dataset gate. Полный replay дважды воспроизводит каждое разрешённое окно, сверяет hashes и frozen reference V1. Зависимости/env/основная SQLite не меняются. JSONL хранит audit попыток и окон, а raw остаётся в исходном storage. Новое изменение `.env` не требуется.

## Linux shared staging — актуальный результат 10 сентября 2026

Завершены 30m smoke и 2h collector-only на VPS: **PASS_WITH_LIMITS** для отдельно разрешаемого 24h. [Отчёт с coverage, lag, ресурсами и replay](LIVE_SHADOW_DEPLOYMENT_REPORT.md), [Linux runbook](docs/prediction-linux-runbook.md). Все prediction services/timer остановлены; 24h, shadow и orders не запускались.

Текущий Linux launcher намеренно ограничен 7200 s; увеличение длительности требует отдельного задания и свежего preflight. Перцентили receive lag не сохраняются, а health соседей нестабилен в baseline: инфраструктурный verdict не является разрешением торгового исполнения. Старые команды и этапы ниже приведены как история/справка.

MVP-дашборд для анализа 5-минутных свечей BTC, ETH и SOL. Основной источник ценовых данных в текущей версии - Chainlink.

Проект находится в MVP-стадии: backend поднимается локально и на VPS, база создается через Alembic, активы BTC/ETH/SOL сидятся в SQLite, а frontend показывает рабочий dark dashboard. Chainlink Streams realtime polling пишет тики и локальные 5-минутные свечи; historical backfill через Chainlink Candlestick API остается заблокированным отдельной авторизацией.

## Текущее состояние

Готово:

- Структура проекта `apps/web`, `services/api`, `docs`.
- Backend на FastAPI.
- SQLite-first база через SQLAlchemy.
- Alembic-миграция `0001_initial`.
- Таблицы:
  - `assets`;
  - `price_ticks`;
  - `candles`;
  - `imbalance_events`;
  - `data_source_runs`.
- Seed-скрипт для BTC / ETH / SOL.
- Chainlink client для Candlestick API.
- Backfill CLI для загрузки 5m свечей.
- HMAC-клиент Chainlink Streams для realtime latest reports.
- Polling CLI для записи BTC/ETH/SOL realtime ticks в `price_ticks` и 5m свечей в `candles`.
- Базовые endpoints:
  - `GET /api/status`;
  - `GET /api/status/sources`;
  - `GET /api/assets`.
- Реальные read endpoints:
  - `GET /api/candles`;
  - `GET /api/ticks`;
  - `GET /api/candles/{id}/drilldown`;
  - `GET /api/analysis/window`;
  - `GET /api/imbalances`.
- Imbalance detection:
  - batch-пересчет через `python -m app.scripts.recalculate_imbalances`;
  - запись событий в `imbalance_events`;
  - API-фильтры по `source`, `event_type`, `direction`, `min_severity`, `window_size`.
- Рабочий Next.js frontend dashboard:
  - overview cards BTC/ETH/SOL;
  - candlestick chart через `lightweight-charts`;
  - таблицы последних свечей и imbalance events;
  - фильтры asset/source/period/direction/event type/severity/window;
  - heatmap дисбалансов;
  - candle drill-down;
  - экспорт CSV/JSON.
- Frontend tooling:
  - `npm run lint`;
  - `npm run test`;
  - `npm run build`.
- Hardening/check tooling:
  - backend test suite - 24 теста;
  - `ops/check-local.ps1` - единый локальный predeploy-check;
  - `pyproject.toml` - базовая Ruff-конфигурация;
  - `docs/deployment-runbook.md` - гайд по Git/server deploy;
  - `ops/linux/*.service.example` - systemd-шаблоны для API, frontend и Chainlink worker;
  - `ops/linux/poly-crypto-nginx-8080.conf.example` - Nginx-шаблон для IP-доступа без домена.
- Серверный деплой:
  - VPS `155.212.183.185`;
  - dashboard доступен на `http://155.212.183.185:8080`;
  - `poly-crypto-api.service` слушает `127.0.0.1:18000`;
  - `poly-crypto-web.service` слушает `127.0.0.1:13000`;
  - `poly-crypto-chainlink-worker.service` собирает Chainlink Streams 24/7;
  - `poly-crypto-db-backup.timer` делает ежечасный SQLite backup и хранит последние 48 копий.
- Документация по источникам и допущениям.

Пока не готово:

- Historical backfill через Chainlink Candlestick API: текущие Streams credentials дают `401 Unauthorized` на `/api/v1/authorize`.
- Historical слой на 90 дней из Polymarket: нужно выбрать markets/token ids и хранить их отдельно от spot candles.
- Внешний backup: ежечасный SQLite backup уже включен на VPS, но отдельного внешнего хранилища для бэкапов пока нет.
- Сохранение frontend-фильтров в URL/localStorage, pagination/virtualization таблиц и дополнительные UX-режимы сравнения источников.

## Структура

```text
poly_crypto/
  apps/
    web/                    # Next.js frontend
  services/
    api/                    # FastAPI backend
      app/
        api/routes/         # HTTP endpoints
        analysis/           # window metrics, imbalance detection, drilldown
        candles/            # candle builder, aggregation, validation
        core/               # settings/config
        db/                 # SQLAlchemy models, session, Alembic migrations
        feeds/              # Chainlink clients and normalizers
        scripts/            # seed/backfill/validation/polling scripts
        workers/            # realtime worker placeholders
  docs/
```

## Backend: быстрый запуск

```powershell
cd C:\Users\viach\OneDrive\Desktop\poly_crypto\services\api
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

Если окружение нужно создать заново:

```powershell
cd C:\Users\viach\OneDrive\Desktop\poly_crypto\services\api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy ..\..\.env.example .env
alembic upgrade head
python -m app.scripts.seed_assets
uvicorn app.main:app --reload
```

Проверка статуса:

```text
http://127.0.0.1:8000/api/status
```

Список активов:

```text
http://127.0.0.1:8000/api/assets
```

Свечи BTC:

```text
http://127.0.0.1:8000/api/candles?asset=BTC&timeframe=5m
```

Оконный анализ BTC:

```text
http://127.0.0.1:8000/api/analysis/window?asset=BTC&window=10
```

## Локальная проверка перед Git/server

Полный predeploy-check:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\check-local.ps1
```

Команда проверяет:

- backend compile;
- backend unit/API tests;
- frontend lint;
- frontend typecheck;
- frontend production build;
- optional API health, если backend уже запущен на `127.0.0.1:8000`.

Если Next.js build в Codex sandbox падает на Windows `spawn EPERM`, запусти команду вне sandbox или отдельно:

```powershell
cd C:\Users\viach\OneDrive\Desktop\poly_crypto\apps\web
npm run build
```

## Chainlink backfill

Перед реальным backfill заполни в `services/api/.env`:

```text
CHAINLINK_USER_ID=
CHAINLINK_API_KEY=
CHAINLINK_PRICE_DECIMALS=18
CHAINLINK_SYMBOL_BTC_USD=BTCUSD
CHAINLINK_SYMBOL_ETH_USD=ETHUSD
CHAINLINK_SYMBOL_SOL_USD=SOLUSD
CHAINLINK_STREAMS_BASE_URL=https://api.dataengine.chain.link
CHAINLINK_FEED_BTC_USDT=
CHAINLINK_FEED_ETH_USDT=
CHAINLINK_FEED_SOL_USD=
```

Realtime Streams использует feedID для BTC/USDT, ETH/USDT и SOL/USD. Значения хранятся только в `.env`; API keys не хардкодим.

Проверить план без запроса к Chainlink:

```powershell
cd C:\Users\viach\OneDrive\Desktop\poly_crypto\services\api
.\.venv\Scripts\Activate.ps1
python -m app.scripts.backfill --dry-run --asset BTC --days 1
```

Smoke backfill за 1 день BTC:

```powershell
python -m app.scripts.backfill --asset BTC --days 1
```

Smoke-проверка фазы 4 с автоматическими DB-checks и повторным запуском для проверки отсутствия дублей:

```powershell
python -m app.scripts.smoke_backfill --asset BTC --days 1
```

Проверка HMAC Chainlink Streams latest report:

```powershell
python -m app.scripts.check_chainlink_streams_auth --asset SOL
```

Проверить все настроенные feedID:

```powershell
python -m app.scripts.check_chainlink_streams_auth --asset BTC
python -m app.scripts.check_chainlink_streams_auth --asset ETH
python -m app.scripts.check_chainlink_streams_auth --asset SOL
```

Один realtime-poll Chainlink Streams и запись ticks/5m candles:

```powershell
python -m app.scripts.poll_chainlink_streams --once --asset SOL
```

Непрерывный polling каждые 10 секунд:

```powershell
python -m app.scripts.poll_chainlink_streams --asset all --interval 10
```

Локальный Windows watchdog для накопления live-истории без открытого терминала:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\windows\register-chainlink-worker-task.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\windows\status-chainlink-worker-task.ps1
```

Если Task Scheduler недоступен, скрипт создает Startup launcher и запускает hidden watchdog. Watchdog держит worker живым, пишет лог в `services/api/logs` и защищен single-instance mutex-ом от повторного запуска. При старте worker пересобирает `chainlink_streams` свечи из уже сохраненных `price_ticks`, чтобы не терять честно собранную live-историю из-за локального сбоя агрегации.

Полезные параметры hardened worker:

```powershell
python -m app.scripts.poll_chainlink_streams --asset all --interval 10 --retry-attempts 5 --retry-initial-delay 1 --retry-max-delay 30
python -m app.scripts.poll_chainlink_streams --asset all --iterations 3
python -m app.scripts.poll_chainlink_streams --asset all --once --no-recalculate-imbalances
```

При переходе в новое 5m окно worker считает предыдущую realtime-свечу закрытой, диагностирует пропуски окон и пересчитывает свежие `imbalance_events` для `source = chainlink_streams`.

Backfill за 90 дней по всем активам:

```powershell
python -m app.scripts.backfill --asset all --days 90
```

Binance historical fallback, если Chainlink Candlestick API недоступен:

```powershell
python -m app.scripts.backfill_binance --asset all --days 90
```

Для более длинного периода:

```powershell
python -m app.scripts.backfill_binance --asset all --years 5
```

Валидация свечей после backfill:

```powershell
python -m app.scripts.validate_candles --asset all --days 90
```

Валидация Binance historical fallback за точный загруженный диапазон:

```powershell
python -m app.scripts.validate_candles --asset all --source binance_klines --from 2026-02-17T20:00:00Z --to 2026-05-18T20:00:00Z
```

Пересчет imbalance events по Binance historical fallback:

```powershell
python -m app.scripts.recalculate_imbalances --asset all --source binance_klines --from 2026-02-17T20:00:00Z --to 2026-05-18T20:00:00Z
```

Примеры чтения найденных событий:

```text
http://127.0.0.1:8000/api/imbalances?asset=BTC&source=binance_klines
http://127.0.0.1:8000/api/imbalances?asset=BTC&source=binance_klines&event_type=window_percent_move
http://127.0.0.1:8000/api/imbalances?asset=ETH&source=binance_klines&min_severity=3
```

Проверка всех локальных источников свечей, включая realtime `chainlink_streams`:

```powershell
python -m app.scripts.validate_candles --asset all --days 90 --source all
```

## Проверенный результат

На текущем этапе проверено:

- venv создан в `services/api/.venv`.
- зависимости backend установлены;
- локальный `.env` создан в `services/api/.env`;
- `alembic upgrade head` прошел успешно;
- `python -m app.scripts.seed_assets` прошел успешно;
- API запускался на `http://127.0.0.1:8000`;
- `/api/status` вернул `ok`;
- `/api/assets` вернул BTC, ETH, SOL;
- `/api/candles?asset=BTC&timeframe=5m` корректно отвечает на пустой базе;
- `/api/analysis/window?asset=BTC&window=3` корректно отвечает на пустой базе;
- `python -m app.scripts.backfill --dry-run --asset BTC --days 1` отрабатывает без Chainlink credentials;
- русские названия активов в БД сохранены корректно;
- Python-файлы синтаксически проверены;
- JSON-конфиги frontend валидны.
- `npm run lint` проходит.
- `npm run test` проходит как TypeScript typecheck (`tsc --noEmit`).
- `npm run build` проходит.
- Миграции применяются с нуля на отдельной SQLite-базе `migration_smoke.db`.
- Backend локально стартует и отвечает `/api/status`.
- Frontend production-start локально стартует на `http://127.0.0.1:3000` и возвращает HTTP 200.
- Chainlink Streams HMAC credentials работают для BTC/USDT, ETH/USDT и SOL/USD.
- `python -m app.scripts.poll_chainlink_streams --once --asset all` пишет realtime ticks в `price_ticks`.
- Realtime ticks агрегируются в 5m candles с `source = chainlink_streams`.
- Добавлен `python -m app.scripts.validate_candles --asset all --days 90`.
- `python -m app.scripts.backfill --dry-run --asset all --days 90` показывает 18 чанков на каждый актив.
- Реальный 90-day Chainlink Candlestick backfill доходит до внешнего API, но `/api/v1/authorize` возвращает `401 Unauthorized`.
- Добавлен Binance historical fallback:
  - `python -m app.scripts.backfill_binance --asset all --days 90`;
  - `source = binance_klines`;
  - поддержаны `--days`, `--years`, `--from`, `--to`;
  - добавлен retry/backoff на сетевые сбои.
- Загружено по 25 920 5m candles за 90 дней по BTC/ETH/SOL с `source = binance_klines`.
- Повторный Binance backfill за тот же диапазон вставил 0 новых строк и обновил существующие свечи.
- `validate_candles` по Binance-диапазону проходит без ошибок и warnings.
- Historical candles с `source = chainlink_candlestick` за 90 дней сейчас отсутствуют; realtime candles с `source = chainlink_streams` продолжаем копить.
- Реализована фаза 6 imbalance detection:
  - BTC: 4 580 событий;
  - ETH: 4 232 события;
  - SOL: 3 236 событий;
  - всего: 12 048 событий по Binance fallback окну `2026-02-17T20:00:00Z..2026-05-18T20:00:00Z`.
- Повторный пересчет BTC за то же окно удаляет старые события и вставляет новые, не накапливая дубли.
- Реализована фаза 7 realtime worker hardening:
  - `app.workers.realtime_worker`;
  - retry/backoff и логирование временных ошибок Chainlink Streams;
  - active candle state;
  - диагностика gap-ов между 5m realtime candles;
  - implicit finalization закрытой свечи при переходе в новое окно;
  - пересчет свежих `imbalance_events` по закрытым realtime-свечам.
- One-shot Chainlink Streams polling после фазы 7 получил HTTP 200 по BTC/ETH/SOL, записал новые ticks и обновил realtime candles.

## Frontend

Frontend dashboard находится в:

```text
apps/web
```

Dashboard подключается к backend через `NEXT_PUBLIC_API_BASE_URL` и использует текущие read endpoints: `assets`, `candles`, `analysis/window`, `imbalances` и `candles/{id}/drilldown`.
Также frontend читает `GET /api/status/sources`, чтобы показывать состояние источников: накопленную локальную историю Chainlink Streams, свежесть worker-а и блокировку Chainlink Candlestick API из-за `401`.

Гайд по индикаторам dashboard:

```text
docs/dashboard-indicators-guide.md
```

Короткая версия гайда также доступна в раскрываемой вкладке `Гайд по индикаторам` внутри dashboard.

Локальный запуск:

```powershell
cd C:\Users\viach\OneDrive\Desktop\poly_crypto\apps\web
npm install
npm run dev
```

Проверки frontend:

```powershell
npm run lint
npm run test
npm run build
```

Production-start после build:

```powershell
npm run start -- -p 3000
```

## Политика источников данных

Для MVP приоритетный источник realtime - Chainlink, historical fallback - Binance:

- Historical Chainlink backfill: Chainlink Data Streams Candlestick API, `5m`, запросы чанками. Сейчас заблокирован `401 Unauthorized` на `/api/v1/authorize`.
- Historical fallback: Binance public Spot klines, `GET /api/v3/klines`, `source = binance_klines`.
- Chainlink API base URL: `https://priceapi.dataengine.chain.link`.
- Chainlink history endpoint: `/api/v1/history/rows`.
- Chainlink auth endpoint: `/api/v1/authorize`.
- Realtime: Chainlink streaming или polling, в зависимости от доступности в выданных API credentials.
- Realtime MVP: Chainlink Data Streams HMAC `reports/latest`, polling каждые 10 секунд, локальная агрегация в 5m candles.
- Важно: `chainlink_streams` - это локально накопленная realtime-история только за время непрерывной работы worker-а. Пропущенные 5m окна нельзя восстановить через latest-report endpoint; для этого нужен рабочий Chainlink Candlestick historical доступ или fallback-источник.
- `volume` nullable, потому что Chainlink может не предоставлять биржевой объем.
- Исторический drill-down пока OHLC-only, если Chainlink не предоставляет raw ticks для выбранного интервала.
- Realtime drill-down сможет использовать локально собранные `price_ticks`.
- API свечей и оконного анализа поддерживает фильтр `source`, например `source=binance_klines`.

API-ключи не хардкодим. Credentials настраиваются в `.env`.

## Документация

- `CHANGELOG.md` - что уже сделано.
- `BACKLOG.md` - техдолг и следующие задачи.
- `ARCHITECTURE.md` - архитектура проекта.
- `PROJECT_PLAN.md` - план проекта по фазам.
- `docs/data-sources.md` - источники данных и ограничения.
- `docs/assumptions.md` - принятые допущения.
- `docs/implementation-plan.md` - план реализации.

## Следующий шаг

Фазы 0-9 закрыты в MVP-объеме, код выгружен в Git, а серверный MVP запущен на VPS:

```text
http://155.212.183.185:8080
```

Серверные проверки:

```bash
systemctl status poly-crypto-api
systemctl status poly-crypto-web
systemctl status poly-crypto-chainlink-worker
systemctl status poly-crypto-db-backup.timer
curl http://127.0.0.1:18000/api/status/sources
```

Локальный Windows watchdog можно оставить как резервный источник наблюдения, но основной сбор `chainlink_streams` теперь должен идти на VPS через `systemd`.

Следующий продуктовый/данный шаг: получить Chainlink Candlestick API credentials, дозагрузить `source = chainlink_candlestick` и сравнить его с `source = binance_klines`. Отдельный будущий слой Polymarket CLOB/outcome-token истории нужно проектировать отдельно от spot/oracle candles.
