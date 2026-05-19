# Гайд: постоянный Chainlink Streams worker на Windows

Цель: сделать так, чтобы `Chainlink accumulated realtime` копился постоянно, а worker не зависел от открытого терминала или сессии Codex.

## Как это работает

В проект добавлены Windows-скрипты:

```text
ops/windows/run-chainlink-worker.ps1
ops/windows/register-chainlink-worker-task.ps1
ops/windows/status-chainlink-worker-task.ps1
ops/windows/unregister-chainlink-worker-task.ps1
```

Схема:

1. Windows Task Scheduler запускает `run-chainlink-worker.ps1` при входе пользователя в Windows.
2. `run-chainlink-worker.ps1` запускает:

```powershell
python -m app.scripts.poll_chainlink_streams --asset all --interval 10
```

3. Если worker падает, watchdog пишет exit code в лог и перезапускает worker через 10 секунд.
4. Логи пишутся в:

```text
services/api/logs/chainlink-worker-YYYY-MM-DD.log
```

Логи добавлены в `.gitignore`, чтобы не попадать в репозиторий.

Дополнительная защита:

- `run-chainlink-worker.ps1` использует single-instance mutex `Local\PolyCryptoChainlinkWorkerWatchdog`, поэтому повторный запуск launcher-а не должен создавать несколько одинаковых watchdog-процессов.
- При старте `poll_chainlink_streams` пересобирает realtime candles `source = chainlink_streams` из уже сохраненных `price_ticks`.
- Repair не создает искусственные свечи: он использует только реально полученные Chainlink reports, которые уже лежат в `price_ticks`.

## Почему это нужно

`Chainlink Streams reports/latest` отдает только свежий report. Если worker не был запущен ночью, пропущенные 5-минутные окна нельзя восстановить через latest-report endpoint.

Для восстановления пропущенной истории нужен рабочий `Chainlink Candlestick API`, но сейчас он заблокирован `HTTP 401`.

Поэтому для накопления realtime-истории критично, чтобы worker работал постоянно.

## Регистрация worker-а

Команду нужно запускать из корня проекта:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\windows\register-chainlink-worker-task.ps1
```

По умолчанию будет создана задача:

```text
PolyCrypto Chainlink Worker
```

Она стартует при входе пользователя в Windows и сразу запускается после регистрации.

Если Windows Task Scheduler не разрешит регистрацию и вернет `Access is denied`, скрипт автоматически создаст fallback launcher в пользовательской Startup-папке:

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PolyCryptoChainlinkWorker.cmd
```

Этот fallback не требует прав администратора и тоже запускает watchdog при входе пользователя в Windows.

## Проверка статуса Windows-задачи

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\windows\status-chainlink-worker-task.ps1
```

Ожидаемо:

- `State` около `Running` или задача недавно запускалась;
- `LastTaskResult = 0` для нормального запуска;
- `LatestLog` указывает на свежий лог.
- Если используется fallback, `Mode` будет `StartupLauncher`, а `StartupLauncherExists` должен быть `True`.

## Проверка через API

Когда backend запущен:

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/status/sources" -UseBasicParsing
```

В dashboard это же видно через `Source status`.

Для `chainlink_streams` важно смотреть:

- `total_candles` - количество локально накопленных realtime-свечей;
- `total_ticks` - количество raw latest reports;
- `last_tick` - время последнего tick;
- `is_live` - свежий ли worker.

## Проверка логов

```powershell
Get-Content .\services\api\logs\chainlink-worker-$(Get-Date -Format yyyy-MM-dd).log -Tail 80
```

В норме должны появляться HTTP 200 от Chainlink Streams и structured polling results.

Если есть gaps, worker пишет предупреждения вида:

```text
Realtime candle gap detected
```

Это значит, что между предыдущим и текущим запуском были пропущены 5-минутные окна.

## Остановка и удаление задачи

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\windows\unregister-chainlink-worker-task.ps1
```

Команда остановит задачу и удалит ее из Windows Task Scheduler.

## Важные ограничения

- Задача запускается при входе пользователя в Windows, а не до логина.
- Если компьютер выключен или спит, realtime-окна не копятся.
- Если интернет недоступен, worker будет retry-ить запросы с backoff.
- Если `.env` с Chainlink credentials отсутствует, регистрация задачи остановится с ошибкой.
- `chainlink_streams` не заменяет полноценный historical backfill.

## Следующий production-шаг

Для настоящего production лучше вынести worker на сервер/VPS и запускать его через:

- systemd на Linux;
- Docker Compose с restart policy;
- process manager с healthcheck;
- PostgreSQL/TimescaleDB вместо локального SQLite.

Для текущего Windows MVP Task Scheduler + watchdog достаточно, чтобы локальная история Chainlink копилась стабильно во время работы компьютера.
