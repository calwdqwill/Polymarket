from html import escape
from pathlib import Path


def render_screen(state: dict, target: Path):
    def text(value):
        return escape(str(value)) if value is not None else "не подтверждено"

    markets = "".join(
        "<tr>"
        + "".join(
            f"<td>{text(value)}</td>"
            for value in (
                m.venue,
                m.canonical_market_id,
                m.market_id,
                m.start.isoformat(),
                m.end.isoformat(),
                m.settlement.get("strike"),
                m.min_order_size,
            )
        )
        + "</tr>"
        for m in state["markets"]
    )
    matches = (
        "".join(
            f"<article><h3>{text(pair['canonical_market_id'])} · {text(pair['quality'])}</h3>"
            f"<p>Отсутствует подтверждение: {text(', '.join(pair['missing']))}</p>"
            f"<p>Расхождения: {text(', '.join(pair['differences']) or 'не установлены')}</p></article>"
            for pair in state["matches"]
        )
        or "<p>Соответствующая пара пока не получена.</p>"
    )
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{text(row.get(key))}</td>"
            for key in (
                "canonical_market_id",
                "venue_yes",
                "venue_no",
                "share_size",
                "yes_vwap",
                "no_vwap",
                "combined_cost",
                "observed_edge",
                "gross_pnl",
                "status",
            )
        )
        + "</tr>"
        for row in state["observer_rows"]
    )
    errors = "".join(f"<p>{text(error)}</p>" for error in state["errors"])
    html = f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BTC 5M — проверка эквивалентности</title>
<style>body{{font:15px system-ui;background:#10151e;color:#e1e8f0;margin:32px;line-height:1.5}}
h1{{margin-bottom:4px}}p{{color:#b0c0d1}}article,.notice{{background:#1d2735;padding:16px;margin:16px 0;border-radius:10px}}
table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #354258}}
.table{{overflow:auto}}.notice{{border-left:4px solid #edbf65}}small{{color:#93a6ba}}</style>
<h1>BTC 5M · Cross-venue research</h1>
<p>Снимок: {text(state['timestamp'])}. Это сохранённый результат probe, не live-котировки.</p>
<div class="notice">{text(state['status'])}. UNKNOWN/PROVISIONAL разрешают исследовательский L2-сбор.
Книги, доступный объём и прибыль пока не измерены.</div>
<h2>Обнаруженные рынки</h2><div class="table"><table><thead><tr><th>Площадка</th><th>Окно</th><th>ID</th>
<th>Начало UTC</th><th>Конец UTC</th><th>Strike</th><th>Минимум shares</th></tr></thead><tbody>{markets}</tbody></table></div>
<h2>Settlement</h2>{matches}
<h2>Книги</h2><table><tr><th>Площадка</th><th>YES bid / ask</th><th>NO bid / ask</th><th>Age / статус</th></tr>
<tr><td>Polymarket</td><td>—</td><td>—</td><td>не собиралась</td></tr>
<tr><td>Limitless</td><td>—</td><td>—</td><td>не собиралась</td></tr>
<tr><td>DexSport</td><td>—</td><td>—</td><td>механизм L2 не подтверждён</td></tr></table>
<h2>BUY YES + BUY NO</h2><small>Gross до комиссий; UNKNOWN/PROVISIONAL — только research.</small>
<div class="table"><table><tr><th>Окно</th><th>YES</th><th>NO</th><th>Shares</th><th>YES VWAP</th><th>NO VWAP</th><th>Cost</th><th>Edge</th>
<th>Gross PnL</th><th>Статус</th></tr>{rows}</table></div><h2>Ошибки API</h2>{errors or '<p>Нет.</p>'}</html>"""
    target.write_text(html, encoding="utf-8")
