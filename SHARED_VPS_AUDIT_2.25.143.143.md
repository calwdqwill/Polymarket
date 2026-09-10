# Аудит общего VPS 2.25.143.143 и план prediction staging

Дата: 10 сентября 2026. Окно remote-проверок: примерно **07:35–07:39 UTC / 10:35–10:39 МСК**.

**Вердикт: PASS_WITH_LIMITS.** Хост можно предложить для ограниченного collector-only staging: сначала 30 минут под лимитами, затем по результатам — до 96 часов обычного потока. Максимальный бюджет новых prediction-данных — 20 GB; при ×3 потоке это примерно 54 часа, а не гарантированные 3–4 дня. Полноценный непрерывный сбор на 3–4 дня при ×3 не помещается с требуемым резервом. Производительность самого collector на этом VPS ещё не проверена.

Выполнены только чтение состояния, короткие resource samples и публичные HTTP/WS handshakes. Пользователи, пакеты, файлы конфигурации, units, firewall и Nginx на сервере не изменялись; код не копировался, collector/shadow/orders не запускались. Reboot/restart/stop не выполнялись. SSH использовал существующий локальный ключ, BatchMode и проверку известного host key. Пароли и private keys не читались и не запрашивались. Обычные серверные access/auth logs могут пополняться от самих подключений.

## A. Сводка сервера

| Параметр | Фактическое значение |
|---|---|
| Host / SSH | `root@2.25.143.143`, port 22; noninteractive key auth успешна |
| OS | Ubuntu 22.04.5 LTS, x86_64 |
| Kernel | `5.15.0-186-generic` |
| Виртуализация | KVM |
| vCPU | **2**, модель AMD EPYC 9354P 32-Core Processor; название физического CPU не означает 32 доступных ядра |
| RAM | **8 322 359 296 bytes**, 8,32 GB / 7,75 GiB |
| Swap | Отсутствует |
| Uptime | 24 дня 11 часов |
| Timezone | Etc/UTC |
| NTP | Включён, `NTPSynchronized=yes`; timesyncd, offset −2,742ms, jitter 3,360ms в последнем состоянии |
| systemd / cgroups | systemd 249; cgroup v2, доступны cpu/io/memory/pids controllers |
| Runtime | `python3` 3.10.12, также найден python3.11; python3.12 не найден в проверенных стандартных путях; Node 20.19.0 |
| Root filesystem | `/dev/sda1`, ext4, 103,865 GB; отдельного persistent data mount не обнаружено |

Восстановлен контекст из [Phase 1 infrastructure plan](LIVE_SHADOW_INFRASTRUCTURE_PLAN.md), исходного prediction audit/probe/live/stabilization/settlement/finalizer runbooks, recovery `REPORT.md`, Execution Simulator V1 и Target Profit V1 с fee research. Это продолжение выполненного в предыдущем ходе bootstrap; актуальные Git/документы/ключевые code hashes перепроверены.

Базовый dataset `research-20260909T151749Z` содержит 122 пригодных окна, 10 ч 10 мин; coverage основной выборки 88,16%. Q10/$0,50/edge 10–15c — кандидат из offline research, не live-результат. Settlement/fees остаются UNKNOWN. Главные блокеры collector: delayed invalidation при WebSocket close, растущая стоимость `metrics()`, синхронный hot path, отсутствие проверенной Linux service lifecycle/durability. Они не исчезают от переноса на VPS.

## B. Существующие нагрузки

| Проект | Размещение | Наблюдаемое состояние |
|---|---|---|
| DeltaGrid | `/opt/deltagrid`, Compose: frontend/backend/PostgreSQL | Три контейнера healthy, работают около трёх недель |
| DeltaGrid preview | `/opt/deltagrid-preview`, отдельный Compose | Три контейнера healthy, около трёх недель |
| MoEx | `/opt/mo-ex`, Compose frontend/backend/DB | Backend/DB healthy; frontend Up; backend/frontend около двух недель |
| ABC-RAP | `/opt/abc-rap/compose.yaml`, releases в `/opt/abc-rap-releases` | Collector, account-sync, maker-shadow-observer и PostgreSQL healthy; workers около 25h |
| Hermes | Native `hermes-dashboard`, `hermes-gateway`, `hermes-workspace` и отдельный контейнер | Native units running от root; workspace `/root/hermes-workspace`; контейнер Up |
| Paperclip | `/docker/paperclip-dv11/docker-compose.yml` | Up, публичный Docker port 64958 |
| Traefik | `/docker/traefik-nahs/docker-compose.yml` | **Restarting**, exit=1; RestartCount=35 084 при проверке |

Имя `maker-shadow-observer` относится к существующему ABC-RAP, не к `poly_crypto`. Содержимое его стратегии/credentials не исследовалось.

В `/srv` нет найденных проектов; `/var/www` содержит `html` и `letsencrypt`; `/home/ubuntu` существует. По inventory prediction-каталоги `/opt/poly-crypto-prediction`, `/var/lib/poly-crypto-prediction`, `/etc/poly-crypto-prediction` отсутствуют; пользователь `prediction` не найден.

Top CPU по `ps` — uvicorn/Python/PostgreSQL; это среднее за время жизни процесса, не текущая host utilization. Среди больших RSS — MainThread ~282,6 MiB, Hermes ~221 MiB, uvicorn ~184 MiB. RSS процессов PostgreSQL не суммируется как уникальная память из-за shared pages.

## C. Запас CPU и RAM

Измерение без обхода каталогов: **12 интервалов по 5 секунд, всего 60 секунд**. CPU вычислен из `/proc/stat`, память из `/proc/meminfo`, I/O из `/proc/diskstats`. Это короткий baseline, не суточная характеристика.

| Метрика | Среднее | Диапазон / максимум |
|---|---:|---:|
| CPU busy, включая system/irq/steal, без idle/iowait | **21,566%** | 6,35–32,73% |
| I/O wait | 0,075% | max 0,2% |
| Steal | 0,042% | max 0,1% |
| MemAvailable | **5,086 GB** | 5,073–5,095 GB |
| Disk read | 0,034 MB/s | max 0,217 |
| Disk write | 0,192 MB/s | max 0,465 |
| Disk busy proxy | 2,287% | max 3,36% |

Load average: сначала 0,42/0,69/0,66, в конце 0,56/0,69/0,67. На двух vCPU признаков постоянного CPU saturation в чистом sample нет. Нет постоянного steal; swap usage=0, поскольку swap отсутствует.

Первый vmstat sample пересёкся с `du` и показал system CPU до 57%, iowait до 19%, steal до 2%. Он **не используется как idle baseline**. Даже обход каталогов на этом shared host влияет на нагрузку; повторные массовые `du`, build/replay/backtest во время сбора исключить. `du` выполнялся с низким nice/I/O priority и ограничением времени. `iostat`/`sar` и архив sysstat не найдены; исторический sustained CPU не подтверждён.

OOM: 6 сентября в 09:23:46 и 09:26:28 UTC kernel убил `abc-rap-backtes` при **CONSTRAINT_MEMCG**. Это два контейнерных memory-limit incidents, не установленный host-wide OOM. Они показывают, что тяжёлые backtests на этом хосте уже достигали лимитов. Других найденных OOM в проверенном семидневном kernel journal нет; полнота более старой истории не утверждается.

RAM gate на момент аудита проходит: available>3 GB. При общем prediction budget 1792 MiB (~1,879 GB) от минимальных 5,073 GB останется ориентировочно **3,194 GB**; это расчёт при неизменной соседней нагрузке, не reservation. Многие соседние контейнеры не имеют memory/CPU limits и могут занять этот запас позднее.

## D. Запас диска

Контрольный `df -B1 /` в 07:38:45 UTC:

- Capacity: **103 865 303 040 bytes**.
- Used: **51 667 865 600 bytes**.
- Available: **52 180 660 224 bytes**, 52,18 GB / 48,60 GiB.
- Использование `df`: 50%; inode used ~650 805 из 12 902 400, около 6%.
- `/var/lib` расположен на том же `/dev/sda1`; Docker overlay mounts не являются дополнительными дисками. `/run` и `/dev/shm` — tmpfs, для raw не подходят.

Наблюдаемые крупные каталоги, allocated bytes по `du`, без удаления:

| Каталог | GB |
|---|---:|
| `/var/lib/containerd` | 20,149 |
| `/var/lib/docker` | 16,965 |
| Внутри Docker: volumes | 15,042 |
| Внутри Docker: containers/logs | 1,856 |
| `/root` | 3,822 |
| `/var/log` | 0,482 |
| `/opt` | 0,207 |

Вложенные строки не складывать с родительскими. Docker image sizes также не складывать как уникальный физический объём: есть shared layers. Никакой prune/очистки не выполнялось и не предлагается как обязательное условие staging.

Принят строгий общий резерв: `max(20 GB, 25% filesystem)` = **25,966 GB**. Дополнительно резервируем **5 GB** на release/venv, prediction logs, small analysis и неопределённый рост соседних данных. Это плановый буфер, не измеренный прогноз соседей.

Доступный raw/derived budget: `52,181 − 25,966 − 5 = 21,214 GB`. Округляем вниз до **20 GB** всех новых prediction data, включая sidecars/derived copies. Полная локальная backup-копия в этот бюджет не помещается как отдельная бесплатная сущность.

| Сценарий | Prediction data | Остаток после data + 5 GB | Резерв ≥25,966 GB |
|---|---:|---:|---|
| 3 дня normal | 8,907 GB | 38,274 GB | Проходит |
| 4 дня normal | 11,876 GB | 35,305 GB | Проходит |
| 3 дня ×3 | 26,721 GB | 20,460 GB | Не проходит |
| 4 дня ×3 | 35,628 GB | 11,553 GB | Не проходит |

При ×3 лимит 20 GB исчерпается за `20 / (2,969×3) ×24` ≈ **53,9h**. При normal четырёхдневный запуск возможен по диску, но мониторинг должен остановить только prediction раньше, если вырастут чужие данные. Новый mount не создаётся; предпочтительный путь raw — `/var/lib/poly-crypto-prediction/raw` на существующем ext4.

## E. Сеть и порты

| Bind | Назначение / факт |
|---|---|
| `0.0.0.0/[::]:22` | sshd |
| `0.0.0.0/[::]:80`, `0.0.0.0:443` | Nginx |
| `0.0.0.0:3000` | Node, cwd `/root/hermes-workspace` |
| `0.0.0.0/[::]:64958` | Paperclip Docker publish → container3100 |
| `127.0.0.1:3001`, `8000` | DeltaGrid |
| `127.0.0.1:3012`, `8011` | DeltaGrid preview |
| `127.0.0.1:8001`, `8080` | MoEx |
| `127.0.0.1:9088`, `9089`, `9091` | ABC-RAP |
| `127.0.0.1:4860` | Hermes container |
| `127.0.0.1:8642`, `9119` | Native Hermes |
| `127.0.0.53:53` TCP/UDP | systemd-resolved |
| `18010`, `13010` | **Свободны** по отдельному `ss` фильтру |

UFW active: deny incoming, allow outgoing, deny routed; разрешены SSH, 80/443 и 4343, включая IPv6. На 4343 listener не найден. Docker имеет собственные DNAT/ACCEPT rules; `DOCKER-USER` пуст. Наличие UFW не означает, что Docker published port закрыт.

Ограниченная внешняя TCP-проверка с локального компьютера: 22/80/443/**64958** доступны; 3000/4343 — timeout 2s. Timeout не доказывает глобальную недоступность, проверка относится только к этому источнику и моменту. 64958 действительно доступен извне; приложение/auth не проверялись. Apache/Caddy не найдены среди running services/listeners.

Эффективная SSH policy, в том числе с `sshd -T -C` для текущего root/source: `PermitRootLogin yes`, `PasswordAuthentication yes`, `PubkeyAuthentication yes`, `KbdInteractiveAuthentication no`, `AuthenticationMethods any`. Root password login разрешён конфигурацией, наличие/валидность пароля не проверялись. Fail2ban установлен, но **inactive**, socket отсутствует. Это существующие риски; никаких security configuration changes в этой фазе.

## F. Docker, systemd и Nginx

Docker Engine **29.5.3**. 17 контейнеров в `ps -a`: 15 Up, один Restarting (Traefik), один Exited(0) hello-world. Семь Compose projects: `abc-rap`, `deltagrid`, `deltagrid-preview`, `moex`, `hermes-agent-eh4b`, `paperclip-dv11`, `traefik-nahs`.

Volumes: `abc-rap_postgres-data`, `deltagrid-preview_postgres_data`, `deltagrid_postgres_data`, `mo-ex_pgdata`, `moex_pgdata`, `traefik-nahs_traefik-letsencrypt`. 20 image entries, включая app images, Postgres16-alpine, Hermes, Paperclip и старые ABC-RAP preflight. Крупнейшие отображаемые images: Hermes9,5GB, Paperclip4,64GB, каждый DeltaGrid frontend1,02GB. Список размеров не является reclaimable disk estimate.

Однократный Docker stats: ABC-RAP PostgreSQL ~509MiB/1GiB, collector110,5MiB/768MiB, account-sync40,6MiB/384MiB, observer34,5MiB/192MiB; Paperclip372MiB, preview PostgreSQL380MiB, MoEx backend184MiB. CPU одного контейнера максимум в этом sample MoEx DB7,59%; такие проценты относятся к CPU accounting Docker, не напрямую к сумме host RAM или хостовым 100%. BlockIO в Docker stats накопленный, не скорость.

ABC-RAP collector/account-sync/observer ограничены соответственно 1,5/0,75/0,35 CPU и memory budgets выше. Большинство остальных containers `Memory=0`, CPU без quota. Native Hermes также `MemoryMax=infinity`, CPU quota отсутствует. Prediction не должен автоматически менять их limits.

Systemd failed units: **0**. Это не отменяет неисправный Docker Traefik. Custom enabled: Hermes dashboard/gateway/workspace, `mo-ex-docker`; старый `mo-ex.service` disabled/inactive, `mo-ex-docker` active. Docker/containerd/Nginx/SSH/timesyncd running. `unattended-upgrades` присутствует, но фактическая политика автоматического reboot не исследована и не изменена.

Nginx routing прочитан только по allowlisted `listen/server_name/root/proxy_pass` directives; полный dump, cert keys, env и auth headers не читались. `nginx -t/-T` не запускался: для inventory достаточно чтения routing, config reload не нужен.

- `deltagrid.pro`, `www.deltagrid.pro`: TLS443 → frontend3001 и backend8000, включая WS route.
- `preview.deltagrid.pro`: HTTP80 → frontend3012/backend8011.
- `mo-ex.online`, `www.mo-ex.online`: HTTP80, frontend path `/opt/mo-ex/frontend`, API8001.
- Host/IP и Hermes hostname: HTTP80 → 4860; отдельный ACME root.

Docker для prediction **не выбран**: venv/systemd уже поддерживаются хостом, дают отдельный cgroup и пути без новых images/builds/port publishing. Само наличие Docker не даёт преимущества, а image storage уже занимает значительную часть диска. Не добавлять prediction в чужие Compose networks/DB/volumes.

## G. Доступность Polymarket

Проверено с VPS: DNS, TLS с проверкой сертификата и по три последовательных GET/handshake с паузой 1s. Новое TLS connection на sample. `response_ms` включает DNS lookup, создание TLS context, connect/TLS и получение HTTP headers; это грубая клиентская задержка ответа, **не ICMP RTT и не matching-engine latency**. Ответы рынков в отчёт не выводились.

| Endpoint | Результаты | response ms | Медиана |
|---|---|---|---:|
| `https://gamma-api.polymarket.com/markets?limit=1` | 200,200,200 | 59,17 / 31,69 / 36,88 | **36,88ms** |
| `https://clob.polymarket.com/time` | 200,200,200 | 116,27 / 113,31 / 115,13 | **115,13ms** |
| `wss://ws-subscriptions-clob.polymarket.com/ws/market` | 101,101,101 | 105,68 / 186,78 / 181,34 | **181,34ms** |

DNS разрешён во всех случаях, TLS errors отсутствуют. WS `Sec-WebSocket-Accept` проверен. Подписка на рынки не отправлялась, книги не собирались. Региональных ограничений на эти запросы не обнаружено; доступ ко всем CLOB methods и длительная стабильность не доказаны.

## H. Доступность Limitless

Та же методика, public-only:

| Endpoint | Результаты | response ms | Медиана |
|---|---|---|---:|
| `https://api.limitless.exchange/markets/timeline?symbol=BTC&frequency=minutely&subFrequency=minutes_5&before=0&after=1` | 200,200,200 | 400,61 / 34,60 / 34,69 | **34,69ms** |
| `wss://ws.limitless.exchange/socket.io/?EIO=4&transport=websocket` | 101,101,101 | 366,09 / 280,36 / 271,35 | **280,36ms** |

Первый API DNS lookup94,32ms, последующие<1ms; первый response выбивается из тёплого sample. TLS/upgrade проходят, failures0/6. Socket.IO namespace/subscription не отправлялись; application heartbeat/book delivery не проверены. WS handshake280ms не означает, что каждое изменение книги приходит с такой задержкой, и не подтверждает simulation100/250ms.

Итого connectivity: 15 проверок, 9 HTTP200 и 6 WS101, без auth/trading. Для решения о качестве collector нужен отдельный разрешённый staging sample.

## I. Риски для действующих сервисов

1. Только два vCPU; snapshot baseline достаточен для ограниченного испытания, но CPUQuota может вызвать отставание collector. Если throughput не укладывается, остановить prediction и выбрать отдельный VPS, не снимать лимиты автоматически.
2. Общий root disk с четырьмя PostgreSQL и Docker storage. Обход каталогов уже показал чувствительность к I/O. Никакого параллельного replay/grid/build или полного backup VPS.
3. Соседние unlimited services способны увеличить RAM/CPU; prediction должен первым уступать ресурсы. Два прошлых backtest OOM не повторять новым heavy job.
4. Traefik restart loop существовал до аудита; исправление вне задания. Не приписывать его prediction и не рестартовать Docker/Nginx для проверки нового кода.
5. Root password auth, inactive fail2ban, внешний64958 и root-native services — подтверждённые отдельные security findings. Prediction endpoint только loopback; новый публичный доступ не добавлять.
6. Непроверенная 3.10/3.11 совместимость проекта, настроенного на Python3.12. Запрещено подменять системный Python. Нужен изолированный проверенный runtime после отдельного разрешения.
7. Текущий collector ещё не готов к заявленным disk/RAM limits и graceful stop. Простая установка unit не гарантирует безопасность данных.
8. Отсутствие off-host backup location не блокирует подготовку плана, но потеря VPS тогда означает потерю новых raw.

## J. Решение

**PASS_WITH_LIMITS** — единственный итоговый статус.

Условия:

- Только collector + маленький status endpoint. Без shadow, PostgreSQL, Next build/frontend, backtests и миграции существующих БД.
- Сначала 30min smoke под указанными limits, затем 2h capacity gate. Продление максимум до 96h только при успешных фактических проверках и отдельном разрешении длительности.
- New prediction data≤20GB, operational overhead≤5GB; сохранять ≥25% root filesystem свободными. При ×3 остановка примерно через54h или раньше при чужом росте.
- Никаких изменений соседних services. Прекратить prediction при host saturation/pressure/queue lag; лимиты не повышать автоматически.
- Safe stop, owner lock, новый run на restart, ранняя инвалидация и bounded metrics должны быть проверены до многосуточного прогона.

Это **не PASS на гарантированные 3–4 суток любого потока**, не доказанная performance collector и не разрешение deployment. Если требуются полные96h при ×3 без возможности ранней остановки — этот хост не удовлетворяет такому требованию по текущему диску; нужен отдельный/расширенный ресурс.

## K. Предлагаемая изоляция

| Путь / сущность | Назначение |
|---|---|
| `/opt/poly-crypto-prediction/releases/<release-id>/` | Immutable code + отдельный venv, root-owned |
| `/opt/poly-crypto-prediction/current` | Symlink на release, не на data |
| `/var/lib/poly-crypto-prediction/raw/<run-id>/` | Исходные transport/metadata/checkpoints |
| `/var/lib/poly-crypto-prediction/state/` | Run registry, owner identity, heartbeat/read model |
| `/var/lib/poly-crypto-prediction/analysis/` | Только малые результаты; heavy processing вне VPS |
| `/run/poly-crypto-prediction/` | Lock/socket, не raw |
| `/etc/poly-crypto-prediction/` | Только prediction config, без trading credentials |
| `prediction` | Предлагаемый system user, `/usr/sbin/nologin`; ещё не создан |
| `127.0.0.1:18010` | Status API; проверен свободным |
| `127.0.0.1:13010` | Зарезервировать в плане под future web, сейчас не использовать |

Существующие Nginx/port/firewall правила не нужны для staging. Смотреть status через SSH tunnel. Перед будущим bind повторно проверить порты. Collector пишет только в свои data/state paths; API читает state. `ProtectSystem=strict`, `ProtectHome=yes`, `NoNewPrivileges=yes`, `UMask=0027`, узкие ReadWritePaths. Не подключать Docker socket и чужие volumes/DB. Backup credentials доступны только backup-процессу.

## L. Предлагаемые resource limits

Значения **не применены**. Отдельная `prediction.slice` ограничивает сумму collector/API/backup/monitor, чтобы вспомогательные процессы не обходили общий бюджет.

| Настройка | Общая slice | Collector / пояснение |
|---|---|---|
| `CPUQuota` | **60%** | Максимум0,6 одного CPU =30% двух-vCPU хоста; это потолок, не резерв |
| `CPUWeight` | 20 | Ниже стандартного100 при конкуренции |
| `MemoryHigh` | 1536M | Collector1024M; ранний warning/pressure |
| `MemoryMax` | **1792M** | Collector1536M; status128M, backup128M, все под общим cap |
| `MemorySwapMax` | 0 | На хосте swap отсутствует |
| `TasksMax` | 128 | Collector64, API/backup32 каждый под parent cap |
| `LimitNOFILE` | Задавать в services | Collector4096, status1024, backup1024 |
| `IOWeight` | 20 | Relative priority, не гарантированная bandwidth isolation |

Семантика CPUQuota относительно **одного CPU**, MemoryHigh/Max и cgroup v2 проверена по официальной [документации systemd249](https://raw.githubusercontent.com/systemd/systemd/v249/man/systemd.resource-control.xml). Лимит памяти может завершить workload внутри cgroup, поэтому MemoryHigh/Max не заменяют своевременный graceful stop и backup.

Результат арифметики baseline: средние21,6% host + максимум30% нового CPU budget ≈51,6%; observed max32,7% +30% ≈62,7%. Это не прогноз будущих пиков. API/backup делят quota с collector; проверять cgroup throttling и receiver lag. Если при60% quota образуется backlog, не объявлять live stream успешным и не увеличивать до100/200% без новой оценки соседей.

Monitor30s: host CPU>70% устойчиво2min, MemAvailable<3GB, I/O wait>10% устойчиво1min или sustained memory pressure → controlled stop prediction. Короткий spike → warning. Все policy значения фиксировать в config/отчёте, не выводить их как измеренные естественные границы.

## M. Безопасность диска

Обязательные пороги будущего collector из задания:

- Warning: filesystem usage≥70%.
- Critical: usage≥80%.
- Controlled stop: free<`max(15 GiB, projected 12h write volume)`.

Для shared host действует **дополнительный более строгий stop**: free<`max(20 GB, 25% filesystem)` либо new prediction data≥20GB. Общий stop threshold — максимум всех указанных порогов. Поэтому здесь остановка может наступить около75% использования, раньше critical80%; это намеренное сохранение резерва соседей.

Рост оценивать инкрементальными stored-byte counters и `statvfs`, не рекурсивным `du` каждый tick. Projected12h — из фактического rolling write rate; при×3 базовая оценка4,454GB, меньше15GiB. Stop должен закрыть gzip/manifest, отметить DISK_PRESSURE и запретить автоматический restart до восстановления условий. Только prediction; никаких delete/prune/chown чужих каталогов.

Текущий код останавливается только при<2GiB и ещё не реализует shared policy. Нужны отдельные изменения и fault tests до запуска. Бюджет20GB не является уже установленной filesystem quota; это будущий app/monitor guard с safety margin. Автоудаление raw запрещено, включая уже выгруженные файлы.

## N. Резервирование

Простой prediction-only off-host copy каждые **10 минут** (допустимый интервал5–15min): закрытые durable gzip segments + manifest/schema/config/code hashes; сравнить SHA-256 и сохранить receipt. Только copy-only, без delete/sync cleanup. Не считать active segment backup-complete. Не создавать полный backup VPS и не переносить чужие Docker volumes.

Storage ещё не выбран; это явный риск, не блокер самого staging plan. Кандидат — пользовательский off-host object storage либо отдельный хост с ограниченным upload account. Не устанавливать backup service и не запрашивать secrets в чате сейчас.

До подтверждения fsync/closed durable manifest существующий `segments.json` доказывает закрытие gzip, но не power-loss durability. Это исправить и проверить до обещания RPO. При10min upload и60s rotation целевой RPO около11min только при исправном uploader; фактический backup age должен быть виден. Без выбранного внешнего storage RPO при потере VPS не ограничен.

Restore sample выполнять в отдельный каталог вне действующей raw-сессии, с ограниченным размером; checksum + gzip parsing + replay. Предпочтительно скачать sample на локальный компьютер, чтобы не конкурировать с PostgreSQL за I/O. При отставании upload предупреждать, не чистить raw автоматически.

## O. Git и blocker release

Ветка: `poly_crypto/V1.0`; HEAD: `a3d74b21be3b2be1dae6ff6adaa2cc88f7360414`. Два старых commits MVP, prediction changes отсутствуют в HEAD.

Локальная allowlist-инвентаризация нашла **48 untracked code/test/fixture/Windows-launcher файлов**: 17 modules в `services/api/app/prediction/`, 21 prediction/target-profit scripts, семь test files, один fixture, два Windows launcher. Research reports/docs дополнительно untracked и не входят в число48. `services/api/requirements.txt` изменён. Обычный clone старого HEAD не является release.

План release только после отдельного подтверждения:

1. Сохранить текущую рабочую копию; не выполнять `git add .` и не делать destructive checkout.
2. Составить явную allowlist source/import dependencies, requirements, tests/fixtures, необходимых docs и новых staging ops. Windows launchers можно сохранить в source branch, но они не являются Linux runtime.
3. Исключить `.env`, секреты/ключи, raw/recovery/cache, SQLite, venv, node_modules, `.next` и накопленные логи; проверить как имена, так и содержимое выбранных файлов.
4. Создать отдельную `codex/prediction-shared-staging` ветку после подтверждения; branch name предварительный. Source manifest: относительный путь/bytes/SHA-256, runtime/dependency versions и базовый commit; dirty additions учитываются явно.
5. Локальные backend tests/Ruff, Linux compatibility/smoke, review точного diff и allowlist. Commit/push/deploy — только после отдельного подтверждения; их разрешение не выводится из запроса аудита.
6. Проверенный release упаковать вне raw; на VPS сверить manifest перед запуском. Не менять системный Python: отдельный Python3.12 runtime либо заранее проверенная совместимость3.11, с явным решением до deployment.

В этой итерации inventory прочитан, manifest/release archive/branch/commit **не создавались**. Key hashes `live_storage.py` и `live_transport.py` совпадают с Phase1/recovery context; известные collector blockers сохраняются.

## P. Точный план staging deployment

**Следующие шаги только предлагаются, на сервере не выполнялись.** Этот master task не разрешает deployment автоматически.

1. Получить подтверждение конкретного bounded staging scope:30min smoke +2h resource test; shared limits, early-stop и отсутствие reboot. 96h continuation утверждать отдельно после sample.
2. Локально подготовить минимальные fixes: SIGTERM/STOP, owner lock и boot/session identity; раннюю invalidation до await close; bounded metrics; shared disk/memory/queue policy. Не менять VWAP/fees/strategy formulas, не добавлять shadow. Если writer не выдерживает quota — отдельный VPS вместо ослабления safety.
3. Подготовить allowlisted release и Linux tests на изолированном окружении. При последующем разрешении создать branch/commit/release manifest по O. Не устанавливать новые пакеты и не собирать frontend на shared VPS во время аудита.
4. Повторить read-only preflight: df/inodes, MemAvailable,2–5min CPU/I/O, свободный18010, текущие workloads. Требования: raw budget20GB +5GB overhead +25% filesystem reserve, available≥3GB; отсутствие новых sustained pressure. Если хуже — пересчитать срок или отказаться.
5. После deployment approval создать только user `prediction`, перечисленные dirs/venv и units: `prediction.slice`, `prediction-collector.service`, `prediction-status.service`, `prediction-health.service/.timer`, при выбранном storage — `prediction-backup.service/.timer`. Создать env example без credentials. Не менять Nginx/firewall/Docker units, не создавать web/DB/shadow.
6. Проверить `systemd-analyze verify`, права и limits. Bounded runner передаёт уникальный persistent `--output` существующему CLI `app.scripts.collect_prediction --seconds 1800`; фиксированный повторно используемый output запрещён. CLI в текущем виде не готов для этого без lifecycle fixes. `Restart=on-failure` с rate limit и отказом restart при resource stop; `TimeoutStopSec=60s`. Нормальный конец smoke не продлевается автоматически.
7. Запустить30min public collector-only smoke. Проверить current+next discovery, snapshots обеих venues, gap/quality, новый segment, status18010 через tunnel. Замерять process/cgroup CPU/RSS/throttled time, queue bytes/age, raw throughput/lag, disk/backup. Отдельно наблюдать известные соседние endpoints/containers без их изменения.
8. Gate30min: нет новых restart/errors существующих сервисов, host steady CPU<70%, MemAvailable≥3GB, нет OOM, writer backlog не растёт, raw/checkpoint sample reproducible, invalidations immediate. Невыполнение → stop prediction, report; не менять чужие limits.
9. Если разрешён2h test и gate пройден — ещё один bounded run или заранее заданное продолжение по явному scope. Проверить только restart **prediction**, fresh run и closed-segment recovery. Shared VPS reboot/SIGKILL fault injection не делать; crash tests выполнить локально/в отдельном staging. Real raw не повреждать ради теста.
10. Выгрузить closed sample, verify SHA/gzip/checkpoint и protocol/observation replay локально. Проверить disk/stop на fixtures; не заполнять shared disk искусственно. Проверить restore sample и backup age, если backup подключён.
11. Отчёт о30min/2h с фактическими временами, CPU/RSS/queue/coverage/disk и влиянием на соседей. Только при успешных gates и отдельном подтверждении — bounded максимум96h при20GB cap; возможна ранняя остановка. Не выдавать плановые96h за собранные данные.
12. В конце срока/лимита остановить только prediction, закрыть сегменты, сохранить final state и report. Raw не удалять, release rollback не касается data. Shadow/strategy и trading остаются запрещены.

Предлагаемые локальные support files: `ops/linux/prediction.slice.example`, collector/status/health/backup unit templates, `prediction.env.example`, минимальный Linux launcher/status/health module, tests lifecycle/resource gates, `docs/prediction-shared-staging-runbook.md`. Их точный patch будет отдельной итерацией; в текущем аудите создан только этот отчёт и обновлены PROJECT_PLAN/BACKLOG/CHANGELOG.

Проверки текущей итерации: SSH host-key+key auth, inventory,60s resource sample,15 public HTTP/WS requests, шесть targeted external TCP connects, Git inventory. Backend tests/Ruff/frontend повторно не запускались: код не менялся, результаты120 tests/prediction Ruff/frontend PASS относятся к предыдущей Phase1; семь общих Ruff I001 остаются её baseline. Структура A–P и doc links проверяются локально перед выдачей.

**STOP. Deployment не выполнен.**
