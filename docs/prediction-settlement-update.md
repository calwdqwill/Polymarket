# BTC 5m: дополнительная проверка settlement

## Дополнение стабилизации, 9 сентября 2026, 15:19 UTC

В официальном HTML [Polymarket BTC 5m 15:15–15:20 UTC](https://polymarket.com/event/btc-updown-5m-1788966900) найден SSR query `crypto-prices/price/BTC/2026-09-09T15:15:00Z/fiveminute/2026-09-09T15:20:00Z/true/60` с `openPrice=78474.82624106224`. Это числовой literal интерфейса, без подтверждённой точности settlement. В [Limitless metadata того же окна](https://api.limitless.exchange/markets/btc-up-or-down-5-min-1788966900) — строка `78474.826241062247333888`, `openPriceCapturedAt` и `openPriceSelectedAt` равны 15:15:00 UTC.

Отображаемый Polymarket strike теперь имеет конкретный официальный источник. Точный сравниваемый strike и алгоритм округления ещё не установлены; округлённый UI literal не передан в matcher. Повторные Gamma market/event ответы не раскрыли точный feed ID, boundary report и fallback. Каталог Chainlink при прямом HTTP-запросе ответил 429; этот ответ не доказывает недоступность feed. Общая документация TWAP снова указывает, что sampling boundaries, weighting, rounding и missing-input behavior custom feed не опубликованы. Cancellation и market-specific redemption equivalence по-прежнему UNKNOWN.

Источники, ответы HTTP и SHA-256 сохранены в `services/api/data/prediction/protocol-research/`; привязка UI literal — `settlement-ui-strike.json`, raw ответы — `settlement-1788966900-*.txt`, копии документации — `sources.json` и HTML рядом. Запрос нового Chainlink reference layer не выполнялся; contemporaneous reference для distance-to-strike в L2 dataset отсутствует. Все эти уточнения не блокировали collection и не повышают settlement quality автоматически.

Дата проверки: 9 сентября 2026 года. Область: ограниченный поиск официальных публичных источников и один актуальный Gamma metadata-запрос. Приватные API, кошельки, ордера и новые потоки Chainlink не использовались.

**Вывод: settlement equivalence остаётся UNKNOWN.** Новые сведения уточняют представление цены и redemption, но не доказывают одинаковые исходы Polymarket и Limitless во всех пограничных случаях. Исследование не блокирует разрешённое пользователем наблюдение публичного L2: положительный gross при UNKNOWN следует обозначать как кандидат с неподтверждённым settlement, а не доказанный арбитраж.

## Polymarket: фактический запрос

Публичный [Gamma-запрос по slug](https://gamma-api.polymarket.com/markets?slug=btc-updown-5m-1788961800) вернул рынок `4364486`, окно `2026-09-09T13:50:00Z` — `13:55:00Z`, condition ID `0x3cea2e0d02dec034951143db76fc5ae2be1e96c97343e84798b3a59ec6a8f322`.

В полученном ответе:

- `outcomes=[Up, Down]`; правило относит равенство к Up.
- `resolutionSource=https://data.chain.link/streams/btc-usd-twap-60s-streams`.
- `cryptoMarketConfigId=btc-5m-twap-60`, `twapEnabled=true`, `twapLookbackSeconds=60`.
- Числовой strike, feed ID, точный алгоритм выбора начального/конечного report, fallback и cancellation не раскрыты. Вложенный event также не дал этих полей.

Это наблюдение конкретного ответа, а не утверждение об отсутствии данных во всех API или интерфейсах. Ответ динамический; исходный payload не записывался отдельным файлом в рамках этой документальной подзадачи. Предыдущий снимок и детали Limitless изложены в [первом probe](prediction-first-probe.md).

## Доказательства и оставшиеся пробелы

| Предмет | Что установлено сейчас | Что ещё требуется |
|---|---|---|
| Strike | Актуальный Gamma-ответ его не раскрыл | Официальное значение начальной цены для каждого сравниваемого окна и provenance |
| Feed ID | Market-specific URL указывает BTC/USD TWAP 60s | Подтверждённая привязка Polymarket к тому же точному feed ID, что у Limitless |
| Boundary selection | RTDS различает observation time и время публикации | Правило выбора report на обеих границах, включая поздние/повторные reports |
| Fallback | Полный контракт BTC 5m не найден в проверенных источниках | Поведение при отсутствии observation, допустимая задержка и дальнейшее разрешение |
| Precision | Документация транспорта описывает точное E18 | Market-specific сравнение и rounding; формат передачи сам по себе этого не доказывает |
| Cancellation | Общее руководство требует учитывать правила рынка | Явное поведение именно BTC 5m при невозможности разрешения, аннулировании или споре |
| Redemption | Общая документация указывает pUSD | Привязка конкретного condition/token ID к collateral и сравнение с выплатой Limitless |

[Официальное руководство Chainlink TWAP в Polymarket](https://docs.polymarket.com/market-data/chainlink-twap) документирует `full_accuracy_value` как signed E18, `payload.timestamp` как observation time и отсутствие snapshot/history/replay в RTDS. Оно прямо сообщает, что параметры custom feed — sampling boundaries, weighting, rounding и missing-input behavior — не опубликованы. Поэтому нельзя самостоятельно восстановить settlement по частоте ticks или округлённому экранному `value`. Выбор 60 секунд обозначает lookback, а не период публикации. Описанный в руководстве самостоятельный выбор freshness/fallback относится к интегратору и не является правилом разрешения BTC 5m.

Повторное открытие [каталога BTC/USD TWAP 60s Chainlink](https://data.chain.link/streams/btc-usd-twap-60s-streams) через web-инструмент завершилось `Internal Error`. Это ограничение данной проверки; оно ничего не доказывает о работоспособности feed. Feed ID из предыдущего Limitless payload нельзя автоматически приписывать Polymarket только по совпавшему URL.

[Общая документация resolution](https://docs.polymarket.com/concepts/resolution) описывает UMA, возможность спора и приоритет правил конкретного рынка. Она не закрывает перечисленные BTC 5m пробелы. Общий процесс UMA не следует без дополнительной привязки превращать в доказанный market-specific путь этого condition.

[Текущая документация управления позициями](https://docs.polymarket.com/trading/positions/manage) описывает выплату 1 pUSD за выигравший токен и 0 за проигравший после разрешения, без срока окончания redemption. В примерах pUSD имеет адрес `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`; операции проходят через collateral adapter. Следовательно, обозначение обеих выплат просто как «USDC» недостаточно для экономической эквивалентности. Здесь не проверены on-chain привязка конкретного рынка и условия конвертации collateral; вывод NOT_EQUIVALENT также не установлен.

## DexSport

Просмотрены [официальная документация](https://dexsport.io/docs-home/), [объяснение prediction markets](https://dexsport.io/academy-articles/dexsport-prediction-markets-explained/) и [обзор продукта](https://dexsport.io/academy-articles/dexsport-prediction-market-review/). Продуктовые страницы описывают Yes/No, stablecoin stake, выплату победившим позициям и возможность продажи до исхода. Эти описания не подтверждают независимую исполнимую CLOB-книгу.

| Возможность | Результат ограниченной проверки |
|---|---|
| Публичный discovery/market-data API | Документированный endpoint и схема не найдены |
| CLOB/depth | Price/size levels, единицы объёма и семантика книги не подтверждены |
| Executable quotes | Не найдены официальные правила срока жизни quote, исполнения и доступного размера |
| Публичный WebSocket | Endpoint, подписка, snapshots/deltas, sequence/reconnect и auth requirements не подтверждены |
| BTC 5m settlement | Не найден полный контракт feed/strike/boundaries/fallback |

**Вердикт adapter: UNKNOWN.** Отсутствие находки в ограниченном поиске не означает отсутствия API у площадки. Для реализации нужен официальный протокол и фактический read-only snapshot/stream; карточка с вероятностью или ранней продажей не заменяет depth. Adapter и имитация книги не создавались.

## Следующее доказательство

Для повышения статуса нужны market-specific strike и точная идентификация feed, одинаковый алгоритм выбора boundary reports, rounding/fallback/cancellation и подтверждённые выплаты. Совпадение победителей нескольких окон является наблюдением, но не доказательством EXACT. До закрытия этих пунктов L2-измерения допустимы как исследование цен/глубины с явной пометкой UNKNOWN; execution и утверждение гарантированного edge из них не следуют.

## Дополнительная проверка единиц Limitless L2

**Текущий официальный web UI подтверждает: raw `size` книги переводится в shares с помощью decimals collateral.** Проверены локальные копии и повторно прочитаны соответствующие публичные JavaScript-ресурсы сайта; код не исполнялся.

- [Ресурс `2hhi21bhj-sgz.js`](https://limitless.exchange/_next/static/immutable/chunks/2hhi21bhj-sgz.js): расчёт preview покупки проходит по `levels` и преобразует `i.size` через `formatUnits(BigInt(Math.round(i.size)), n)`, где `n` — входной `decimals`. Результат используется как доступное число контрактов уровня.
- [Ресурс `120xbaigdo0wt.js`](https://limitless.exchange/_next/static/immutable/chunks/120xbaigdo0wt.js): отображение размера и cumulative depth используют `formatUnits(BigInt(e.size), q?.collateralToken.decimals || 6)`; денежная глубина получается умножением преобразованного размера на цену.

Для проверяемых рынков с `collateralToken.decimals=6`: `50000000` → 50 shares, `1000000000` → 1000 shares. Нормализация в аналитическом adapter должна использовать `Decimal(raw_size) / 10**decimals`, сохраняя raw и источник decimals; копировать промежуточные float/`Math.round` из UI не нужно. Если decimals отсутствует, не следует молча считать 6 без отдельного обозначения допущения.

Есть расхождение с текстовым описанием SDK: [документация TypeScript SDK](https://docs.limitless.exchange/developers/sdk/typescript/markets) описывает `size` как число shares. [Исходник официального SDK, commit `1d5b3a7c44af1bfba97ce7b5bd8c2f859cdfeeba`](https://github.com/limitless-labs-group/limitless-exchange-ts-sdk/blob/1d5b3a7c44af1bfba97ce7b5bd8c2f859cdfeeba/src/types/markets.ts#L173-L188) помечает `OrderbookEntry` как точное соответствие API и `size` как shares; метод [getOrderBook](https://github.com/limitless-labs-group/limitless-exchange-ts-sdk/blob/1d5b3a7c44af1bfba97ce7b5bd8c2f859cdfeeba/src/markets/fetcher.ts) возвращает ответ без масштабирования. Для действующего raw transport реализация текущего сайта даёт более конкретное доказательство преобразования, чем этот комментарий SDK.

[Официальная документация WS](https://docs.limitless.exchange/developers/websocket/market-data) указывает JSON number для price/size и одинаковую форму книги с REST, но отдельно не фиксирует scale. Проверенные примеры официального [agents-starter](https://github.com/limitless-labs-group/agents-starter/blob/main/src/scripts/check-orderbook.ts) также показывают raw книгу без деления. `collateral.decimals=6` само по себе не доказывает единицы конкретного поля L2.

Копии источников сохранены в `services/api/data/prediction/unit-research/chunk-55.js`, `chunk-64.js`; соответствие URL задаёт `sources.json` (индексы 55 и 64). SHA-256 локальных копий соответственно: `eac27fe4f962d1e12115aa0659602fbe07e93d1ac969c6bf183a822600ca1757`, `a6f17f63f1a5d8209a7e8f5bfe9d01f2122c031a453bf6b478157a5a44cd6cd5`. Проверка закрывает вопрос масштаба для наблюдаемого UI/API пути; settlement equivalence остаётся UNKNOWN, а доступность размера во время исполнения из snapshot не гарантируется. Ордер для проверки не отправлялся.
