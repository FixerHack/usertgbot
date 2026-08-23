"""Renders metrics/db_maintenance.jsonl into a single self-contained HTML
report: stat tiles, a broken-records trend line, a fixed/deleted bar chart,
and a run-by-run table. No external services, no CDN, no JS framework —
just a static file you can open straight from disk or serve as-is.
"""

import html
from datetime import datetime

from db_maintenance.metrics.store import RunMetrics

_CHART_WIDTH = 720
_CHART_HEIGHT = 220
_PAD = 32


def _fmt_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def _trend_svg(runs: list[RunMetrics]) -> str:
    if len(runs) < 2:
        return '<p class="empty">Замало запусків для графіка тренду (потрібно 2+).</p>'

    values = [r.broken for r in runs]
    max_val = max(values) or 1
    n = len(values)
    x_step = (_CHART_WIDTH - 2 * _PAD) / (n - 1)

    def x(i: int) -> float:
        return _PAD + i * x_step

    def y(v: int) -> float:
        return _CHART_HEIGHT - _PAD - (v / max_val) * (_CHART_HEIGHT - 2 * _PAD)

    points = [(x(i), y(v)) for i, v in enumerate(values)]
    polyline = " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
    area = f"{_PAD},{_CHART_HEIGHT - _PAD} " + polyline + f" {points[-1][0]:.1f},{_CHART_HEIGHT - _PAD}"

    dots = []
    for i, (px, py) in enumerate(points):
        r = runs[i]
        dots.append(
            f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" class="dot" data-command="{html.escape(r.command)}">'
            f"<title>{html.escape(_fmt_time(r.started_at))} · {html.escape(r.command)} · "
            f"broken={r.broken} · source={html.escape(r.source)}</title></circle>"
        )

    gridlines = []
    for frac in (0, 0.5, 1):
        gy = _CHART_HEIGHT - _PAD - frac * (_CHART_HEIGHT - 2 * _PAD)
        gridlines.append(f'<line x1="{_PAD}" y1="{gy:.1f}" x2="{_CHART_WIDTH - _PAD}" y2="{gy:.1f}" class="grid"/>')
        gridlines.append(f'<text x="4" y="{gy + 4:.1f}" class="axis-label">{round(frac * max_val)}</text>')

    return f"""
<svg viewBox="0 0 {_CHART_WIDTH} {_CHART_HEIGHT}" class="chart" role="img" aria-label="Тренд битих записів">
  {"".join(gridlines)}
  <polygon points="{area}" class="area"/>
  <polyline points="{polyline}" class="line"/>
  {"".join(dots)}
</svg>
""".strip()


def _bars_svg(runs: list[RunMetrics]) -> str:
    fix_runs = [r for r in runs if r.command == "fix"]
    if not fix_runs:
        return '<p class="empty">Ще не було жодного запуску `fix`.</p>'

    max_val = max((r.fixed + r.deleted for r in fix_runs), default=1) or 1
    n = len(fix_runs)
    slot = (_CHART_WIDTH - 2 * _PAD) / n
    bar_w = min(28, slot * 0.5)

    bars = []
    for i, r in enumerate(fix_runs):
        cx = _PAD + slot * i + slot / 2
        fixed_h = (r.fixed / max_val) * (_CHART_HEIGHT - 2 * _PAD)
        deleted_h = (r.deleted / max_val) * (_CHART_HEIGHT - 2 * _PAD)
        base_y = _CHART_HEIGHT - _PAD
        bars.append(
            f'<rect x="{cx - bar_w / 2:.1f}" y="{base_y - fixed_h:.1f}" width="{bar_w:.1f}" '
            f'height="{fixed_h:.1f}" class="bar-fixed"><title>fixed={r.fixed}</title></rect>'
        )
        bars.append(
            f'<rect x="{cx - bar_w / 2:.1f}" y="{base_y - fixed_h - deleted_h:.1f}" width="{bar_w:.1f}" '
            f'height="{deleted_h:.1f}" class="bar-deleted"><title>deleted={r.deleted}</title></rect>'
        )

    return f"""
<svg viewBox="0 0 {_CHART_WIDTH} {_CHART_HEIGHT}" class="chart" role="img" aria-label="Виправлено/видалено за запуск">
  <line x1="{_PAD}" y1="{_CHART_HEIGHT - _PAD}" x2="{_CHART_WIDTH - _PAD}" y2="{_CHART_HEIGHT - _PAD}" class="grid"/>
  {"".join(bars)}
</svg>
""".strip()


def _rows_table(runs: list[RunMetrics]) -> str:
    rows = []
    for r in reversed(runs):
        broken_cls = "bad" if r.broken > 0 else "good"
        rows.append(
            "<tr>"
            f"<td>{html.escape(_fmt_time(r.started_at))}</td>"
            f"<td><span class='pill pill-{html.escape(r.command)}'>{html.escape(r.command)}</span></td>"
            f"<td>{html.escape(r.source)}</td>"
            f"<td>{r.scanned}</td>"
            f"<td class='{broken_cls}'>{r.broken}</td>"
            f"<td>{r.fixed}</td>"
            f"<td>{r.deleted}</td>"
            f"<td>{r.duration_seconds:.2f}s</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _stat_tiles(runs: list[RunMetrics]) -> str:
    last = runs[-1]
    total_fixed = sum(r.fixed for r in runs)
    total_deleted = sum(r.deleted for r in runs)
    avg_duration = sum(r.duration_seconds for r in runs) / len(runs)

    tiles = [
        ("Запусків", str(len(runs))),
        ("Останній скан", f"{last.scanned}"),
        ("Битих зараз", f"{last.broken}"),
        ("Виправлено всього", str(total_fixed)),
        ("Видалено всього", str(total_deleted)),
        ("Середня тривалість", f"{avg_duration:.2f}s"),
    ]
    cells = "".join(f'<div class="tile"><div class="tile-value">{v}</div><div class="tile-label">{k}</div></div>' for k, v in tiles)
    return f'<div class="tiles">{cells}</div>'


_TEMPLATE = """<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<title>db_maintenance report</title>
<style>
:root {{
  --bg: #f7f8fa; --surface: #ffffff; --text: #1a1d23; --muted: #6b7280;
  --border: #e5e7eb; --accent: #4f6df5; --accent-2: #22c55e; --bad: #ef4444; --good: #16a34a;
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #14161a; --surface: #1c1f26; --text: #e8eaed; --muted: #9aa1ac; --border: #2a2e37; }}
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 32px 20px 60px; background: var(--bg); color: var(--text);
  font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
}}
.wrap {{ max-width: 920px; margin: 0 auto; }}
h1 {{ font-size: 1.4rem; margin: 0 0 4px; }}
.subtitle {{ color: var(--muted); margin: 0 0 28px; font-size: 0.9rem; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 20px; }}
.card h2 {{ font-size: 0.95rem; margin: 0 0 14px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 14px; }}
.tile {{ background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 14px; }}
.tile-value {{ font-size: 1.5rem; font-weight: 700; }}
.tile-label {{ font-size: 0.78rem; color: var(--muted); margin-top: 2px; }}
.chart {{ width: 100%; height: auto; overflow: visible; }}
.line {{ fill: none; stroke: var(--accent); stroke-width: 2.5; }}
.area {{ fill: var(--accent); opacity: 0.08; }}
.dot {{ fill: var(--accent); }}
.grid {{ stroke: var(--border); stroke-width: 1; }}
.axis-label {{ fill: var(--muted); font-size: 10px; }}
.bar-fixed {{ fill: var(--good); }}
.bar-deleted {{ fill: var(--bad); opacity: 0.85; }}
.empty {{ color: var(--muted); font-size: 0.9rem; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }}
th {{ color: var(--muted); font-weight: 600; font-size: 0.75rem; text-transform: uppercase; }}
td.bad {{ color: var(--bad); font-weight: 700; }}
td.good {{ color: var(--good); }}
.table-scroll {{ overflow-x: auto; }}
.pill {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 0.75rem; font-weight: 600; }}
.pill-check {{ background: rgba(79,109,245,0.15); color: var(--accent); }}
.pill-fix {{ background: rgba(34,197,94,0.15); color: var(--good); }}
.pill-restore {{ background: rgba(239,68,68,0.15); color: var(--bad); }}
footer {{ color: var(--muted); font-size: 0.78rem; text-align: center; margin-top: 24px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>db_maintenance — звіт</h1>
  <p class="subtitle">Згенеровано {generated_at} · {run_count} запуск(ів)</p>

  <div class="card">{tiles}</div>

  <div class="card">
    <h2>Тренд битих записів</h2>
    {trend_svg}
  </div>

  <div class="card">
    <h2>Виправлено / видалено за fix-запуск</h2>
    {bars_svg}
  </div>

  <div class="card">
    <h2>Історія запусків</h2>
    <div class="table-scroll">
    <table>
      <thead><tr>
        <th>Час</th><th>Команда</th><th>Джерело</th><th>Скановано</th><th>Биті</th><th>Виправлено</th><th>Видалено</th><th>Тривалість</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    </div>
  </div>

  <footer>db_maintenance · локальний звіт, без Prometheus/Grafana · metrics/db_maintenance.jsonl</footer>
</div>
</body>
</html>
"""


def render_html(runs: list[RunMetrics]) -> str:
    if not runs:
        return _TEMPLATE.format(
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            run_count=0,
            tiles='<p class="empty">Записів ще немає — запусти `db-maintenance check` хоча б раз.</p>',
            trend_svg="",
            bars_svg="",
            rows="",
        )

    return _TEMPLATE.format(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        run_count=len(runs),
        tiles=_stat_tiles(runs),
        trend_svg=_trend_svg(runs),
        bars_svg=_bars_svg(runs),
        rows=_rows_table(runs),
    )
