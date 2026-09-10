# Ограниченный staging на общем VPS

Разрешён только публичный collector на `2.25.143.143`: 1800 секунд, затем при успешном gate отдельные 7200 секунд. После отчёта остановить collector, status и health timer. Shadow и торговое исполнение не входят в контур.

## Устройство

`prediction.slice` ограничивает CPU 60% одного ядра, RAM 1792 MiB, swap 0. Collector имеет RAM 1536 MiB и TasksMax 64. Отдельный пользователь `prediction` без login; runtime и releases принадлежат root. Status слушает только `127.0.0.1:18010`, без основной БД.

Системный Python не изменяется. Изолированный Python 3.12 устанавливается через uv в `/opt/poly-crypto-prediction/python`; способ установки описан в [официальной документации uv](https://docs.astral.sh/uv/guides/install-python/). Фактические версии и freeze фиксируются с релизом. Releases содержат только allowlist, архив проверяется по SHA-256.

Launcher удерживает flock в `state/writer.lock`, создаёт новый каталог `raw/<UTC>-<UUID>` и сохраняет boot/process/run identity. SIGTERM/SIGINT устанавливают событие graceful stop. `Restart=no` предотвращает незапрошенное продление bounded sample; ручной restart создаёт новую сессию. Старый raw не дописывается.

Raw записывается синхронно до применения события; writer queue отсутствует. Метрики `queue_items/bytes/oldest_age_ms=0` относятся именно к writer, а не TCP/WebSocket backlog. WebSocket backpressure начинается от 16 кадров; telemetry учитывает frames/items/bytes и возраст до completed recv. При >512 frames или >8 MiB буфера немедленно снимается VALID, записывается queue_overflow и соединение прерывается. Возраст до WS parser неизвестен. `pending_durable_*` измеряет записи до fsync; сохраняются max_durable_lag_ms и max_flush_ms; `processing_lag_ms` — от recv до записи raw. Percentile частоты ограничен последними 300 секундами, среднее — за весь запуск. Исторические файлы не сканируются при каждом metrics tick.

Раз в секунду выполняется flush/fsync активных потоков; закрытие gzip дописывает footer и делает fsync. SHA-256 закрытых сегментов формируется после остановки. Это не подтверждение off-host backup или power-loss восстановления.

Порог controlled stop: free < max(20 GB, 25% FS), prediction data >=20 GB, MemAvailable <3 GB, CPU >=70% в течение 30 секунд. При pre-flight нужен ещё остаток prediction budget и 5 GB буфера. Текущий размер учитывает один начальный обход, incremental gzip bytes, фиксированные sidecars и рост state-файлов. Дополнительный запас 100 MB останавливает запись заранее между проверками. Автоудаления нет. При использовании >=70% FS записывается warning.

## Команды

Параметр `PREDICTION_SECONDS` задаётся в `/etc/poly-crypto-prediction/collector.conf`. Units устанавливаются из `ops/linux/`, без enable на boot. Перед каждым запуском проверяются RAM/disk/inodes/CPU/I/O, соседние units/containers, OOM и свободный порт.

```bash
systemctl start prediction-status.service prediction-health.timer
systemctl start prediction-collector.service
systemctl stop prediction-collector.service
systemctl stop prediction-health.timer prediction-status.service
```

Через отдельный локальный SSH tunnel:

```bash
ssh -N -L 18010:127.0.0.1:18010 root@2.25.143.143
curl http://127.0.0.1:18010/status
```

После остановки проверить `live.status`, `segments.active=null`, gzip footer/JSONL и replay на отдельной копии. Тяжёлый анализ выполняется локально. Список SHA-256 хранится отдельно от raw. Проверить фактические длительность, текущие/следующие рынки, coverage, queue/lag, host/cgroup trends и соседние restart/OOM. PASS не выводится из одной успешной команды запуска.

Rollback: остановить только prediction units; вернуть `current` на проверенный предыдущий release при его наличии. Данные сохраняются. Не менять Docker/Nginx/firewall и не reboot общий VPS.
