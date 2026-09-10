# Длительный read-only сбор BTC 5m

## Восстановление после аварийного завершения

Для прерванного `research-20260909T151749Z` выполнены forensic и восстановление 10 сентября; [причина, ограничения и evidence](prediction-recovery.md). Исходник не изменяется. Используйте новый каталог результата; повторный запуск recovery в уже существующий forensic bundle блокируется.

После проверки отсутствия collector и сохранения системных событий, из `services/api`:

```powershell
.venv/Scripts/python.exe -m app.scripts.recover_prediction data/prediction/research-20260909T151749Z data/prediction/НОВАЯ_КОПИЯ
.venv/Scripts/python.exe -m app.scripts.replay_prediction data/prediction/НОВАЯ_КОПИЯ/dataset
.venv/Scripts/python.exe -m app.scripts.verify_prediction_observations data/prediction/НОВАЯ_КОПИЯ/dataset
.venv/Scripts/python.exe -m app.scripts.analyze_prediction_research data/prediction/НОВАЯ_КОПИЯ/dataset --full-windows-only --replay-exclusions data/prediction/НОВАЯ_КОПИЯ/dataset/observation-replay.json --progress
.venv/Scripts/python.exe -m app.scripts.repair_prediction_episode_timestamps data/prediction/НОВАЯ_КОПИЯ
.venv/Scripts/python.exe -m app.scripts.recover_prediction data/prediction/research-20260909T151749Z data/prediction/НОВАЯ_КОПИЯ --verify-only
.venv/Scripts/python.exe -m app.scripts.report_prediction_recovery data/prediction/НОВАЯ_КОПИЯ
```

Каждую следующую команду выполняйте только после успешной предыдущей. Mismatch блокирует анализ до разбора первого расхождения. `observation-replay.json` отдельно сохраняет protocol-небезопасные окна: их исключения обязательны при анализе. Совпадение raw VWAP с observation не отменяет задержку инвалидирования обнаруженного rejected frame.

Пять итоговых файлов создаются в каталоге `НОВАЯ_КОПИЯ`, рядом с manifest и forensic metadata. `dataset/analysis-progress.json` показывает ход пересчёта. Сегмент 623 хранится отдельно в `repaired-tail` и не входит в основную стратегию. Исходный LIVE/WAITING не подменяется STOPPED, новый collector не запускается.

UTC-метки производных эпизодов пересчитываются по локальным clock anchors observations. Единственный anchor начала многочасового запуска даёт небольшую погрешность относительно границ окон. Исходный `dataset/opportunities.jsonl.gz` сохраняется; исправленные метки и точный TTE начала записываются в отдельный `opportunities.jsonl.gz` рядом с итоговыми отчётами. Monotonic endpoints, lifetime, edge, capacity и состав эпизодов не меняются. `report_prediction_recovery` предназначен именно для forensic-case `research-20260909T151749Z`, а не для автоматического утверждения причины будущих аварий.

Ниже сохранены обычные команды сбора и штатного завершения. Их не следует запускать автоматически после recovery.

Текущий формат — schema v2. Используются публичные данные Polymarket и Limitless. Ордеров, кошельков и private keys в этом процессе нет. Settlement UNKNOWN допускает исследовательские наблюдения, но не подтверждает гарантированный арбитраж.

## Запуск

Из корня проекта в PowerShell:

```powershell
./ops/windows/start-prediction-research.ps1 -Hours 12
```

Launcher открывает скрытый процесс, печатает каталог и PID, создаёт рядом с каталогом `.launch.json`, `.stdout.log`, `.stderr.log`. На Windows PID launcher Python и рабочего дочернего Python могут различаться: рабочий PID записан в `run.json`. Завершение задачи Codex не останавливает процесс. Компьютер должен оставаться включённым и не уходить в сон; выход из учётной записи и перезагрузка не поддерживаются этим launcher как автоматический restart. Он не является установленной Windows-службой.

Foreground-вариант из `services/api`:

```powershell
.venv/Scripts/python.exe -m app.scripts.collect_prediction --seconds 21600
.venv/Scripts/python.exe -m app.scripts.collect_prediction --seconds 43200
```

Запускайте одну из команд. Collector сам выбирает новый каталог. Существующий dataset не дописывается: новая сессия получает отдельные идентификаторы и snapshots. `--full-windows N` остаётся способом сбора заданного числа полных окон. `--max-age-ms` — совместимый fallback для старых книг; в live schema v2 используются описанные ниже отдельные TTL.

Launcher также запускает завершающий процесс: он ждёт выхода рабочего PID через ОС, проверяет терминальный статус и закрытие gzip, затем выполняет replay, анализ и генерацию `REPORT.md`. Пока collector жив, finalizer не читает активные проекции и raw. Состояния — WAITING/VALIDATING/REPLAY/ANALYSIS/REPORT/COMPLETE/FAILED в `finalization.json`, отдельные stdout/stderr с уникальным суффиксом попытки рядом с dataset. При mismatch replay расчёт выводов блокируется. Для запуска только collector используйте `-SkipAnalysis`.

К уже работающему foreground collector можно подключить ту же финализацию из `services/api`:

```powershell
.venv/Scripts/python.exe -m app.scripts.finish_prediction_research data/prediction/ИМЯ_ЗАПУСКА --wait
```

Это обычный локальный процесс обработки dataset. Он не включает уведомления и не запускает новую задачу Codex. Длительный анализ выполняется после завершения сбора, поэтому не конкурирует с ним за CPU.

Чтобы восстановить только упавший finalizer в фоне, из корня проекта:

```powershell
./ops/windows/start-prediction-finalizer.ps1 -OutputDirectory 'ПОЛНЫЙ_ПУТЬ_К_DATASET'
```

Эта команда не запускает и не перезапускает collector. Единственный finalizer защищён собственной `finalization.lock`; старые логи сохраняются. При ошибке статус содержит этап и traceback. Временные ошибки чтения/публикации после выхода collector повторяются до 60s. Ручной повтор завершённого dataset из `services/api`: `.venv/Scripts/python.exe -m app.scripts.finish_prediction_research 'ПОЛНЫЙ_ПУТЬ_К_DATASET'`. Исходные raw/сегменты остаются целыми, производные результаты пересоздаются. При незакрытом хвосте или отсутствии терминального статуса сначала нужна отдельная диагностика; принудительного обхода проверок нет. [Разбор Windows PermissionError и тесты](prediction-finalization-fix.md).

## Наблюдение и остановка

В каталоге запуска:

- `run.json`: начало, плановый срок, рабочий PID, schema, параметры и хеши модулей.
- `live.json`, `live.html`: текущее окно, котировки, возраст и статусы книг.
- `metrics.json`: фактическая длительность, число сообщений, logical bytes, gzip bytes и MB/hour.
- `errors.*.jsonl.gz`: ошибки сети, протокола и восстановления.
- `processing_lag.*.jsonl.gz`: задержки event loop относительно 50ms таймера, превышающие 100ms. Это не измерение сетевой latency.
- `segments.json`: завершённые 60s сегменты; активный сегмент ещё не является полным gzip-файлом.

Для штатной остановки создайте файл `STOP` внутри конкретного каталога:

```powershell
New-Item -ItemType File -Path 'ПОЛНЫЙ_ПУТЬ_К_DATASET/STOP'
```

Завершение отменяет подписки, сохраняет последние checkpoints, закрывает gzip и устанавливает STOPPED/FAILED. Порог свободного диска — 2 GiB: ниже collector завершится с ошибкой вместо бесконтрольного заполнения. Автоматического удаления raw нет. Накопленные данные необходимо отдельно переносить/архивировать перед многосуточными запусками.

После аварийного завершения незакрытый последний сегмент нельзя считать полным. Закрытые сегменты сохраняются отдельно; не склеивайте их с повреждённым хвостом молча. Этот процесс не гарантирует сохранение ОС-буферов при потере питания. Автоматический restart после reboot и внешнее резервирование остаются отдельной эксплуатационной задачей.

## Анализ, пока сбор продолжается

Из `services/api`, заменив пути на нужный запуск и новый каталог копии:

```powershell
.venv/Scripts/python.exe -m app.scripts.freeze_prediction data/prediction/ИМЯ_ЗАПУСКА data/prediction/ИМЯ_КОПИИ
.venv/Scripts/python.exe -m app.scripts.replay_prediction data/prediction/ИМЯ_КОПИИ
.venv/Scripts/python.exe -m app.scripts.analyze_prediction data/prediction/ИМЯ_КОПИИ
.venv/Scripts/python.exe -m app.scripts.report_prediction_research data/prediction/ИМЯ_КОПИИ
```

`freeze_prediction` копирует только закрытые сегменты, сохраняет их SHA-256 и точный конец фактически скопированных данных. Статус копии — FROZEN_PREFIX. Активный raw не читается как завершённый файл. Копия занимает дополнительное место; оригинал продолжает пополняться. Если закрытых сегментов ещё нет, повторите после первой минуты.

После завершения запуска выполните последние три команды непосредственно для его каталога, без `freeze_prediction`. Анализ сохраняет:

- `replay.json`: проверку raw → full books/status/timestamps в checkpoints.
- `research-analysis.json`: coverage каждого окна, все Q, event/time distributions, positive-only, TTE, thresholds, capacity, lifetime, rates и storage projection.
- `opportunities.jsonl.gz`: только завершённые/цензурированные эпизоды с границами, интегральным средним edge, максимумами Q и gross PnL.
- `REPORT.md`: таблицы A–L. Разделы о legacy DESYNC и settlement относятся к конкретной проверке 9 сентября 2026; новые результаты не превращают их автоматически в обновлённые доказательства.

Квантили считаются с Decimal через временный файловый SQLite-кэш стандартной библиотеки Python; существующая БД проекта не используется и не мигрируется. Это позволяет ограничить память при многочасовом анализе. Кэш удаляется после расчёта. В случае принудительного завершения анализа его временный каталог может остаться в системной временной папке.

## Политика качества

Polymarket: абсолютные deltas и полные snapshots. Повторная старая delta, уже совпадающая с текущим уровнем, не меняет состояние и timestamp. Неидемпотентная регрессия, crossed book и BBO mismatch дают DESYNC. В течение 1s collector сохраняет следующие raw и ждёт свежий snapshot в той же сессии. Deltas в DESYNC не делают книгу валидной. Без snapshot — reconnect, новый adapter и RECOVERING. Начальная подписка ограничена 15s, application silence — 30s, максимальный возраст книги — 30s.

Limitless: полная coalesced YES-книга, NO отражает ту же ликвидность. BOOK_AGE хранится отдельно от CONNECTION_HEALTH. Возраст до 60s допускается при живом соединении. После 30s без book update выполняется повторная подписка; её промежуток RECOVERING исключается. Heartbeat deadline определяется `pingInterval + pingTimeout` из Engine.IO handshake. Версии после подписки могут приходить вне порядка; поздняя регрессия требует новой сессии. Version 0 не заменяет уже полученную более новую книгу.

Связь «heartbeat жив → publisher исправен» не доказана. Ограниченный TTL и периодическая повторная подписка уменьшают этот риск; baseline с возрастом 2s рассчитывается отдельно. VALID означает выполнение документированной локальной проверки и выбранной freshness policy.

## Интерпретация статистики

Размеры: 10, 25, 50, 100, 250, 500, 1000 shares. Основное внимание — 10–100. A: YES Limitless + NO Polymarket; B: YES Polymarket + NO Limitless.

`edge(Q) = 1 − VWAP_YES(Q) − VWAP_NO(Q)`. Fees, slippage после снимка, minimum Limitless, возможность одновременных fills и settlement equivalence не доказаны. Depth-executable не означает исполнимую сделку.

По времени интегрируется состояние между соседними наблюдениями с обрезкой на дедлайнах, disconnect, границах окна и stop. Startup и промежуток до первого наблюдения следующего окна учитываются как MISSING. Ни elapsed time таймера, ни частота событий не создают искусственную прибыль.

Эпизоды ведутся для Q × direction × threshold и отдельно как ANY_Q — объединение размеров. Пороги строгие: >0, >0.5, >1, >2, >3, >5 центов. Не суммируйте разные Q/threshold как независимые сделки. Для каждого эпизода PnL — максимум одного состояния по доступным Q, а не сумма повторяющихся котировок. Lifetime с левой/правой цензурой анализируется отдельно.

Для старого sample полный разбор 133 событий воспроизводится командой:

```powershell
.venv/Scripts/python.exe -m app.scripts.audit_prediction_desync data/prediction/live-20260909T141038Z-080c6260
```

Результаты: `desync-audit.json` и `desync-forensic.jsonl.gz`. Исходные transport logs не меняются.
