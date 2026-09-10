"""Explicit research estimates; never silently substitute an unknown fee with zero."""

from decimal import ROUND_CEILING
from decimal import Decimal as D

FEE_STATUS = "FEE_UNKNOWN"
SOURCES = {
    "Polymarket": "https://docs.polymarket.com/trading/fees",
    "Limitless": "https://docs.limitless.exchange/user-guide/fees",
}


def retained_fraction(venue, limitless_rate=D(".03")):
    return 1 - limitless_rate if venue == "Limitless" else D(1)


def fees(venue, levels, limitless_rate=D(".03")):
    """Return cash fee and withheld contracts. Round UP for the explicit estimate."""
    if venue == "Limitless":
        return D(0), (sum((q for _, q in levels), D(0)) * limitless_rate).quantize(D(".000001"), rounding=ROUND_CEILING)
    cash = sum((q * D(".07") * p * (1 - p) for p, q in levels), D(0))
    return cash.quantize(D(".00001"), rounding=ROUND_CEILING), D(0)


def description():
    return {
        "status": FEE_STATUS,
        "checked_date": "2026-09-10",
        "historical_market_fee_binding": "UNVERIFIED",
        "Polymarket": {
            "maker": "0",
            "taker_formula": "sum(q * 0.07 * p * (1-p))",
            "currency": "USDC",
            "documented_precision": "0.00001",
            "documented_min_fee": "0.00001; smaller fees may round to zero",
            "estimate_rounding": "CEILING per simulated order; actual fill partition unknown",
            "source": SOURCES["Polymarket"],
        },
        "Limitless": {
            "maker": "0",
            "taker_formula": None,
            "documented_buy_range": "0.4%-3%",
            "currency": "outcome contracts",
            "estimate": "3% withheld contracts, no tier discount",
            "rounding": "UNKNOWN; estimate CEILING to 1e-6 contracts",
            "min_fee": None,
            "source": SOURCES["Limitless"],
        },
        "gas_settlement_cost": "UNKNOWN; additional friction sensitivity, not a proven bound",
    }
