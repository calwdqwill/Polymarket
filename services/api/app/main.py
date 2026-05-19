from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import analysis, assets, candles, imbalances, status, ticks
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title="API крипто-дашборда",
        version="0.1.0",
        description="API аналитики 5-минутных свечей BTC, ETH и SOL.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(status.router, prefix="/api", tags=["status"])
    app.include_router(assets.router, prefix="/api/assets", tags=["assets"])
    app.include_router(candles.router, prefix="/api/candles", tags=["candles"])
    app.include_router(ticks.router, prefix="/api/ticks", tags=["ticks"])
    app.include_router(analysis.router, prefix="/api/analysis", tags=["analysis"])
    app.include_router(imbalances.router, prefix="/api/imbalances", tags=["imbalances"])

    return app


app = create_app()
