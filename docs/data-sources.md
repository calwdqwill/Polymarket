# Источники данных

## Chainlink

Основной источник MVP для BTC/USD, ETH/USD и SOL/USD.

Текущая интеграция:

- Base URL: `https://priceapi.dataengine.chain.link`.
- Auth: `POST /api/v1/authorize`.
- History: `GET /api/v1/history/rows`.
- Формат строки свечи: `[time, open, high, low, close, volume]`.
- Масштаб цены задается через `CHAINLINK_PRICE_DECIMALS`; по умолчанию `18`.

Планируемое использование:

- Backfill 5-минутных OHLC-свечей примерно за последние 90 дней.
- Сохранение свечей в нормализованном виде в `candles`.
- Сохранение realtime-наблюдений цены в `price_ticks`, когда включен polling или streaming.
- Формирование и финализация live 5-минутных свечей из собранных тиков.

Известное ограничение: исторические candlestick-данные Chainlink дают OHLC-свечи, но не обязательно каждый raw tick внутри исторической свечи. Поэтому исторический drill-down строится по OHLC-признакам, если tick-level data недоступны в конкретном аккаунте/API-продукте.

## Polymarket

Polymarket важен как рыночный контекст, но в первом MVP не используется как price feed. CLOB prices - это цены outcome-токенов, а не spot-цены BTC/ETH/SOL. Правила resolution у Polymarket markets могут ссылаться на внешние источники данных, и их нужно проверять отдельно для каждого market.
