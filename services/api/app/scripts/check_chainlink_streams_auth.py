import argparse
import asyncio

from app.core.config import settings
from app.db.models import AssetSymbol
from app.feeds.chainlink_streams import ChainlinkStreamsClient
from app.feeds.symbols import chainlink_stream_feed_for_asset


async def main() -> int:
    parser = argparse.ArgumentParser(description="Check Chainlink Data Streams HMAC credentials without printing secrets.")
    parser.add_argument("--path", default="/api/v1/reports/latest", help="Data Streams API path to call.")
    parser.add_argument("--feed-id", default=None, help="Optional feedID query parameter.")
    parser.add_argument("--asset", default=None, choices=[symbol.value for symbol in AssetSymbol])
    args = parser.parse_args()

    asset = AssetSymbol(args.asset) if args.asset else None
    feed_id = args.feed_id or (chainlink_stream_feed_for_asset(asset) if asset else None)
    params = {"feedID": feed_id} if feed_id else None
    client = ChainlinkStreamsClient()

    try:
        response = await client.get(args.path, params)
    except RuntimeError as exc:
        print(f"Streams auth check cannot start: {exc}")
        return 2

    print(
        {
            "base_url": settings.chainlink_streams_base_url,
            "path": args.path,
            "asset": asset.value if asset else None,
            "feed_id_set": bool(feed_id),
            "status_code": response.status_code,
            "body_preview": response.body_preview,
        }
    )
    if response.status_code == 401:
        print("HMAC credentials were rejected, or this API key is not allowed for this endpoint/feed.")
        return 1
    if response.status_code == 200 and feed_id and response.payload:
        report = client.parse_latest_report_payload(
            response.payload,
            feed_id=feed_id,
            price_decimals=settings.chainlink_price_decimals,
        )
        print(
            {
                "parsed_price": str(report.price),
                "observations_timestamp": report.observations_timestamp.isoformat(),
                "expires_at": report.expires_at.isoformat(),
            }
        )
    print("HMAC request reached Chainlink Data Streams API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
