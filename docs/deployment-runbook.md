# Гайд: подготовка MVP к Git и серверу

Цель: зафиксировать минимальный путь от локального MVP к GitHub/GitLab и VPS, чтобы Chainlink worker работал 24/7 и live-история не зависела от локального компьютера.

## Текущее состояние

MVP локально готов к выкладке как рабочая версия:

- backend: FastAPI, SQLAlchemy, Alembic, SQLite для локального MVP;
- frontend: Next.js dashboard;
- исторический fallback: Binance `source = binance_klines`;
- realtime: Chainlink Streams `source = chainlink_streams`;
- локальный Windows watchdog включен, но он не работает при выключенном ПК.

Главный внешний блокер: Chainlink Candlestick historical API возвращает `401 Unauthorized` на текущих credentials. Это не блокирует MVP, потому что исторический слой закрыт Binance fallback, но для целевого Chainlink historical нужны отдельные/разрешенные credentials.

## Перед первым Git push

Проверить, что секреты не попадают в репозиторий:

- `services/api/.env` не коммитить;
- `services/api/logs/` не коммитить;
- `services/api/poly_crypto.db` не коммитить для публичного репозитория;
- API keys хранить только в `.env` или secrets менеджере сервера.

Локальная проверка:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\check-local.ps1
```

Если каталог еще не является git-репозиторием:

```powershell
git init
git add .
git commit -m "Prepare poly crypto MVP"
git branch -M main
git remote add origin <GIT_REMOTE_URL>
git push -u origin main
```

## Рекомендуемый серверный контур

Минимальный VPS-контур:

```text
VPS
├─ FastAPI backend
├─ Chainlink worker через systemd
├─ Next.js frontend
└─ SQLite на MVP или PostgreSQL/TimescaleDB как следующий production-шаг
```

Для надежного накопления истории критичен именно worker на сервере:

- сервер включен 24/7;
- `systemd` перезапускает worker после падения;
- история не зависит от локального Windows sleep/shutdown.

Текущий MVP уже развернут на VPS:

```text
IP: 155.212.183.185
Внешний URL: http://155.212.183.185:8080
API service: poly-crypto-api.service -> 127.0.0.1:18000
Web service: poly-crypto-web.service -> 127.0.0.1:13000
Worker service: poly-crypto-chainlink-worker.service
SQLite backup timer: poly-crypto-db-backup.timer
```

Порт `8080` выбран намеренно, чтобы не конфликтовать с уже существующим Nginx-сайтом на `mo-ex.online` и процессом, который использует `127.0.0.1:8000`.

## Переменные окружения backend

На сервере создать `services/api/.env`:

```text
DATABASE_URL=sqlite:///./poly_crypto.db
ENVIRONMENT=prod
LOG_LEVEL=INFO

CHAINLINK_STREAMS_BASE_URL=https://api.dataengine.chain.link
CHAINLINK_USER_ID=
CHAINLINK_API_KEY=
CHAINLINK_PRICE_DECIMALS=18
CHAINLINK_FEED_BTC_USDT=
CHAINLINK_FEED_ETH_USDT=
CHAINLINK_FEED_SOL_USD=

BINANCE_BASE_URL=https://api.binance.com
```

Для PostgreSQL позже заменить `DATABASE_URL` на:

```text
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/poly_crypto
```

Перед этим нужно добавить PostgreSQL driver в зависимости и прогнать миграции на новой базе.

## Backend на VPS

Пример команд:

```bash
cd /opt
git clone <GIT_REMOTE_URL> poly_crypto
cd /opt/poly_crypto/services/api
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python -m app.scripts.seed_assets
```

Проверка:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 18000
curl http://127.0.0.1:18000/api/status
```

## Worker через systemd

В репозитории есть шаблон:

```text
ops/linux/poly-crypto-chainlink-worker.service.example
```

Установка:

```bash
sudo cp /opt/poly_crypto/ops/linux/poly-crypto-chainlink-worker.service.example /etc/systemd/system/poly-crypto-chainlink-worker.service
sudo systemctl daemon-reload
sudo systemctl enable poly-crypto-chainlink-worker
sudo systemctl start poly-crypto-chainlink-worker
sudo systemctl status poly-crypto-chainlink-worker
```

Логи:

```bash
journalctl -u poly-crypto-chainlink-worker -f
```

Проверка live-накопления:

```bash
curl http://127.0.0.1:18000/api/status/sources
```

Ожидание:

- `chainlink_streams.is_live = true`;
- `last_tick` свежий;
- количество `candles` и `ticks` растет.

## API через systemd

Шаблон:

```text
ops/linux/poly-crypto-api.service.example
```

Установка:

```bash
sudo cp /opt/poly_crypto/ops/linux/poly-crypto-api.service.example /etc/systemd/system/poly-crypto-api.service
sudo systemctl daemon-reload
sudo systemctl enable poly-crypto-api
sudo systemctl start poly-crypto-api
sudo systemctl status poly-crypto-api
```

Для внешнего доступа лучше поставить Nginx reverse proxy и TLS, а backend оставить на loopback-адресе. В текущем VPS-контуре используется `127.0.0.1:18000`, потому что `127.0.0.1:8000` уже занят другим проектом.

## Frontend

На сервере:

```bash
cd /opt/poly_crypto/apps/web
npm ci
NEXT_PUBLIC_API_BASE_URL=https://<DOMAIN_OR_API_HOST> npm run build
npm run start -- -H 127.0.0.1 -p 13000
```

Для production/MVP на текущем VPS frontend запущен отдельным `systemd` service:

```text
ops/linux/poly-crypto-web.service.example
```

Если нет домена и на сервере уже есть другой сайт, можно использовать отдельный Nginx-порт:

```text
ops/linux/poly-crypto-nginx-8080.conf.example
```

Текущая серверная схема:

```text
browser -> http://155.212.183.185:8080 -> Nginx
Nginx /api/* -> 127.0.0.1:18000 -> FastAPI
Nginx /*     -> 127.0.0.1:13000 -> Next.js
```

Frontend build должен получать:

```bash
NEXT_PUBLIC_API_BASE_URL=http://155.212.183.185:8080 npm run build
```

## SQLite backup

На текущем VPS включен ежечасный backup:

```bash
systemctl status poly-crypto-db-backup.timer
ls -lh /opt/poly_crypto/backups/sqlite
```

Backup создается через SQLite backup API, а не простым копированием файла во время записи. Хранятся последние 48 копий.

Важно: это backup на том же сервере. Для production-нормы нужен внешний backup: второй VPS, object storage или другой внешний storage.

## Контроль после деплоя

Проверить:

```bash
curl http://127.0.0.1:18000/api/status
curl http://127.0.0.1:18000/api/status/sources
systemctl status poly-crypto-api
systemctl status poly-crypto-web
systemctl status poly-crypto-chainlink-worker
systemctl status poly-crypto-db-backup.timer
journalctl -u poly-crypto-chainlink-worker -n 100
```

Для текущего VPS:

```bash
curl http://127.0.0.1:18000/api/status
curl http://127.0.0.1:18000/api/status/sources
curl http://155.212.183.185:8080/api/status
```

Критерий готовности серверного MVP:

- API отвечает;
- dashboard открывается;
- `chainlink_streams` live;
- свечи и ticks растут;
- worker переживает restart процесса;
- секреты не лежат в Git.
