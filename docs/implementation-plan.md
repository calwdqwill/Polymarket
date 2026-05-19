# План реализации

## Уже сделано в скелете

- Структура папок проекта в `apps/web`, `services/api` и `docs`.
- FastAPI app factory и начальные routers.
- SQLite-first SQLAlchemy-модели.
- Alembic-миграция `0001_initial`.
- Seed-скрипт активов BTC, ETH и SOL.
- Граница интеграции с Chainlink.
- Начальные модули для candle features, validation и imbalance helpers.
- Chainlink Candlestick API client.
- Backfill CLI с dry-run, chunking и checkpoint.
- Read endpoints для свечей, тиков, drill-down, оконного анализа и imbalance events.

## Следующие инкременты

1. Заполнить Chainlink credentials и прогнать smoke backfill за 1 день BTC.
2. Проверить фактический масштаб цены и формат `volume` на реальном ответе Chainlink.
3. Прогнать backfill за 90 дней для BTC/ETH/SOL.
4. Добавить retry/backoff и обработку rate limits.
5. Добавить пересчет imbalance events после backfill.
6. Реализовать realtime worker и финализацию активной 5m свечи.
7. Собрать загрузку данных и графики для dashboard.
