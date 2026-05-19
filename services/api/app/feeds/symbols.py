from app.core.config import settings
from app.db.models import AssetSymbol


def chainlink_symbol_for_asset(asset: AssetSymbol) -> str:
    mapping = {
        AssetSymbol.BTC: settings.chainlink_symbol_btc_usd,
        AssetSymbol.ETH: settings.chainlink_symbol_eth_usd,
        AssetSymbol.SOL: settings.chainlink_symbol_sol_usd,
    }
    return mapping[asset]


def chainlink_stream_feed_for_asset(asset: AssetSymbol) -> str | None:
    mapping = {
        AssetSymbol.BTC: settings.chainlink_feed_btc_usdt,
        AssetSymbol.ETH: settings.chainlink_feed_eth_usdt,
        AssetSymbol.SOL: settings.chainlink_feed_sol_usd,
    }
    return mapping[asset]


def binance_symbol_for_asset(asset: AssetSymbol) -> str:
    mapping = {
        AssetSymbol.BTC: settings.binance_symbol_btc_usdt,
        AssetSymbol.ETH: settings.binance_symbol_eth_usdt,
        AssetSymbol.SOL: settings.binance_symbol_sol_usdt,
    }
    return mapping[asset]
