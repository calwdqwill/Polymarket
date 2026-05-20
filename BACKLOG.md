# Backlog

Техдолг и следующие задачи по MVP.

## P0 - ближайший этап

- Разобраться с Chainlink Candlestick credentials:
  - текущие Streams HMAC credentials работают для `/api/v1/reports/latest`;
  - `/api/v1/authorize` для Candlestick API возвращает `401 Unauthorized`;
  - нужен отдельный/разрешенный API key для historical Candlestick API.
- После получения Candlestick credentials дозагрузить/сравнить Chainlink historical candles:
  - запустить `python -m app.scripts.backfill --asset all --days 90`;
  - прогнать `python -m app.scripts.validate_candles --asset all --source chainlink_candlestick --days 90`;
  - сравнить расхождения с Binance `source = binance_klines`.
- Настроить внешний backup:
  - ежечасный SQLite backup уже включен на VPS и хранит последние 48 копий;
  - следующий надежный шаг - копировать backup во внешнее хранилище или на второй сервер.

Для MVP других P0-блокеров нет: dashboard, API, fallback history, realtime accumulation, VPS/systemd, Git и check-команды готовы.

## P0 - уже закрыто

- Подключена базовая auth-схема Chainlink Data Streams/DataEngine.
- Уточнен основной endpoint path для Chainlink candlestick history.
- Реализован `ChainlinkClient.fetch_candles`.
- Реализован backfill CLI с `--dry-run`, чанками и checkpoint.
- Реализован upsert historical candles в таблицу `candles`.
- Реализованы реальные read endpoints:
  - `GET /api/candles`;
  - `GET /api/candles/{id}/drilldown`;
  - `GET /api/ticks`;
  - `GET /api/analysis/window`;
  - `GET /api/imbalances`.
- Настроены frontend quality scripts:
  - `npm run lint`;
  - `npm run test`;
  - `npm run build`.
- Проверены миграции с нуля на отдельной SQLite-базе.
- Проверен локальный старт backend и frontend.
- Реализован HMAC-клиент Chainlink Data Streams:
  - signed headers;
  - latest report;
  - decoder report v3.
- Добавлен realtime polling:
  - `python -m app.scripts.poll_chainlink_streams --once --asset all`;
  - `source = chainlink_streams`;
  - запись в `price_ticks`;
  - агрегация в 5m `candles`.
- Проверены feedID BTC/USDT, ETH/USDT, SOL/USD.
- One-shot polling записал BTC/ETH/SOL ticks и обновил текущие 5m candles.
- Реализован validator command:
  - `python -m app.scripts.validate_candles --asset all --days 90`;
  - gaps;
  - duplicates;
  - OHLC consistency;
  - source/timestamp checks.
- Реализован Binance historical fallback:
  - `python -m app.scripts.backfill_binance --asset all --days 90`;
  - `source = binance_klines`;
  - параметры `--days`, `--years`, `--from`, `--to`;
  - retry/backoff для сетевых сбоев;
  - 90 дней BTC/ETH/SOL загружены и провалидированы без критических ошибок.
- Реализован пересчет imbalance events после backfill:
  - `python -m app.scripts.recalculate_imbalances --asset all --source binance_klines --from 2026-02-17T20:00:00Z --to 2026-05-18T20:00:00Z`;
  - percent move, streaks, body/wick/body-range, rolling mean/median, z-score и percentile detectors;
  - `GET /api/imbalances` фильтрует `source`, `event_type`, `min_severity` и `window_size`;
  - в `imbalance_events` записано 12 048 событий по BTC/ETH/SOL.
- Реализован hardened realtime worker для фазы 7:
  - `app.workers.realtime_worker`;
  - совместимая команда `python -m app.scripts.poll_chainlink_streams`;
  - retry/backoff и логирование временных ошибок Chainlink Streams;
  - active candle state по asset;
  - диагностика пропущенных 5m realtime окон;
  - implicit finalization закрытой свечи при переходе в новое окно;
  - автоматический пересчет свежих `imbalance_events` для `source = chainlink_streams`.
- Реализован frontend dashboard для фазы 8:
  - подключение к backend API;
  - source health banner через `GET /api/status/sources`;
  - overview cards BTC/ETH/SOL;
  - candlestick chart через `lightweight-charts`;
  - таблицы последних свечей и imbalance events;
  - фильтры asset/source/period/direction/event type/severity/window;
  - heatmap дисбалансов;
  - candle drill-down;
  - экспорт CSV/JSON.
- Добавлен локальный Windows supervisor для Chainlink Streams worker:
  - `ops/windows/run-chainlink-worker.ps1`;
  - `ops/windows/register-chainlink-worker-task.ps1`;
  - `ops/windows/status-chainlink-worker-task.ps1`;
  - `ops/windows/unregister-chainlink-worker-task.ps1`;
  - гайд `docs/chainlink-worker-service-guide.md`.
- Усилено сохранение Chainlink live-истории:
  - realtime candles пересобираются из сохраненных `price_ticks` при старте worker-а;
  - Windows watchdog защищен single-instance mutex-ом от повторных launcher-запусков;
  - текущая база вручную пересобрана из live ticks без искусственного заполнения пропущенных окон.
- Закрыта фаза 9 hardening в MVP-объеме:
  - добавлены API-тесты для dashboard endpoints;
  - backend test suite расширен до 24 тестов;
  - добавлен `ops/check-local.ps1`;
  - добавлен `pyproject.toml` с Ruff-конфигурацией;
  - добавлен `services/api/requirements-dev.txt`;
  - добавлен `docs/deployment-runbook.md`;
  - добавлены systemd-шаблоны для API и Chainlink worker;
  - `npm audit --audit-level=moderate` показывает `0 vulnerabilities`.
- MVP выгружен на VPS `155.212.183.185`:
  - внешний dashboard: `http://155.212.183.185:8080`;
  - `poly-crypto-api.service` активен на `127.0.0.1:18000`;
  - `poly-crypto-web.service` активен на `127.0.0.1:13000`;
  - `poly-crypto-chainlink-worker.service` активен и пишет Chainlink Streams ticks;
  - `poly-crypto-db-backup.timer` активен и делает ежечасный SQLite backup.
- Выполнена контрольная проверка локального состояния после фазы 8:
  - `npm run lint`;
  - `npm run test`;
  - `npm run build`;
  - backend и frontend local start;
  - браузерная проверка dashboard на `http://localhost:3000`.
- Выполнена контрольная проверка локального состояния после фазы 6:
  - frontend `lint`, `test`, `build`;
  - backend compile/unit tests;
  - fresh Alembic migration;
  - backend и frontend local start с HTTP 200.

## P1 - аналитика и надежность

- Добавить Pydantic-схемы request/response.
- Добавить полноценную сортировку и pagination для свечей и тиков.
- Добавить полноценный candle drill-down:
  - OHLC features для исторических свечей;
  - tick list для realtime-свечей, если тики собраны локально.
- Настроить пороги imbalance engine на реальном использовании dashboard, если текущая выборка окажется слишком шумной или слишком редкой.
- Расширить наблюдаемость источников данных: метрики ошибок, длительность запросов, счетчики retry/backoff.
- Улучшить frontend UX после реального использования:
  - добавить сохранение выбранных фильтров в URL/localStorage;
  - добавить pagination/virtualization для длинных таблиц;
  - добавить отдельный режим сравнения `binance_klines` и `chainlink_streams`;
  - добавить более тонкое управление markers на графике, если событий слишком много.

## P2 - frontend dashboard: закрыто в MVP

- Подключен frontend к backend API.
- Добавлены overview cards BTC/ETH/SOL.
- Добавлена таблица последних свечей.
- Добавлен candlestick chart через `lightweight-charts`.
- Добавлена таблица imbalance events.
- Добавлены фильтры:
  - asset;
  - source;
  - период;
  - window size;
  - severity threshold;
  - direction;
  - event type.
- Добавлен drill-down по клику на свечу/строку таблицы.
- Добавлен экспорт CSV/JSON.

## P3 - инфраструктура и качество

- Разобрать свежий `npm audit` на сервере:
  - после серверного `npm ci` registry показывает 2 moderate warnings по `postcss < 8.5.10` через `next@15.5.18`;
  - автоматический `npm audit fix --force` не применять без проверки, потому что он предлагает breaking change.
- Расширить frontend-тесты: сейчас `npm run test` является typecheck-командой, а не набором unit/UI-тестов.
- Добавить больше API/integration tests для edge-cases:
  - пустые выборки;
  - некорректные query-параметры;
  - большие лимиты и pagination;
  - ошибки внешних источников.
- Подключить Ruff в CI после появления Git remote.
- Добавить опциональную PostgreSQL/TimescaleDB конфигурацию.
- Добавить Docker только после стабилизации локального MVP, если понадобится.

## Известный техдолг

- Сейчас БД SQLite, хотя целевая production-friendly схема лучше ляжет на PostgreSQL/TimescaleDB.
- В миграции и моделях технические имена таблиц/полей оставлены на английском, чтобы не ломать API/ORM-конвенции.
- `volume` nullable, потому что Chainlink может не давать биржевой объем.
- Исторический tick-level drill-down через Chainlink пока не гарантирован.
- PowerShell может некорректно отображать русский текст в терминале, хотя данные в UTF-8 и БД сохраняются корректно.
