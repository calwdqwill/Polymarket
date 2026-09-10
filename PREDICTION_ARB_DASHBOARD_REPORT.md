# Prediction Arb dashboard — Iteration 2

Результат: локальный исследовательский интерфейс поверх validated shadow journal реализован. Стратегия и финансовые формулы не изменены. Подключения VPS, live collector, authenticated API и реальные ордера отсутствуют. После этой итерации — STOP.

## A. Восстановленный контекст

Рабочая ветка `codex/prediction-live-shadow`; исходный commit Iteration 1 — `df79560`. Изучены проектные документы, `LIVE_SHADOW_ENGINE_DESIGN.md`, отчёты Target Profit V1 / Execution Simulator V1, инфраструктурные отчёты и runbooks collector/replay/finalizer, shadow models/engine/storage/replay и verify/audit scripts. Рабочее дерево содержало изменения независимого collector-трека: они сохранены и не включаются в commit этой итерации.

Источник — `shadow-engine-v1-validated`, config hash `18090425a1d6ff349d2b5b159c974a0d068cc2f9a6c0edbce43794b4c0b6aa19`. Существующий независимый verifier повторно проверил журнал при загрузке read-model. Исторические артефакты включают summary/config/verification/parity/provenance и 122 журнала. Parity с прежним V1 — SEMANTIC_DIFFERENCES, не EXACT.

| Показатель | 100 ms | 250 ms |
| --- | ---: | ---: |
| Окна | 122 | 122 |
| Попытки | 97 | 97 |
| Captures | 83 | 82 |
| Условный simulated net, USD | 35.1978121770 | 29.3677004530 |
| Net / календарный час, USD | 3.4621 | 2.8886 |

Это результаты существующего historical receive replay, не нового сбора. Settlement и точные fees остаются UNKNOWN.

## B. Архитектура

FastAPI получил отдельный read-only router. `LocalDashboardRepository` загружает завершённый bundle, вызывает verifier и строит последние версии окон/попыток по journal key. Основная SQLite и её схема не меняются. Входная директория задаётся сервером, браузер не передаёт файловых путей.

Графики загружаются независимо и лениво из SHA-pinned historical cache. React-компоненты разделены по экранам в `apps/web/components/prediction`, используют общий типизированный API-клиент и существующий lightweight-charts. Новых runtime-зависимостей нет. Форматирование выполнено отдельным запуском Prettier без изменения package manifests/lockfile.

Будущий источник может заменить repository/series provider за тем же read API. Live delivery, restart storage и sockets в эту итерацию не входят.

## C. Backend API

Все маршруты ниже начинаются с `/api/prediction`, разрешён только GET.

| Маршрут | Данные |
| --- | --- |
| `/health` | LOCAL_HISTORICAL, READY/NO_DATA, verification и отсутствие live connection |
| `/summary` | Dataset, фиксированный config/hash, uncertainty, статистика каждого сценария |
| `/windows` | Окна, `scenario=100/250`, `status`, `date` UTC, `offset`, `limit` |
| `/windows/{id}` | Metadata, оба scenario ledger, попытки и точные transitions/leg events |
| `/windows/{id}/series` | Ограниченные price/edge/net samples, последние asks/depth, точные invalid intervals |
| `/attempts` | Фильтры scenario/direction/result/date/strategy_version, сортировка и пагинация |
| `/attempts/{id}` | Выбранная попытка и обе версии того же window/signal/direction, если существуют |
| `/stats` | Отдельные 100/250 агрегаты, cumulative/window/hour series и причины неудач |

Пагинация: по умолчанию 25 записей, максимум 100; отрицательные offsets и неизвестный scenario отвергаются с 422. Неизвестный объект — 404. Отсутствующий bundle — NO_DATA; повреждённый или незавершённый bundle — 503 с безопасным сообщением. Отсутствующий chart cache — NO_CACHE, при этом journal detail остаётся доступным. Raw transport и секреты не выдаются.

## D. Экраны

- `/prediction`: dataset/health/config, отдельные итоговые карточки, выбор исторического окна и его детали.
- `/prediction/windows`: история всех 122 включённых окон с фильтрами capture/failed/no-signal/one-leg/incomplete coverage, дата UTC, пагинация.
- `/prediction/windows/[id]`: metadata, последние historical asks/depth/status, scenario states, графики, попытки и точная последовательность событий.
- `/prediction/trades`: журнал с фильтрами и переходом в попытку.
- `/prediction/trades/[id]`: signal books/VWAP, arrival books/fills, gross/retained Q, комиссии, friction, residual, net и state transitions по каждому latency-сценарию.
- `/prediction/stats`: показатели и графики обоих сценариев с переключением видимости.

На всех экранах видны research-only и UNKNOWN предупреждения. Время помечено UTC. Исходные ISO timestamps с микросекундами и monotonic ns доступны в деталях; табличное краткое время округлено только для отображения до миллисекунд.

Свечной dashboard сохранён; в его header добавлена ссылка Prediction Arb. Новые стили ограничены новым разделом, унаследованная минимальная ширина таблиц переопределена.

## E. Графики

Probability — Polymarket/Limitless YES ask с переключением на NO ask. Это цены asks, не установленная вероятность события. Edge — A: LL YES + Poly NO; B: Poly YES + LL NO, по Q10 gross requests и существующей fee-модели. Показаны линии 10c и 15c.

Маркеры не округляются до секунды: используются дробные timestamp seconds. S — signal, A/F — arrival/fill, C — capture, X — failure; invalid/STALE отмечены красным. События 100/250 переключаются отдельно. Все исходные timestamps и обе venue fills доступны под графиком. Близкие события на общем 5m масштабе могут накладываться; для разбора используются масштабирование и точная таблица.

Линия разрывается при invalid interval, даже если разрыв короче шага визуальной выборки, и при отсутствии вычислимой depth/edge. В отдельной таблице сохранены точные начало/конец invalid intervals, включая TTL expiry между receive rows.

## F. Журнал попыток

Показаны signal time, окно, A/B, Q, signal edge, 100/250 net и результат. Фильтры: UTC date, direction, success, все failed, one-leg, partial, scenario и версия. Сортировка: новые, net, edge, время capture.

Пагинация считает scenario-attempt записи; одинаковый window/signal/direction объединяется в строку внутри страницы. Сравнительный net второго сценария выдаётся независимо от страницы и фильтра. Результат строки относится к отфильтрованным попыткам. Отсутствующий peer не подменяется ближайшим по времени или номером попытки. В деталях доступны оба реально существующих сценария.

Signal VWAP восстановлен существующим fill helper с предыдущим consumption того же scenario/window. Все 194 historical попытки сверены: сумма VWAP ног воспроизводит сохранённый signal edge без расхождений. Расчёт engine не менялся.

## G. Статистика

Success rate = captures / включённые окна. Captures/hour и net/hour используют сумму календарных длительностей включённых окон; valid hours выводятся отдельно. Это не нормализация на время валидной книги. Исключённые окна не заполняются нулями.

Net включает все попытки, в том числе отрицательные, partial и one-leg. Выводятся net/attempt, median/worst attempt, доля one-leg, доля полных двухногих попыток, доля ног с ненулевым fill, median capture от начала окна. Причины попыток и причины окон без capture разделены.

Cumulative series учитывает завершённые попытки; window series включает окна без сигнала; hourly series агрегирует по UTC часу начала 5m окна и сохраняет неполные часы. Отрицательные столбцы выделены красным. Сценарии нигде не складываются в один PnL.

## H. Производительность и загрузка

При старте read-model проверяются только компактные журналы; полный raw dataset не загружается. Chart endpoint читает один cache, строит 601 равномерный receive-state sample и отдельные точные events. LRU ограничен четырьмя готовыми chart projections, исходный большой JSON освобождается после расчёта.

Замер окна `BTC-5M-20260910T013500Z`: 28 487 source rows → 601 точка + 32 invalid intervals; JSON 170 530 байт. Холодный расчёт 0,123 s, повторный около 0,000095 s на этой машине. Это замер одного окна, не универсальный SLA.

Выборка не сохраняет все локальные экстремумы. Max edge в таблице берётся из полного engine ledger, а точные signal markers — из журнала. Cache и journal считаются неизменяемыми на время процесса; после их замены нужен restart API.

## I. Проверки

- TypeScript: `npm run test` — PASS.
- ESLint: `npm run lint` — PASS.
- Production: `npm run build` — PASS, все шесть новых маршрутов включены; 164 KB First Load JS для prediction routes.
- Ruff новых backend-файлов и `app/main.py` — PASS.
- Восемь новых synthetic tests: API, 422/404/405, пагинация/фильтры, scenario separation, отрицательный net, window/attempt detail, no-signal/empty, pair comparison через границу страницы, corruption и sub-sample gap/hash.
- Полный backend suite после финальных правок: 170 тестов, PASS (22,438 s).
- Отдельная копия только Git index, без незакоммиченных файлов и datasets: все 8 dashboard tests PASS; импорт основного API работает, health возвращает NO_DATA, summary — HTTP 200. Зависимость от чужих незакоммиченных модулей не требуется.
- Historical verifier PASS при загрузке; агрегаты совпали с validated summary, 194 signal VWAP/edge проверки без расхождений.

Synthetic fixtures используются только в тестах и не подмешиваются в API historical paths.

## J. Браузерная проверка

Проверены локальные production frontend/API в браузере Codex. Визуально просмотрены обзор, capture и failed окна, попытка, статистика, cumulative/negative window/hour charts. Проверены navigation, one-leg и no-signal filters, пустой журнал по дате. Console errors на проверенных экранах не обнаружены. Скриншоты просмотрены в ходе проверки через browser tool; отдельные файлы изображений не сохранялись.

Воспроизводимые адреса:

- Обзор: `http://127.0.0.1:3000/prediction`.
- Capture: `/prediction/windows/BTC-5M-20260910T013500Z`.
- Failed: `/prediction/windows/BTC-5M-20260909T182000Z` — −7.0402 / −6.7909 USD по сценариям.
- Attempt: `/prediction/trades/18090425a1d6ff34%3ABTC-5M-20260909T182000Z%3A100%3A1` — signal VWAP 0.653740/0.244000, net −3.7758/−3.4342.
- One-leg: фильтр One-leg в `/prediction/trades`, 3 scenario-attempt записи.
- Empty: дата 2099-01-02 в журнале, 0–0 из 0, обе кнопки пагинации отключены.
- Статистика: `/prediction/stats`.

## K. Изменённые файлы

- `services/api/app/api/routes/prediction.py`, `app/prediction/dashboard_read.py`, `app/prediction/dashboard_series.py`, `app/main.py`.
- `services/api/tests/test_prediction_dashboard.py`.
- `apps/web/app/prediction/`: layout, CSS и шесть page routes.
- `apps/web/components/prediction/`: шесть экранов и shared helpers.
- `apps/web/components/PredictionChart.tsx`, `PredictionDashboard.tsx`, существующий `Dashboard.tsx`.
- `apps/web/lib/prediction.ts`.
- Этот отчёт; добавленные разделы README, ARCHITECTURE, PROJECT_PLAN, BACKLOG, CHANGELOG.

В Git включаются только эти файлы/разделы. Dataset, journals, secrets, build caches и существующие правки collector-трека исключены. Документы индексируются по HEAD плюс добавления этой итерации, чтобы не захватить чужие незакоммиченные разделы.

## L. Известные ограничения

1. Bid ladders не сохранены в historical cache: UI показывает недоступность. Depth — сохранённый ask prefix, не полная depth рынка. UNKNOWN settlement/fees не устранены.
2. Выборка графика 601 точка может пропускать короткие ценовые экстремумы. Exact events/invalid intervals и ledger maxima сохранены независимо.
3. Sampled predicted net/edge относится к наблюдаемой книге без собственного depletion; signal VWAP/edge и net попытки учитывают ledger. Эти величины явно различаются в интерфейсе.
4. Engine journal хранит receive UTC сигнала отдельно от timestamps transitions, вычисленных через monotonic anchor. Возможны малые UTC расхождения; обе исходные метки сохранены, порядок задаётся monotonic ns. Это не исправлялось изменением engine.
5. Только включённые validated 122 окна: два protocol-unsafe исключённых окна не имеют shadow journal и не выдаются как проверенные. Невалидные интервалы внутри включённых окон видны.
6. Источник фиксируется на время процесса. Нет авторизации, live polling, больших постоянных индексов или production deployment; сервер запускается на loopback для локального research.

## M. Следующая итерация

Только отдельное задание: согласовать scope Iteration 3 и проверить fees/settlement/execution API preflight. Этот отчёт не разрешает authenticated действия, интеграцию VPS или orders. Текущая итерация заканчивается после отдельного commit/push.
