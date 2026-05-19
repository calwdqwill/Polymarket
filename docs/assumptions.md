# Допущения

- MVP отслеживает только price feed assets BTC, ETH и SOL.
- Timeframe MVP - только `5m`.
- База начинается с SQLite для быстрого локального запуска, но SQLAlchemy-модели остаются переносимыми на PostgreSQL/TimescaleDB.
- `volume` nullable, потому что Chainlink price feeds/candlesticks могут не давать биржевой объем.
- Исторический raw tick drill-down от Chainlink не гарантирован. Realtime tick drill-down начинается после запуска локального worker-а, который собирает тики.
