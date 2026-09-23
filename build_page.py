"""Build the nightly HTML report (report.html) from results.json."""

from __future__ import annotations

import html
import json
import sys


def esc(x):
    return html.escape(str(x)) if x is not None else ""


def num(x, nd=2, pct=False, dash="—"):
    if x is None:
        return dash
    try:
        v = float(x)
    except (TypeError, ValueError):
        return dash
    if pct:
        return f"{v * 100:.{nd}f}%"
    return f"{v:,.{nd}f}"


def strike_txt(x):
    return "—" if x is None else f"{float(x):g}"


def compact(n):
    if n is None:
        return "—"
    n = float(n)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return f"{n / div:.1f}{suf}"
    return f"{n:.0f}"


CSS = """
:root{
  --bg:#F2F3EF; --surface:#FFFFFF; --ink:#1A211E; --muted:#5C6862; --line:#DADFD9;
  --accent:#1F5A78; --floor:#2B7049; --floor-soft:#DCEBDF; --ceil:#A8413A; --ceil-soft:#F3DEDA;
  --warn:#8A5A00; --warn-soft:#F6E8C8; --spot:#1F5A78;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme:dark;
    --bg:#0F1412; --surface:#161D1A; --ink:#E2E8E4; --muted:#95A29B; --line:#2A3430;
    --accent:#79B6D3; --floor:#62C08A; --floor-soft:#1B3325; --ceil:#E0806F; --ceil-soft:#3A221E;
    --warn:#E8B85C; --warn-soft:#352914; --spot:#79B6D3;
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --bg:#0F1412; --surface:#161D1A; --ink:#E2E8E4; --muted:#95A29B; --line:#2A3430;
  --accent:#79B6D3; --floor:#62C08A; --floor-soft:#1B3325; --ceil:#E0806F; --ceil-soft:#3A221E;
  --warn:#E8B85C; --warn-soft:#352914; --spot:#79B6D3;
}
body{background:var(--bg);color:var(--ink);font-family:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  font-size:15px;line-height:1.5;padding-inline:16px;padding-block:24px 64px}
.wrap{max-width:1080px;margin:0 auto;display:flex;flex-direction:column;gap:28px}
h1,h2,h3{font-family:"Archivo","IBM Plex Sans",system-ui,sans-serif;text-wrap:balance;margin:0;letter-spacing:-.01em}
h1{font-size:28px;font-weight:700}
h2{font-size:22px;font-weight:700}
h3{font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
p{margin:0}
a{color:var(--accent)}
a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.mono,.num,td,th{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums}
.muted{color:var(--muted)}
header.top{display:flex;flex-direction:column;gap:8px}
.filters{display:flex;flex-wrap:wrap;gap:6px}
.chip{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:12px;padding:2px 8px;border:1px solid var(--line);
  border-radius:4px;background:var(--surface);color:var(--muted);white-space:nowrap}
.chip.floor{color:var(--floor);border-color:var(--floor)}
.chip.ceil{color:var(--ceil);border-color:var(--ceil)}
.chip.warn{color:var(--warn);background:var(--warn-soft);border-color:transparent}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px}
th{font-weight:500;color:var(--muted);text-align:right;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap;font-size:12px}
td{text-align:right;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
td.l,th.l{text-align:left}
.overview td a{font-weight:600;text-decoration:none}
.floor-t{color:var(--floor);font-weight:600}
.ceil-t{color:var(--ceil);font-weight:600}
section.co{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:20px;display:flex;flex-direction:column;gap:18px;scroll-margin-top:12px}
.co-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px}
.co-head .tk{font-family:"Archivo",sans-serif;font-size:30px;font-weight:800;letter-spacing:-.02em}
.co-head .price{font-family:"IBM Plex Mono",monospace;font-size:20px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px}
.stat{display:flex;flex-direction:column;gap:2px}
.stat .k{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.stat .v{font-family:"IBM Plex Mono",monospace;font-size:18px}
.stat .v.floor{color:var(--floor)} .stat .v.ceil{color:var(--ceil)}
.grid2{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);gap:24px}
@media (max-width:820px){.grid2{grid-template-columns:minmax(0,1fr)}}
.block{display:flex;flex-direction:column;gap:8px;min-width:0}
.ladder td{padding:3px 6px}
.ladder .bar{height:10px;border-radius:2px;display:block}
.ladder .pbar{background:var(--floor);margin-left:auto}
.ladder .cbar{background:var(--ceil)}
.ladder .bcell{width:28%}
.ladder tr.is-floor td{background:var(--floor-soft)}
.ladder tr.is-ceil td{background:var(--ceil-soft)}
.ladder tr.spot td{border-bottom:2px solid var(--spot);padding:0;height:0}
.ladder .spotlbl{font-size:11px;color:var(--spot);text-align:center;padding:2px 0}
.tag{font-size:10px;font-weight:600;letter-spacing:.05em;padding:1px 5px;border-radius:3px}
.tag.floor{background:var(--floor);color:var(--surface)} .tag.ceil{background:var(--ceil);color:var(--surface)}
.about{max-width:70ch;color:var(--muted);font-size:14px}
.links{display:flex;flex-wrap:wrap;gap:14px;font-size:14px}
.empty{color:var(--muted);font-size:13px;font-style:italic}
.funnel{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13px;color:var(--muted)}
.note{font-size:13px;color:var(--muted);max-width:75ch}
.pill{font-family:"IBM Plex Mono",monospace;font-size:12px;padding:1px 7px;border-radius:10px}
.pill.pos{background:var(--floor-soft);color:var(--floor)}
.pill.neg{background:var(--ceil-soft);color:var(--ceil)}
"""


def ladder(c):
    rows = c.get("strike_map") or []
    if not rows:
        return '<p class="empty">Sin datos de la cadena de opciones.</p>'
    spot = c["spot"]
    mx = max([max(r["put_oi"], r["call_oi"]) for r in rows] + [1])
    out = ['<div class="scroll"><table class="ladder"><thead><tr>'
           '<th>Vol puts</th><th class="bcell">OI puts</th><th class="l" style="text-align:center">Strike</th>'
           '<th class="bcell l">OI calls</th><th>Vol calls</th><th class="l"></th></tr></thead><tbody>']
    spot_done = False
    for r in rows:  # high -> low
        if not spot_done and r["strike"] < spot:
            out.append(f'<tr class="spot"><td colspan="6"></td></tr>'
                       f'<tr><td colspan="6" class="spotlbl">precio {num(spot)}</td></tr>')
            spot_done = True
        cls = ""
        tag = r.get("tag") or ""
        if "PISO" in tag:
            cls = "is-floor"
        elif "TECHO" in tag:
            cls = "is-ceil"
        pw = 100 * r["put_oi"] / mx
        cw = 100 * r["call_oi"] / mx
        label = ""
        if tag:
            parts = []
            if "PISO 2" in tag:
                parts.append('<span class="tag floor">PISO 2</span>')
            elif "PISO" in tag:
                parts.append('<span class="tag floor">PISO</span>')
            if "TECHO 2" in tag:
                parts.append('<span class="tag ceil">TECHO 2</span>')
            elif "TECHO" in tag:
                parts.append('<span class="tag ceil">TECHO</span>')
            label = " ".join(parts)
        out.append(
            f'<tr class="{cls}"><td>{r["put_vol"]:,}</td>'
            f'<td class="bcell" title="{r["put_oi"]:,} puts abiertos"><span class="bar pbar" style="width:{pw:.1f}%"></span>'
            f'<span class="muted" style="font-size:11px">{r["put_oi"]:,}</span></td>'
            f'<td style="text-align:center;font-weight:600">{strike_txt(r["strike"])}</td>'
            f'<td class="bcell l" title="{r["call_oi"]:,} calls abiertos"><span class="bar cbar" style="width:{cw:.1f}%"></span>'
            f'<span class="muted" style="font-size:11px">{r["call_oi"]:,}</span></td>'
            f'<td>{r["call_vol"]:,}</td><td class="l">{label}</td></tr>')
    if not spot_done:
        out.append(f'<tr class="spot"><td colspan="6"></td></tr>'
                   f'<tr><td colspan="6" class="spotlbl">precio {num(spot)}</td></tr>')
    out.append("</tbody></table></div>")
    return "".join(out)


def contracts_table(items, title):
    if not items:
        return f'<div class="block"><h3>{title}</h3><p class="empty">Ninguno.</p></div>'
    rows = "".join(
        f'<tr><td class="l {"floor-t" if i["type"] == "PUT" else "ceil-t"}">{i["type"]}</td>'
        f'<td>{strike_txt(i["strike"])}</td><td class="l">{esc(i["expiration"])}</td><td>{i["dte"]}</td>'
        f'<td>{i["oi"]:,}</td><td>{i["volume"]:,}</td><td>{num(i["last"])}</td><td>{num(i["iv"], 0, pct=True)}</td></tr>'
        for i in items)
    return (f'<div class="block"><h3>{title}</h3><div class="scroll"><table><thead><tr>'
            f'<th class="l">Tipo</th><th>Strike</th><th class="l">Vence</th><th>DTE</th><th>OI</th><th>Volumen</th>'
            f'<th>Último</th><th>IV</th></tr></thead><tbody>{rows}</tbody></table></div></div>')


def puts_table(c):
    puts = c.get("puts") or []
    if not puts:
        return '<p class="empty">Ningún put semanal pasa los filtros.</p>'
    rows = []
    for p in puts:
        below = p.get("strike_below_support")
        rows.append(
            f'<tr><td class="l">{esc(p["expiration"])}</td><td>{p["dte"]}</td><td><b>{strike_txt(p["strike"])}</b></td>'
            f'<td>{num(p["premium"])}</td><td>{num(p.get("bid"))} / {num(p.get("ask"))}</td>'
            f'<td>{num(p["premium_yield"], 2, pct=True)}</td><td>{num(p["delta"])}</td>'
            f'<td>{num(p["iv"], 0, pct=True)}</td><td>{p["oi"]:,}</td><td>{p["volume"]:,}</td>'
            f'<td>{num(p["distance_from_spot"], 1, pct=True)}</td>'
            f'<td class="l">{"<span class=floor-t>debajo del piso</span>" if below else "arriba del piso"}</td></tr>')
    src = puts[0].get("premium_source") or ""
    return ('<div class="scroll"><table><thead><tr><th class="l">Vence</th><th>DTE</th><th>Strike</th><th>Prima</th>'
            '<th>Bid / Ask</th><th>Rend.</th><th>Delta</th><th>IV</th><th>OI</th><th>Vol</th><th>Dist.</th>'
            f'<th class="l">vs piso</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
            f'<p class="note">Prima = {esc(src)}. Rend. = prima ÷ strike (colateral en efectivo).</p>')


def company(c):
    tk = esc(c["ticker"])
    regime = c.get("gamma_regime")
    rpill = (f'<span class="pill {"pos" if regime == "positivo" else "neg"}">gamma {esc(regime)}</span>'
             if regime else "")
    earn = c.get("earnings_days")
    chips = [f'<span class="chip">{esc(c.get("sector") or "")}</span>' if c.get("sector") else "",
             f'<span class="chip">earnings en {int(earn)} días</span>' if earn is not None else
             '<span class="chip">earnings: sin fecha</span>',
             rpill]
    if c.get("bullish_divergence"):
        chips.append('<span class="chip floor">divergencia alcista RSI</span>')
    flip = c.get("gamma_flip")
    stats = [
        ("Beta (1 año)", num(c.get("beta"), 2), ""),
        ("RSI diario", num(c.get("rsi"), 1), ""),
        ("P/E", num(c.get("pe"), 1), ""),
        ("P/E forward", num(c.get("forward_pe"), 1), ""),
        ("Piso", strike_txt(c.get("floor")), "floor"),
        ("Piso 2", strike_txt(c.get("floor_2")), "floor"),
        ("Techo", strike_txt(c.get("ceiling")), "ceil"),
        ("Techo 2", strike_txt(c.get("ceiling_2")), "ceil"),
        ("Gamma flip", num(flip) if flip is not None else "—", ""),
        ("Cap. mercado", compact(c.get("market_cap")), ""),
    ]
    stats_html = "".join(f'<div class="stat"><span class="k">{k}</span><span class="v {cls}">{v}</span></div>'
                         for k, v, cls in stats)
    bc = c.get("big_contracts") or {}
    about = esc(c.get("business_summary") or "")
    links = [f'<a href="https://finance.yahoo.com/quote/{tk}/profile" target="_blank" rel="noopener">Perfil (Yahoo)</a>',
             f'<a href="https://finviz.com/quote.ashx?t={tk}" target="_blank" rel="noopener">Finviz</a>',
             f'<a href="https://finance.yahoo.com/quote/{tk}/options" target="_blank" rel="noopener">Option chain</a>',
             f'<a href="https://www.nasdaq.com/market-activity/stocks/{tk.lower()}/earnings" target="_blank" rel="noopener">Earnings</a>']
    if c.get("website"):
        links.append(f'<a href="{esc(c["website"])}" target="_blank" rel="noopener">Sitio web</a>')
    return f"""
<section class="co" id="{tk}">
  <div class="co-head"><span class="tk">{tk}</span><span class="muted">{esc(c.get("name") or "")}</span>
    <span class="price">${num(c["spot"])}</span></div>
  <div class="filters">{"".join(chips)}</div>
  <div class="stats">{stats_html}</div>
  <div class="grid2">
    <div class="block"><h3>20 strikes alrededor del precio · OI y volumen (vencimientos ≤ 60 días)</h3>{ladder(c)}</div>
    <div class="block" style="gap:18px">
      <div class="block"><h3>Puts semanales que pasan los filtros</h3>{puts_table(c)}</div>
      {contracts_table(bc.get("unusual"), "Volumen inusual hoy (volumen &gt; OI)")}
    </div>
  </div>
  <div class="grid2">
    {contracts_table(bc.get("by_oi"), "Contratos con más open interest")}
    {contracts_table(bc.get("by_volume"), "Contratos con más volumen hoy")}
  </div>
  <div class="block"><h3>Background</h3><p class="about">{about or "Sin descripción disponible."}</p>
    <div class="links">{"".join(links)}</div></div>
</section>"""


def overview(companies):
    rows = []
    for c in companies:
        best = max(c.get("puts") or [], key=lambda p: p["premium_yield"], default=None)
        put_txt = (f'{strike_txt(best["strike"])}P {esc(best["expiration"][5:])} · ${num(best["premium"])} '
                   f'({num(best["premium_yield"], 2, pct=True)})') if best else "—"
        rows.append(
            f'<tr><td><a href="#{esc(c["ticker"])}">{esc(c["ticker"])}</a></td><td>{num(c["spot"])}</td>'
            f'<td>{num(c.get("beta"), 2)}</td><td>{num(c.get("rsi"), 1)}</td><td>{num(c.get("pe"), 1)}</td>'
            f'<td class="floor-t">{strike_txt(c.get("floor"))}</td><td class="ceil-t">{strike_txt(c.get("ceiling"))}</td>'
            f'<td class="l">{esc(c.get("gamma_regime") or "—")}</td>'
            f'<td>{"—" if c.get("earnings_days") is None else int(c["earnings_days"])}</td>'
            f'<td class="l">{put_txt}</td></tr>')
    return ('<div class="scroll"><table class="overview"><thead><tr><th class="l">Ticker</th><th>Precio</th>'
            '<th>Beta</th><th>RSI</th><th>P/E</th><th>Piso</th><th>Techo</th><th class="l">Gamma</th><th>Earnings (días)</th>'
            f'<th class="l">Put semanal con más prima</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def build(data):
    f = data["filters"]
    companies = data["companies"]
    chips = [
        f'precio ${f["price"][0]:g}–${f["price"][1]:g}',
        f'beta ≥ {f.get("min_beta", 0):g}',
        f'RSI diario {f["rsi"][0]:g}–{f["rsi"][1]:g}',
        f'P/E 0–{f["max_pe"]:g}',
        f'prima ≥ {f["min_weekly_yield"] * 100:.1f}% semanal',
        f'delta {f["delta"][0]:.2f}–{f["delta"][1]:.2f}',
        f'vence en {f["dte"][0]}–{f["dte"][1]} días',
        f'cap. ≥ {compact(f["min_market_cap"])}',
    ]
    funnel = "".join(f"<span>{esc(k)}: <b>{v}</b></span>" for k, v in
                     sorted(data.get("funnel", {}).items(), key=lambda kv: -kv[1]))
    body = "".join(company(c) for c in companies) if companies else \
        '<p class="empty">Hoy ninguna compañía pasó todos los filtros.</p>'
    return f"""<title>Puts Semanales</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@600;700;800&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>{CSS}</style>
<div class="wrap">
  <header class="top">
    <h3>Escaneo del {esc(data["generated_at"])}</h3>
    <h1>{len(companies)} compañías para vender puts semanales</h1>
    <div class="filters">{"".join(f'<span class="chip">{esc(x)}</span>' for x in chips)}</div>
    <div class="funnel"><span>Universo: <b>{data.get("universe_size", "—")}</b></span>{funnel}</div>
  </header>
  {overview(companies)}
  <p class="note"><span class="floor-t">Piso</span>: strike debajo del precio con más gamma de puts.
  <span class="ceil-t">Techo</span>: strike arriba del precio con más gamma de calls. Se calculan con los 20 strikes
  alrededor del precio y todos los vencimientos de hasta 60 días. Son estimaciones con datos públicos de Yahoo,
  no soportes garantizados. Gamma negativo = los movimientos tienden a amplificarse.</p>
  {body}
</div>
"""


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "results.json"
    dst = sys.argv[2] if len(sys.argv) > 2 else "report.html"
    with open(src, encoding="utf-8") as fh:
        data = json.load(fh)
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(build(data))
    print(f"Wrote {dst} ({len(data['companies'])} companies)")
