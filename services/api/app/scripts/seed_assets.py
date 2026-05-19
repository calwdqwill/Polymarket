from sqlalchemy import select

from app.db.models import Asset, AssetSymbol
from app.db.session import SessionLocal

SEED_ASSETS = [
    (
        AssetSymbol.BTC,
        "Биткоин",
        {
            "chainlink_symbol_env_key": "CHAINLINK_SYMBOL_BTC_USD",
            "chainlink_feed_env_key": "CHAINLINK_FEED_BTC_USDT",
            "chainlink_quote_asset": "USDT",
        },
    ),
    (
        AssetSymbol.ETH,
        "Эфириум",
        {
            "chainlink_symbol_env_key": "CHAINLINK_SYMBOL_ETH_USD",
            "chainlink_feed_env_key": "CHAINLINK_FEED_ETH_USDT",
            "chainlink_quote_asset": "USDT",
        },
    ),
    (
        AssetSymbol.SOL,
        "Солана",
        {
            "chainlink_symbol_env_key": "CHAINLINK_SYMBOL_SOL_USD",
            "chainlink_feed_env_key": "CHAINLINK_FEED_SOL_USD",
            "chainlink_quote_asset": "USD",
        },
    ),
]


def main() -> None:
    with SessionLocal() as db:
        for symbol, name, source_config in SEED_ASSETS:
            existing = db.scalar(select(Asset).where(Asset.symbol == symbol))
            if existing:
                existing.name = name
                existing.source_config = source_config
                continue
            db.add(Asset(symbol=symbol, name=name, source_config=source_config))
        db.commit()


if __name__ == "__main__":
    main()
