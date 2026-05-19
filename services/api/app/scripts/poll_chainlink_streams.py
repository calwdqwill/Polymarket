import asyncio

from app.workers.realtime_worker import main


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
