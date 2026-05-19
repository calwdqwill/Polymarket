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
uvicorn app.main:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/api/status
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
curl http://127.0.0.1:8000/api/status/sources
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

Для внешнего доступа лучше поставить Nginx reverse proxy и TLS, а backend оставить на `127.0.0.1:8000`.

## Frontend

На сервере:

```bash
cd /opt/poly_crypto/apps/web
npm ci
NEXT_PUBLIC_API_BASE_URL=https://<DOMAIN_OR_API_HOST> npm run build
npm run start -- -p 3000
```

Для production лучше добавить отдельный systemd service для frontend или использовать reverse proxy/PM2. Для MVP достаточно убедиться, что сборка проходит и dashboard видит API.

## Контроль после деплоя

Проверить:

```bash
curl http://127.0.0.1:8000/api/status
curl http://127.0.0.1:8000/api/status/sources
systemctl status poly-crypto-api
systemctl status poly-crypto-chainlink-worker
journalctl -u poly-crypto-chainlink-worker -n 100
```

Критерий готовности серверного MVP:

- API отвечает;
- dashboard открывается;
- `chainlink_streams` live;
- свечи и ticks растут;
- worker переживает restart процесса;
- секреты не лежат в Git.
