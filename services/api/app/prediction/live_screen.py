from datetime import datetime, timezone
from html import escape

from app.prediction.live_storage import atomic_text


def render_live(state, target):
    def text(value):
        return escape(str(value)) if value is not None else "—"

    def table(headers, rows):
        return (
            "<table><tr>"
            + "".join("<th>" + text(h) + "</th>" for h in headers)
            + "</tr>"
            + "".join("<tr>" + "".join("<td>" + text(v) + "</td>" for v in row) + "</tr>" for row in rows)
            + "</table>"
        )

    top = table(["Venue", "YES bid", "YES ask", "NO bid", "NO ask", "Age ms", "Status"], state["top"])
    directions = ""
    for venue, label in [("Limitless", "A"), ("Polymarket", "B")]:
        rows = [r for r in state["rows"] if r["venue_yes"] == venue]
        keys = [
            "share_size",
            "yes_vwap",
            "no_vwap",
            "combined_cost",
            "observed_edge",
            "gross_pnl",
            "status",
            "limiting_venue",
        ]
        directions += (
            "<h2>Direction "
            + label
            + " · BUY YES "
            + venue
            + " + BUY NO "
            + ("Polymarket" if venue == "Limitless" else "Limitless")
            + "</h2>"
        )
        directions += table(
            ["Q", "YES VWAP", "NO VWAP", "Cost", "Observed edge", "Gross PnL", "Depth status", "Limiting venue"],
            [[r.get(k) for k in keys] for r in rows],
        )
    html = """<!doctype html><html lang="ru"><meta charset="utf-8"><meta http-equiv="refresh" content="2">
<title>BTC 5M research</title><style>body{background:#111821;color:#e5edf5;font:15px system-ui;margin:30px}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}td,th{padding:8px;border-bottom:1px solid #354050;text-align:right}th:first-child,td:first-child{text-align:left}.notice{padding:14px;background:#42351d}p{color:#b6c6d6}</style>"""
    html += (
        "<h1>BTC 5M · "
        + text(datetime.fromtimestamp(state["window"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        + "</h1><p>"
        + text(state["timestamp"])
        + " · "
        + text(state["status"])
        + "</p>"
    )
    html += (
        '<div class="notice">MATCH QUALITY = '
        + text(state["quality"])
        + "<br>RESEARCH ONLY — SETTLEMENT EQUIVALENCE NOT FULLY VERIFIED</div>"
    )
    html += (
        "<p>Executable означает только достаточную показанную depth. Gross до комиссий. NO Limitless — зеркальная ликвидность. Время локальное, не network latency.</p>"
        + top
        + directions
    )
    return atomic_text(target, html)
