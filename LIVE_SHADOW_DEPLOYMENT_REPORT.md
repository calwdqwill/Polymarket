# Развёртывание prediction collector на общем VPS — Iteration 2A

Дата: 10 сентября 2026. **PASS_WITH_LIMITS для следующего отдельно разрешаемого 24h collector-only.** Полные 30m smoke и 2h capacity завершены; replay, SHA-256, gzip CRC и проверка observations пройдены. Collector и вспомогательные prediction units остановлены. 24h, shadow и orders не запускались. [Машиночитаемые результаты](docs/prediction-staging-evidence/gate.json).

## A. Релиз

- Ветка: `codex/prediction-shared-staging`, опубликована в origin.
- Исходный release commit: `8bd4f72`; актуальный source commit: `229aff03993a6a66cf978c0b07aa13ca197c140b`.
- Release ID: `staging-20260910-229aff0`.
- [Allowlist](ops/linux/release-allowlist.txt): 61 файл содержимого и два файла упаковки. [SHA-256 manifest](ops/linux/release-manifest.json). Все хеши проверены локально и на VPS.
- Архив второго релиза: SHA-256 `637a74944a343872ad1340bce1b83356f7bf878ba9a0638b7b5e2e87ab48a860`.
- Runtime: изолированный CPython **3.12.14**, uv **0.12.12**, pip **26.2.1**. `runtime-version.txt` и `runtime-freeze.txt` находятся в release directory. Второй release установлен по freeze первого.
- Системный Python не менялся. Python 3.10 использовался только для проверки SHA-256 стандартной библиотекой, не для collector.
- Проверка credential-паттернов выбранных файлов: совпадений нет. `.env`, ключи, SQLite, datasets, venv, node_modules, `.next`, logs/caches исключены. Historical execution/target-profit реализация не включена в staging scope.
- До commit показаны точные manifest и staged diff. Первый commit: 57 файлов / 9251 добавление; второй: 8 файлов / +189−14. `git add .` не применялся. Предыдущие изменения пользователя вне scope сохранены.
- Проверки: полный локальный baseline 123 теста; после queue-доработки 26 релевантных тестов; на итоговом Linux release 64 теста, включая 2 Windows-only skips. Ruff итогового release — PASS.

## B. Изменения VPS

Хост: `root@2.25.143.143`, Ubuntu 22.04.5, systemd 249, cgroup v2, 2 vCPU / 8,32 GB RAM.

Созданы:

- System user/group `prediction`, UID/GID 998, shell `/usr/sbin/nologin`, home `/var/lib/poly-crypto-prediction`.
- `/opt/poly-crypto-prediction/{releases,tools,python,cache}`; `current` указывает на итоговый release.
- Releases: `staging-20260910-8bd4f72` (отклонённая упаковка), `staging-20260910-8bd4f72-blobs` (первый технический sample), `staging-20260910-229aff0` (smoke/capacity).
- В releases сохранены исходные архивы `prediction-staging-8bd4f72.tar`, `prediction-staging-8bd4f72-lf.tar`, `prediction-staging-8bd4f72-blobs.tar`, `prediction-staging-v2.tar`; отклонённые варианты не запускались.
- `/var/lib/poly-crypto-prediction/{raw,state,analysis,quarantine}`, `/run/poly-crypto-prediction`, `/etc/poly-crypto-prediction`.
- `/etc/poly-crypto-prediction/collector.conf`: только duration/release ID, без credentials.
- `/etc/systemd/system/prediction.slice`, `prediction-collector.service`, `prediction-status.service`, `prediction-health.service`, `prediction-health.timer`; templates в [ops/linux](ops/linux/).
- Изолированные uv/uvx, standalone Python и два venv. Apt/system packages не устанавливались; полный список Python dependencies записан в runtime freeze.
- State: owner lock, current identity, health JSON/JSONL, snapshots native units/containers, SHA-256 manifests. Raw runs перечислены ниже; закрытый smoke скопирован в `analysis/smoke.tar`.

Выполнен `systemctl daemon-reload`. Units не включались через enable. Status слушает только `127.0.0.1:18010`; доступ через реальный SSH tunnel проверен. Public route/firewall/Docker/Nginx/соседние services/основная БД не изменялись; reboot не выполнялся.

## C. Результаты 30m

Основной run: `20260910T085105Z-3fae3770982d`, collector PID 3801654. Начало 08:51:05 UTC / 11:51:05 МСК, штатное завершение 09:21:10 UTC / 12:21:10 МСК. 1800 секунд bounded collection и около 5 секунд graceful close; общая длительность journal 1805,05 секунды.

| Показатель | Факт |
|---|---:|
| Health samples во время LIVE | 114 |
| Host CPU среднее / максимум | 38,83% / 51,46% |
| MemAvailable минимум | 5,005 GB |
| Host I/O wait среднее / максимум | 0,163% / 0,255% |
| Host disk write среднее / максимум sample | 0,566 / 4,060 MB/s |
| Host disk read среднее / максимум sample | 0,155 / 11,744 MB/s |
| prediction.slice memory максимум | 175,69 MB |
| prediction.slice anonymous memory максимум | 73,90 MB |
| MemoryHigh / OOM / OOM kill events | 0 / 0 / 0 |
| Collector CPU time | 583,856 секунд |
| Receive queue пик отдельного соединения | 419 frames / 274872 bytes |
| Receive queue в health samples | 0 frames; backlog не накапливался |
| Максимальный parsed-frame → recv lag | 1225,64 ms |
| Максимальный durable lag: рабочие snapshots / с shutdown | 3133,50 / 5021,41 ms |
| Максимальный возраст receive queue с shutdown | 4995,52 ms |
| Максимальный flush/fsync | 86,17 ms |
| Queue overflow | 0 |
| Файлы / общий размер | 424 / 100878860 bytes |
| Gzip write rate | 201,20 MB/h |
| Cross-venue valid coverage, весь интервал | 98,9393% |
| Строгий baseline свежести 2s | 61,4289% |

Все 31 segment IDs закрыты (`active=null`); последний содержит shutdown tail. Raw обеих площадок, metadata, checkpoints и observations сохранены. **30m resource/replay gate PASS.** Это не подтверждение пригодности 100/250ms strategy или shadow.

Smoke reconnect того же venue/market: Polymarket 2, Limitless 0; CONNECTED 10/8 по 8 разным рынкам. BBO DESYNC diagnostics 1626, recoveries 1622. Duration-weighted DESYNC: Polymarket 16,232 s, Limitless 5,320 s, cross-venue 16,340 s; MISSING 0,900 s, RECOVERING cross-venue 1,906 s, STALE 0 s при штатных TTL. Discovery следующего Limitless market вернул 10 временных 404; повторные запросы продолжались. Ограничения baseline health из раздела E относятся и к smoke.

Ранее выполнен отдельный технический run `20260910T083815Z-fe7111c54847`: 88,37 секунды, остановлен SIGTERM для дополнения telemetry; 34 файла / 8,76 MB. Он не засчитывается в 30m. Новый запуск получил другой run/session ID и новый каталог, прежний raw не дописывался.

## D. Результаты 2h

Начало service: **09:26:21 UTC / 12:26:21 МСК**, PID 3855515. Run начат 09:26:22.098535 UTC, штатно завершён **11:26:28.828787 UTC / 14:26:28 МСК**: 7200 секунд collection и shutdown tail, общая длительность journal **7206,739658 секунды**. Systemd: `Result=success`, `ExecMainStatus=0`, `inactive/dead`, `MainPID=0`; перезапусков не было. Процесс имел PPID 1, отдельный SID, без TTY и stdin `/dev/null`, то есть не зависел от Codex/SSH.

Pre-flight: MemAvailable 5,197 GB, free disk 51,573 GB, inode usage 6%; 30s CPU sample 6–51%, swap/I/O wait 0, failed units отсутствуют. Повторный disk/RAM guard PASS.

| Показатель | Факт |
|---|---:|
| Health samples во время LIVE | 453, примерно каждые 15–16 s |
| Host CPU среднее / максимум интервала | 41,71% / 58,48% |
| Collector CPU time | 2784,925 s |
| MemAvailable минимум | 4,9766 GB |
| prediction.slice memory максимум, включая file cache | 415,14 MB |
| Anonymous memory максимум / конец | 79,54 / 78,51 MB |
| MemoryHigh / max / OOM / OOM kill events | 0 / 0 / 0 / 0 |
| Host I/O wait среднее / максимум | 0,1597% / 0,2688% |
| Host read среднее / максимум интервала | 0,0434 / 8,7210 MB/s |
| Host write среднее / максимум интервала | 0,5470 / 9,8710 MB/s |
| Receive queue пик отдельного соединения | 442 frames / 274980 bytes |
| Queue в 453 health snapshots / overflow | 0 / 0 |
| Максимальный parsed-frame → completed recv lag | 1667,36 ms |
| Максимальный возраст очереди: рабочие snapshots / с shutdown | 3689,83 / 5010,80 ms |
| Максимальный durable lag: рабочие snapshots / с shutdown | 3641,28 / 6729,35 ms |
| Максимальный flush/fsync | 107,85 ms |
| Исходные файлы / gzip | 1661 / 1656 |
| Размер исходных файлов | 479729601 bytes |
| Gzip growth без локальной производной аналитики | 239,63 MB/h |

Anonymous memory после первых 30 минут: 78,45 → 77,62 → 77,63 → 78,51 MB; устойчивого линейного роста в оставшихся 90 минутах не видно. Это двухчасовое наблюдение, не доказательство отсутствия утечки за сутки. Средний host CPU по четвертям: 40,66% / 42,52% / 39,73% / 43,86%; монотонного роста нет. I/O относится ко всему shared host, не только collector. Короткие пики между health samples могут быть пропущены.

Перцентили — nearest-rank empirical CDF, **разные выборки не смешиваются**:

| Lag, ms | n | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|
| Writer processing, периодические health samples | 453 | 0,180 | 1,347 | 4,777 | 41,627 |
| Timer excess lag, только записанные события >100 ms | 3351 | 440,711 | 2030,886 | 2251,081 | 3101,885 |
| Receive, все сообщения | не сохраняется | N/A | N/A | N/A | 1667,359 |

Timer-перцентили условны по lag >100 ms и не описывают все ticks. Writer snapshots не являются per-message latency distribution; receive до TCP/TLS parser не измерен. Нулевой backlog в периодических snapshots не означает отсутствие кратковременных очередей. Peak 442/512 означает около 86% лимита по frames; запас ограничен. Рост durable lag до 6,73 s относится к финальному закрытию, когда RAW уже не применяется к VALID-книге, но ожидает окончательного fsync.

Reconnect в пределах того же venue/market: **Polymarket 3, Limitless 9**; штатная ротация исключена. Подключено по 26 разных рынков каждой площадки (current + next), CONNECTED 29/35. Причины: Polymarket — 2 recovery deadline и 1 continuity invalidation; Limitless — 7 version regression, 1 initial snapshot missing, 1 connection close. Discovery 404 следующего Limitless market: 32 записи, с повторными попытками. Polymarket BBO DESYNC diagnostics — **8430**, recoveries — **8426**; это события по исходам, не число независимых аварий/окон. Recovery duration p50/p95/p99/max: 3,429 / 29,444 / 63,436 / 841,772 ms. Существующие safety checks не снимались.

Duration-weighted coverage по всему фактическому интервалу, включая начальную и финальную неполные минуты: **99,1906% cross-venue VALID**, строгая свежесть обеих книг 2s — **66,0300%**. VALID использует существующие book TTL 30s/60s и не означает 2s freshness. Polymarket DESYNC **43,696 s**, Limitless DESYNC **15,849 s**, объединённый cross-venue DESYNC **54,328 s**; cross-venue RECOVERING **3,323 s**, MISSING **0,681 s**, STALE **0 s** по указанным TTL. Полная coverage не равна strategy coverage после исключений.

Проверены **2373763 observations**, **30618461 priced rows**, **1032 matching records**, расхождений нет; **2614207 null/unpriced rows** явно исключены из проверки цены. Verifier обнаружил **7 late Limitless version-regression интервалов**, raw→INVALID **0,612–1,104 ms**, **0 priced rows** в этих интервалах. Консервативные strategy exclusions — 4 окна: `1789032600`, `1789032900`, `1789033200`, `1789033500`. Они не замалчиваются и не объявляются пригодными для shadow. Длительного ожидания async close до INVALID в этих событиях нет, но этот sample не заменяет проверку всех возможных транспортных отказов.

## E. Влияние на существующие сервисы

После smoke native Hermes dashboard/gateway/workspace, Nginx, Docker/containerd сохранили ActiveEnterTimestamp и NRestarts. Рабочие контейнеры сохранили RestartCount/StartedAt/OOMKilled; их health отслеживался каждые 15 секунд. Новых kernel OOM и failed units не обнаружено.

Исключение baseline: Traefik уже находился в постоянном restart loop до deployment. Между 08:37 и 09:22 UTC RestartCount изменился 35143 → 35188; в итоговом снимке 11:27 UTC — 35312, примерно прежняя частота один restart в минуту. Другие container RestartCount/StartedAt/OOMKilled и native unit timestamps/NRestarts не изменились. Traefik не изменялся.

**Важное ограничение:** `abc-rap-collector-1` и `abc-rap-maker-shadow-observer-1` были unhealthy уже в preflight 08:38:02 UTC, до первого prediction process 08:38:15 UTC, и периодически меняли healthy/unhealthy во время staging. Нельзя утверждать, что все соседи были здоровы непрерывно, либо что отсутствие влияния доказано. В прочитанных health checks collector встречались timeout; рестартов/OOM не добавилось. Для причинного сравнения нет длительного baseline без prediction и прикладных latency/SLO. Это основание ограниченного, а не безусловного допуска к 24h. Соседние сервисы не исправлялись.

Во время LIVE 2h из 453 samples: ABC collector unhealthy **63**, maker-shadow-observer unhealthy **53**; остальные работающие контейнеры с healthcheck, кроме уже зацикленного Traefik, healthy во всех снимках. `Up` без healthcheck не трактуется как проверенное прикладное здоровье.

## F. Replay и backup

- Smoke: **532087 Polymarket + 9408 Limitless raw frames**, **2108 + 207 checkpoints**, 0 mismatches, обе площадки consistent.
- 2h: **2539444 Polymarket + 35803 Limitless raw frames**, **10202 + 841 checkpoints**, 0 mismatches. Все **1661 SHA-256** совпали после off-host копирования; **1656 gzip** прошли CRC/footer, все segment IDs 0–120 закрыты. SHA-256 архива: `ff5395375638256894005fc15ab5c053776bdd67b003d8cb097ddd86e023cc07`.
- Smoke observation verifier: 492882 observations, 6375389 priced rows, 0 расхождений; protocol unsafe intervals и replay exclusions отсутствуют.
- Технический sample: 41949 + 382 raw frames, 330 + 13 checkpoints, 0 mismatches.
- Все исходные файлы smoke сверены SHA-256 после copy-only переноса на локальный компьютер. Архив smoke SHA-256: `4f067e2cf5cd22263481d89ad7a95472bc0037e191aa1864004c80f9c6467ebd`.
- Evidence локально: `C:/Users/viach/.codex/attachments/prediction-staging-evidence/`. Manifest закрытых файлов также сохранён в VPS `state/smoke-sha256.json`.
- Это проверенная off-host копия sample, не автоматический backup с подтверждённым постоянным RPO. Raw не удалялся; destructive sync не выполнялся. Replay/анализ проводились локально после закрытия файлов.

## G. Disk runway

Строгий резерв: `max(20 GB, 25% × 103865303040)` = **25,9663 GB**. Prediction data cap — 20 GB; контроль включает существующие данные, incremental gzip, фиксированные sidecars и рост state. Дополнительный запас 100 MB позволяет остановиться раньше между проверками. При >=70% FS — warning; при нарушении резерва или budget — controlled stop. Автоудаления нет.

По фактическим исходным файлам 2h: около **0,23964 GB/h**, normal 24h **5,75 GB**, ×3 за 24h **17,25 GB**. После сохранения обеих архивных копий prediction data занимает **1,1874 GB**, free **50,4685 GB**, начальный disk gate PASS. Из 20 GB cap остаётся около **18,8126 GB**; с 100 MB early-stop запасом — около **78,1 h normal / 26,0 h при ×3**, до учёта будущего роста state и соседей. Резерв FS сейчас больше оставшегося data budget; ограничивает data cap. При ×3 запас сверх 24h небольшой, поэтому 48/72/96h не рекомендуются на этом измерении.

Во время LIVE свободное место всего хоста уменьшилось на 0,618 GB, больше объёма collector 0,480 GB; разница включает соседние записи и state. Проекции не включают будущий рост соседей и новый полный tar после 24h: такую копию нельзя автоматически считать помещающейся в 20 GB cap. Автоудаления нет; повторный preflight обязателен непосредственно перед отдельно разрешённым запуском.

## H. Лимиты и lifecycle

`prediction.slice`: CPUQuota=60% одного ядра, CPUWeight=20, MemoryHigh=1536M, MemoryMax=1792M, MemorySwapMax=0, TasksMax=128, IOWeight=20. Collector: MemoryHigh=1280M, MemoryMax=1536M, TasksMax=64, LimitNOFILE=4096. Status: 64M / 8 tasks; health: 96M / 16 tasks.

Quota срабатывала: в smoke среднее приращение `throttled_usec` около 0,097 секунды на секунду elapsed, max sample 0,357; в 2h — **0,155 s/s mean, 0,382 s/s max sample**. Это cgroup counter, не доля потерянных WS messages. Лимиты не повышались. MemoryHigh/OOM events отсутствуют.

SIGTERM и новый каталог проверены реальным техническим sample; owner flock отклонил второго writer. SIGTERM/SIGINT подключены к одному stop event. Boot ID, process start ticks, PID, release и run/session identity сохранены. Restart=no не продлевает sample автоматически. Host CPU >=70% sustained 30s либо MemAvailable <3 GB приводит к остановке только prediction.

Writer синхронный raw-before-apply; отдельной writer queue нет. Receive telemetry относится к разобранным WS data frames до completed recv, не к неизвестному времени TCP/TLS arrival. При overflow >512 frames или >8 MiB VALID снимается и gap логируется. Метрики используют ограниченные 300-second bins и incremental counters; тест запрещает исторический glob. Parser/transport/protocol failure инвалидирует книги до длительного async close; slow-close tests PASS.

## I. Ошибки и ограничения

1. Первая упаковка git archive изменила line endings systemd-файлов. SHA-256 gate остановил deployment; исправленная упаковка читает точные Git blobs. Отклонённый код не запускался.
2. Первоначальная telemetry не измеряла receive backlog; технический sample остановлен через SIGTERM, затем выпущен `229aff0` и полный smoke начат заново.
3. Windows fsync read-only descriptor вызвал ошибку локальных тестов; режим исправлен до первого запуска, тесты прошли.
4. `systemd-analyze verify` показал существующие неизвестные directives в snapd/Hermes units. Prediction units прошли проверку; соседние конфигурации не исправлялись.
5. Baseline 2s coverage существенно ниже основной TTL coverage. Settlement/fees остаются UNKNOWN; infra PASS не разрешает shadow/orders.
6. Reboot/power-loss recovery и непрерывный off-host backup не проверялись в shared staging.
7. Полные per-message receive lag p50/p95/p99 отсутствуют; есть максимум и ограниченные выборки других lag. Пик очереди 442/512, CPU throttling и durable shutdown lag 6,73 s не позволяют объявить low-latency gate пройденным.
8. 4 окна консервативно исключены verifier из strategy scope после version regression; данные не удалены. Исследовательский analyzer сохраняет MORE DATA REQUIRED, что не противоречит ограниченному инфраструктурному verdict.

## J. Решение

**PASS_WITH_LIMITS — только 24h collector-only, без автоматического запуска.**

| Проверка | Результат |
|---|---|
| Фактические 30m + 2h, bounded stop, отдельный systemd process | PASS |
| CPU <70% в health samples, RAM >=3 GB, отсутствие OOM | PASS в пределах измеренного интервала |
| SHA-256, gzip CRC/footer, закрытие всех сегментов | PASS |
| Raw/checkpoint replay и все non-null observation rows | PASS; 4 conservative strategy exclusions |
| Очередь без накопления и overflow | PASS_WITH_LIMITS: пик 86% frame cap, snapshots не покрывают каждый момент |
| Lag/durability | PASS_WITH_LIMITS: receive quantiles отсутствуют, seconds-scale tails |
| Диск под 24h с ×3 write rate | PASS_WITH_LIMITS: около 2h запаса до cap, повторный preflight обязателен |
| Влияние на соседей | PASS_WITH_LIMITS: новых restart/OOM нет; baseline health нестабилен, SLO не измерены |

Для обычного публичного collector ресурсы и целостность подтверждены на двух часах. Это не доказательство непрерывной 24h устойчивости, пригодности 100/250ms стратегии, settlement/fee correctness или отсутствия влияния на прикладные SLO соседей.

## K. Следующая разрешаемая длительность

Следующий кандидат — **не более 24h collector-only**, только отдельным заданием и после свежего resource/disk gate. Перед таким запуском необходимо отдельно согласовать длительность: текущий launcher намеренно принимает только `1..7200` секунд, а unit ограничен `2h50s`; сейчас эти ограничения не менялись. Для безусловного допуска нужны per-message receive lag quantiles и более качественный baseline соседних сервисов. Существующие CPU/RAM/disk guards и запрет удаления raw сохраняются.

На завершение работы `prediction-collector.service`, `prediction-status.service`, `prediction-health.timer`, `prediction-health.service` — **inactive**, boot enable отсутствует (`static`). Локальный SSH tunnel закрыт. Никаких новых collector runs, 24h/48h/72h/96h, shadow или orders не запущено. Серверный raw сохранён; следующий запуск автоматически не планировался.
