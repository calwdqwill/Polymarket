import asyncio
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote

import httpx

from app.prediction.models import SETTLEMENT_FIELDS, Market, decimal

POLY = "https://gamma-api.polymarket.com"
LIMITLESS = "https://api.limitless.exchange"


def parse_time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.astimezone(timezone.utc)


def normalize_poly(raw: dict, url: str) -> Market:
    slug = raw["slug"]
    if not re.fullmatch(r"btc-updown-5m-\d+", slug):
        raise ValueError("Unexpected Polymarket series")
    start = parse_time(raw["eventStartTime"])
    if int(start.timestamp()) != int(slug.rsplit("-", 1)[1]):
        raise ValueError("Slug and eventStartTime disagree")
    outcomes = json.loads(raw["outcomes"]) if isinstance(raw["outcomes"], str) else raw["outcomes"]
    tokens = json.loads(raw["clobTokenIds"]) if isinstance(raw["clobTokenIds"], str) else raw["clobTokenIds"]
    if len(outcomes) != 2 or set(outcomes) != {"Up", "Down"} or len(tokens) != 2:
        raise ValueError("Unverified outcome mapping")
    settlement = dict.fromkeys(SETTLEMENT_FIELDS)
    evidence = {}
    source = raw.get("resolutionSource")
    if source:
        settlement["reference"] = source.rstrip("/")
        evidence["reference"] = url + "#resolutionSource"
    if "greater than or equal to" in raw.get("description", ""):
        settlement["equality"] = ">="
        evidence["equality"] = url + "#description"
    config = raw.get("cryptoMarketConfig") or {}
    if config.get("twapEnabled") is True and config.get("twapLookbackSeconds") is not None:
        settlement["twap_seconds"] = str(config["twapLookbackSeconds"])
        evidence["twap_seconds"] = url + "#cryptoMarketConfig.twapLookbackSeconds"
    # TWAP configuration is evidence of lookback, not evidence of boundary selection or fallback.
    return Market(
        "Polymarket",
        str(raw["id"]),
        slug,
        raw["conditionId"],
        {"YES" if outcome == "Up" else "NO": str(token) for outcome, token in zip(outcomes, tokens)},
        start,
        parse_time(raw["endDate"]),
        "BTC",
        raw.get("active") is True and raw.get("closed") is False and raw.get("acceptingOrders") is True,
        raw.get("description", ""),
        settlement,
        evidence,
        decimal(raw["orderMinSize"]) if raw.get("orderMinSize") is not None else None,
        decimal(raw["orderPriceMinTickSize"]) if raw.get("orderPriceMinTickSize") is not None else None,
    )


def normalize_limitless(raw: dict, url: str) -> Market:
    if not re.fullmatch(r"btc-up-or-down-5-min-\d+", raw["slug"]) or raw.get("tradeType") != "clob":
        raise ValueError("Unexpected Limitless market or mechanism")
    oracle = raw.get("priceOracleMetadata") or {}
    if oracle.get("ticker") != "BTC":
        raise ValueError("Unverified underlying")
    metadata = raw.get("metadata") or {}
    stream = metadata.get("chainlinkDataStream") or {}
    settlement = dict.fromkeys(SETTLEMENT_FIELDS)
    evidence = {}
    for field, value, path in (
        ("strike", metadata.get("openPrice"), "metadata.openPrice"),
        ("reference", stream.get("streamUrl"), "metadata.chainlinkDataStream.streamUrl"),
        ("feed_id", stream.get("feedId"), "metadata.chainlinkDataStream.feedId"),
        ("precision", stream.get("priceDecimals"), "metadata.chainlinkDataStream.priceDecimals"),
        ("twap_seconds", stream.get("twapWindowSeconds"), "metadata.chainlinkDataStream.twapWindowSeconds"),
        ("payout_currency", (raw.get("collateralToken") or {}).get("symbol"), "collateralToken.symbol"),
    ):
        if field == "strike" and value is not None:
            try:
                value = decimal(value)
                if value <= 0:
                    value = None
            except ValueError:
                value = None
        if value is not None:
            settlement[field] = str(value)
            evidence[field] = url + "#" + path
    rules = raw.get("description", "")
    if "greater than or equal to" in rules:
        settlement["equality"] = ">="
        evidence["equality"] = url + "#description"
    if "first Chainlink observation within the following 5 seconds" in rules:
        settlement["fallback"] = "first_observation_in_next_5s_else_no_automatic_resolution"
        evidence["fallback"] = url + "#description"
    start = parse_time(raw["startAt"])
    if int(start.timestamp()) != int(raw["slug"].rsplit("-", 1)[1]):
        raise ValueError("Slug and startAt disagree")
    tokens = {key.upper(): str(value) for key, value in raw["tokens"].items() if key in ("yes", "no")}
    # settings.minSize belongs to liquidity rewards, not a verified trading minimum.
    return Market(
        "Limitless",
        str(raw["id"]),
        raw["slug"],
        raw["conditionId"],
        tokens,
        start,
        datetime.fromtimestamp(int(raw["expirationTimestamp"]) / 1000, timezone.utc),
        "BTC",
        raw.get("status") in ("CREATED", "FUNDED") and raw.get("expired") is False,
        rules,
        settlement,
        evidence,
        size_scale=Decimal(10) ** int(raw["collateralToken"]["decimals"]),
    )


class Discovery:
    def __init__(self, client: httpx.AsyncClient, journal):
        self.client, self.journal = client, journal
        self.errors: list[dict] = []

    async def get(self, url: str):
        for attempt in range(3):
            try:
                response = await self.client.get(url)
                self.journal.append(
                    "raw_http",
                    {
                        "url": url,
                        "status": response.status_code,
                        "server_date": response.headers.get("date"),
                        "body": response.text,
                    },
                )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        retry = response.headers.get("retry-after", "")
                        await asyncio.sleep(min(30, int(retry)) if retry.isdigit() else 2**attempt)
                        continue
                response.raise_for_status()
                return json.loads(response.text, parse_float=Decimal)
            except (httpx.HTTPError, ValueError) as exc:
                if isinstance(exc, httpx.TransportError) and attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
                error = {"url": url, "error": type(exc).__name__, "detail": str(exc)}
                self.errors.append(error)
                self.journal.append("errors", error)
                return None

    async def collect(self, now: datetime) -> list[Market]:
        self.errors = []
        window = int(now.timestamp()) // 300 * 300
        poly_urls = [f"{POLY}/markets?slug=btc-updown-5m-{start}" for start in (window, window + 300)]
        timeline_url = (
            f"{LIMITLESS}/markets/timeline?symbol=BTC&frequency=minutely" "&subFrequency=minutes_5&before=0&after=1"
        )
        responses = await asyncio.gather(*(self.get(url) for url in (*poly_urls, timeline_url)))
        markets = []
        for url, payload in zip(poly_urls, responses[:2]):
            if payload is not None:
                for raw in payload:
                    self._normalize(markets, normalize_poly, raw, url)
        timeline = responses[2]
        if isinstance(timeline, dict):
            slugs = {
                slot["slug"]
                for key in ("current", "next")
                if isinstance(slot := timeline.get(key), dict) and slot.get("slug")
            }
            urls = [f"{LIMITLESS}/markets/{quote(slug, safe='')}" for slug in sorted(slugs)]
            payloads = await asyncio.gather(*(self.get(url) for url in urls))
            for url, payload in zip(urls, payloads):
                if payload:
                    self._normalize(markets, normalize_limitless, payload, url)
        for market in markets:
            self.journal.append("markets", market)
        return markets

    def _normalize(self, markets, normalizer, raw, url):
        try:
            markets.append(normalizer(raw, url))
        except (KeyError, TypeError, ValueError) as exc:
            error = {"url": url, "error": "SCHEMA_ERROR", "detail": str(exc)}
            self.errors.append(error)
            self.journal.append("errors", error)
