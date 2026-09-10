# Воспроизведение shadow-engine из Git

Этот документ относится только к фиксации принятой Iteration 1. Iteration 2 не начинается. Изменения соседнего collector/VPS-трека не входят в shadow-коммит.

## Объём воспроизводимости

Чистый checkout содержит engine, event/config/journal contracts, local storage, три CLI, synthetic unit-тесты и необходимые неизменённые зависимости модели V1. Никаких незакоммиченных модулей рабочего каталога для этого не требуется.

**Полный historical результат на 122 окнах нельзя получить только из Git:** raw/recovery, cache, baseline datasets и журналы не включаются в репозиторий по заданию. Для такого повторения нужны внешние исторические входы, перечисленные только именами и SHA-256 в [манифесте](shadow-engine-input-manifest.json). Это ограничение нельзя подменять утверждением о полной автономной воспроизводимости исторических чисел.

## Чистая установка и synthetic-проверки

Из корня чистого checkout, Python 3.12:

```powershell
python -m venv services/api/.venv
services/api/.venv/Scripts/python.exe -m pip install -r services/api/requirements.txt
Set-Location services/api
.venv/Scripts/python.exe -m unittest tests.test_prediction_shadow -v
.venv/Scripts/python.exe -m app.scripts.replay_prediction_shadow --help
.venv/Scripts/python.exe -m app.scripts.verify_prediction_shadow --help
.venv/Scripts/python.exe -m app.scripts.audit_prediction_shadow --help
```

На Linux используйте `.venv/bin/python`. Для этих проверок не нужны `.env`, credentials, wallets, network API, основная БД или historical datasets. Установка Python-зависимостей требует доступ к пакетному источнику либо заранее подготовленный package cache.

Synthetic-тесты включают event replay, детерминизм, независимые latency ledgers, rounding/fees/friction, crossing/gaps, partial/one-leg, window journal и независимый verifier. Они не заменяют historical replay.

Проверка allowlisted Git tree перед commit: экспорт в отдельный чистый каталог, **118/118 тестов PASS** (22,266 s), включая 30 shadow-тестов; scoped Ruff и `--help` трёх CLI — PASS. Импорт engine подтверждён из экспортированного дерева. Использован существующий Python 3.12 environment с зависимостями проекта, без копирования его в Git. Прежние 158 тестов относятся ко всему рабочему дереву с другими незакоммиченными research/collector-тестами и не являются числом тестов этого allowlist.

## Повтор historical результата

Внешний каталог research должен содержать `cache-manifest.json`, `cache/<window>.json.gz`, `selected_runs/<window>.json.gz`. Внешний каталог dataset должен содержать `observation-replay.json` и `markets*.jsonl.gz`. Все необходимые входы и их хеши перечислены в манифесте; содержимое данных в нём отсутствует. Проверяйте SHA-256 всех входов перед запуском.

Из `services/api`:

```powershell
.venv/Scripts/python.exe -m app.scripts.replay_prediction_shadow ПУТЬ_К_RESEARCH ПУТЬ_К_DATASET НОВЫЙ_OUTPUT --workers 3
.venv/Scripts/python.exe -m app.scripts.verify_prediction_shadow НОВЫЙ_OUTPUT
.venv/Scripts/python.exe -m app.scripts.audit_prediction_shadow ПУТЬ_К_RESEARCH НОВЫЙ_OUTPUT
```

Пути указывайте вне checkout; output должен быть новым и отдельным от источников. Каждая следующая команда выполняется после успешного завершения предыдущей. Ожидаемые агрегаты, config hash и число receive rows сохранены в манифесте. Подробный принятый отчёт — [LIVE_SHADOW_ENGINE_DESIGN.md](../LIVE_SHADOW_ENGINE_DESIGN.md); его ссылки на локальные журналы доступны только при наличии внешних результатов.

## Allowlist коммита

Общие Markdown-файлы включаются только shadow-разделами поверх HEAD; остальные локальные правки остаются unstaged.

```text
ARCHITECTURE.md
BACKLOG.md
CHANGELOG.md
PROJECT_PLAN.md
README.md
LIVE_SHADOW_ENGINE_DESIGN.md
docs/shadow-engine-git-reproduction.md
docs/shadow-engine-input-manifest.json
services/api/app/prediction/execution_fees.py
services/api/app/prediction/execution_replay.py
services/api/app/prediction/execution_simulator.py
services/api/app/prediction/target_profit.py
services/api/app/prediction/shadow_models.py
services/api/app/prediction/shadow_engine.py
services/api/app/prediction/shadow_storage.py
services/api/app/prediction/shadow_replay.py
services/api/app/scripts/replay_prediction_shadow.py
services/api/app/scripts/verify_prediction_shadow.py
services/api/app/scripts/audit_prediction_shadow.py
services/api/tests/test_prediction_shadow.py
```

Четыре зависимости V1 уже использовались принятой реализацией, но ранее отсутствовали в Git. Они добавляются целиком без изменения финансовой логики. Historical research runners, reports, прочие тесты, telemetry, ops и текущие collector fixes в allowlist не включены.
