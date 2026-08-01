"""Self-contained HTML dashboard: strategy backtest comparison + live paper equity.

Reads existing report outputs only (strategy_compact_report.csv,
paper_portfolio_monitor.csv, paper_trade_log.csv) -- computes nothing, touches no
strategy/ranking logic, and is not part of the live nightly DSS chain. Charts are
rendered with matplotlib (already a project dependency, requirements.txt) to base64
PNGs embedded directly in the HTML, so the output is a single portable file with no
external network requests, CDN scripts, or server needed.

The live paper equity section renders a placeholder until the paper book has at
least one filled trade (paper_trade_log.csv is currently empty in production --
the nightly chain has not completed a full cycle yet) rather than showing a blank
or misleading chart.
"""

from __future__ import annotations

import base64
import html
import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

COMPACT_REPORT_COLUMNS = [
    "Experiment Name",
    "Strategy Family",
    "Net Getiri (%)",
    "CAGR (%)",
    "Max Drawdown (%)",
    "Sharpe Ratio",
    "Trade Count",
    "Win Rate (%)",
    "Strategy Excess Return vs QQQ (%)",
    "Compact Score",
]


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _load_strategy_compact_report(latest_dir: Path) -> pd.DataFrame:
    frame = _read_csv(Path(latest_dir) / "strategy_compact_report.csv")
    if frame.empty:
        return frame
    available = [column for column in COMPACT_REPORT_COLUMNS if column in frame.columns]
    frame = frame[available].copy()
    if "Compact Score" in frame.columns:
        frame = frame.sort_values("Compact Score", ascending=False).reset_index(drop=True)
    return frame


def _load_live_paper_summary(latest_dir: Path) -> dict[str, str]:
    frame = _read_csv(Path(latest_dir) / "paper_portfolio_monitor.csv")
    if frame.empty or "section" not in frame.columns:
        return {}
    summary_rows = frame[frame["section"] == "summary"]
    return {
        str(row["metric"]): str(row["value"])
        for _, row in summary_rows.iterrows()
        if pd.notna(row.get("metric"))
    }


def _load_paper_equity_series(latest_dir: Path) -> pd.DataFrame:
    frame = _read_csv(Path(latest_dir) / "paper_trade_log.csv")
    if frame.empty or "date" not in frame.columns or "paper_capital" not in frame.columns:
        return pd.DataFrame()
    series = frame[["date", "paper_capital"]].dropna()
    if series.empty:
        return pd.DataFrame()
    return series.sort_values("date").reset_index(drop=True)


def _figure_to_base64_png(figure) -> str:
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", bbox_inches="tight", dpi=110)
    plt.close(figure)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


_CHART_FACE = {"family": "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", "size": 9}


def _style_axis(axis, facecolor: str, edgecolor: str, textcolor: str, gridcolor: str) -> None:
    axis.set_facecolor(facecolor)
    axis.figure.set_facecolor(facecolor)
    for spine_name in ("top", "right"):
        axis.spines[spine_name].set_visible(False)
    for spine_name in ("left", "bottom"):
        axis.spines[spine_name].set_color(edgecolor)
    axis.tick_params(colors=textcolor, labelsize=_CHART_FACE["size"])
    axis.yaxis.grid(True, color=gridcolor, linewidth=0.6)
    axis.set_axisbelow(True)
    axis.title.set_color(textcolor)
    axis.title.set_fontsize(10.5)
    axis.title.set_fontweight("bold")
    axis.yaxis.label.set_color(textcolor)


def _bar_chart_base64(labels: list[str], values: list[float], title: str, ylabel: str, accent: str) -> str:
    figure, axis = plt.subplots(figsize=(6.6, 3.3))
    colors = [accent if value >= 0 else "#b3271e" for value in values]
    axis.bar(labels, values, color=colors, width=0.62)
    axis.set_title(title)
    axis.set_ylabel(ylabel)
    axis.axhline(0, color="#8a8375", linewidth=0.7)
    axis.tick_params(axis="x", rotation=32)
    for label in axis.get_xticklabels():
        label.set_ha("right")
    _style_axis(axis, "none", "#8a8375", "#3a352c", "#d8d2c3")
    figure.patch.set_alpha(0)
    figure.tight_layout()
    return _figure_to_base64_png(figure)


def _line_chart_base64(x: list[str], y: list[float], title: str, ylabel: str, accent: str) -> str:
    figure, axis = plt.subplots(figsize=(6.6, 3.3))
    axis.plot(x, y, color=accent, linewidth=1.8, marker="o", markersize=3.5, markerfacecolor=accent, markeredgecolor="none")
    axis.fill_between(range(len(x)), y, min(y), color=accent, alpha=0.08)
    axis.set_title(title)
    axis.set_ylabel(ylabel)
    axis.tick_params(axis="x", rotation=32)
    for label in axis.get_xticklabels():
        label.set_ha("right")
    _style_axis(axis, "none", "#8a8375", "#3a352c", "#d8d2c3")
    figure.patch.set_alpha(0)
    figure.tight_layout()
    return _figure_to_base64_png(figure)


_SIGNED_COLUMNS = {"Sharpe Ratio", "Net Getiri (%)", "Strategy Excess Return vs QQQ (%)"}


def _format_cell(column: str, value) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, (int, float)):
        return f"{value:.2f}"
    return html.escape(str(value))


def _html_table(frame: pd.DataFrame) -> str:
    header_cells = "".join(f"<th>{html.escape(str(column))}</th>" for column in frame.columns)
    body_rows = []
    for _, row in frame.iterrows():
        cells = []
        for column in frame.columns:
            value = row[column]
            text = _format_cell(column, value)
            css_class = ""
            if column in _SIGNED_COLUMNS and isinstance(value, (int, float)) and not pd.isna(value):
                css_class = ' class="pos"' if value >= 0 else ' class="neg"'
            elif column == "Experiment Name":
                css_class = ' class="name-cell"'
            cells.append(f"<td{css_class}>{text}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
    return f'<table class="metrics-table"><thead><tr>{header_cells}</tr></thead><tbody>{"".join(body_rows)}</tbody></table>'


def _summary_cards_html(compact: pd.DataFrame) -> str:
    cards = [("Variants compared", str(len(compact)))]
    if "Sharpe Ratio" in compact.columns and not compact["Sharpe Ratio"].isna().all():
        best_sharpe = compact.loc[compact["Sharpe Ratio"].idxmax()]
        cards.append(("Best Sharpe", f'{best_sharpe["Sharpe Ratio"]:.2f}', html.escape(str(best_sharpe.get("Experiment Name", "")))))
    if "Net Getiri (%)" in compact.columns and not compact["Net Getiri (%)"].isna().all():
        best_return = compact.loc[compact["Net Getiri (%)"].idxmax()]
        cards.append(("Best net return", f'{best_return["Net Getiri (%)"]:.1f}%', html.escape(str(best_return.get("Experiment Name", "")))))
    html_cards = []
    for card in cards:
        if len(card) == 3:
            label, value, sub = card
            html_cards.append(f'<div class="card"><span class="card-label">{label}</span><span class="card-value">{value}</span><span class="card-sub">{sub}</span></div>')
        else:
            label, value = card
            html_cards.append(f'<div class="card"><span class="card-label">{label}</span><span class="card-value">{value}</span></div>')
    return f'<div class="card-row">{"".join(html_cards)}</div>'


_STYLE = """
:root {
  --bg: #f5f3ee;
  --surface: #ffffff;
  --text: #1c1a17;
  --text-muted: #6b6558;
  --border: #ddd7c9;
  --accent: #a8681a;
  --positive: #1f7a4d;
  --negative: #b3271e;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14120f;
    --surface: #1d1a15;
    --text: #ece7dc;
    --text-muted: #9c9585;
    --border: #322d24;
    --accent: #d9a441;
    --positive: #56b98a;
    --negative: #e07a72;
  }
}
:root[data-theme="dark"] {
  --bg: #14120f;
  --surface: #1d1a15;
  --text: #ece7dc;
  --text-muted: #9c9585;
  --border: #322d24;
  --accent: #d9a441;
  --positive: #56b98a;
  --negative: #e07a72;
}
:root[data-theme="light"] {
  --bg: #f5f3ee;
  --surface: #ffffff;
  --text: #1c1a17;
  --text-muted: #6b6558;
  --border: #ddd7c9;
  --accent: #a8681a;
  --positive: #1f7a4d;
  --negative: #b3271e;
}
* { box-sizing: border-box; }
body {
  font-family: ui-sans-serif, system-ui, "Segoe UI", Arial, sans-serif;
  margin: 0;
  padding: 2.5rem clamp(1rem, 4vw, 3rem);
  color: var(--text);
  background: var(--bg);
}
.masthead { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 0.5rem 1.5rem; border-bottom: 1px solid var(--border); padding-bottom: 1rem; margin-bottom: 1.75rem; }
.masthead h1 { font-size: 1.3rem; font-weight: 600; letter-spacing: 0.01em; margin: 0; }
.masthead .eyebrow { text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.7rem; color: var(--text-muted); }
h2 { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.07em; color: var(--text-muted); font-weight: 600; margin: 2.25rem 0 0.9rem; }
.panel { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 1.1rem 1.25rem; }
.card-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 0.9rem; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 0.9rem 1.1rem; display: flex; flex-direction: column; gap: 0.2rem; }
.card-label { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); }
.card-value { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-variant-numeric: tabular-nums; font-size: 1.5rem; font-weight: 600; color: var(--accent); }
.card-sub { font-size: 0.75rem; color: var(--text-muted); }
.chart-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 1rem; margin-top: 1rem; }
.chart-grid .panel img { width: 100%; height: auto; display: block; }
.table-wrap { overflow-x: auto; margin-top: 1rem; border: 1px solid var(--border); border-radius: 10px; }
.metrics-table { border-collapse: collapse; width: 100%; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-variant-numeric: tabular-nums; font-size: 0.78rem; background: var(--surface); }
.metrics-table th { text-align: right; font-family: ui-sans-serif, system-ui, sans-serif; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; font-size: 0.65rem; color: var(--text-muted); padding: 0.6rem 0.75rem; border-bottom: 1px solid var(--border); position: sticky; top: 0; background: var(--surface); }
.metrics-table td { text-align: right; padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--border); white-space: nowrap; }
.metrics-table th:first-child, .metrics-table td:first-child { text-align: left; }
.metrics-table td.name-cell { font-family: ui-sans-serif, system-ui, sans-serif; color: var(--text); }
.metrics-table tr:last-child td { border-bottom: none; }
.metrics-table td.pos { color: var(--positive); }
.metrics-table td.neg { color: var(--negative); }
.placeholder { color: var(--text-muted); font-size: 0.85rem; padding: 1.4rem; background: var(--surface); border: 1px dashed var(--border); border-radius: 10px; margin-top: 1rem; }
"""


def build_strategy_dashboard(latest_dir: Path) -> str:
    latest_dir = Path(latest_dir)
    compact = _load_strategy_compact_report(latest_dir)
    paper_summary = _load_live_paper_summary(latest_dir)
    equity = _load_paper_equity_series(latest_dir)
    accent_light = "#a8681a"

    if compact.empty:
        strategy_section = '<p class="placeholder">No backtest comparison data yet -- run the strategy experiments to populate strategy_compact_report.csv.</p>'
    else:
        labels = compact["Experiment Name"].astype(str).tolist()
        charts = []
        if "Sharpe Ratio" in compact.columns:
            sharpe_png = _bar_chart_base64(labels, compact["Sharpe Ratio"].tolist(), "Sharpe Ratio", "Sharpe Ratio", accent_light)
            charts.append(f'<div class="panel"><img src="data:image/png;base64,{sharpe_png}" alt="Sharpe Ratio by strategy variant"></div>')
        if "Net Getiri (%)" in compact.columns:
            return_png = _bar_chart_base64(labels, compact["Net Getiri (%)"].tolist(), "Net Return (%)", "Net Return (%)", accent_light)
            charts.append(f'<div class="panel"><img src="data:image/png;base64,{return_png}" alt="Net Return by strategy variant"></div>')
        strategy_section = (
            _summary_cards_html(compact)
            + f'<div class="chart-grid">{"".join(charts)}</div>'
            + f'<div class="table-wrap">{_html_table(compact)}</div>'
        )

    total_filled_trades = int(float(paper_summary.get("total_filled_trades", 0) or 0))
    if total_filled_trades <= 0 or equity.empty:
        paper_section = '<p class="placeholder">No paper trades yet -- this section will populate once the nightly chain has filled orders.</p>'
    else:
        equity_png = _line_chart_base64(equity["date"].astype(str).tolist(), equity["paper_capital"].tolist(), "Paper account capital over time", "Paper capital ($)", accent_light)
        paper_section = f'<div class="panel"><img src="data:image/png;base64,{equity_png}" alt="Paper account equity curve"></div>'

    open_positions = paper_summary.get("open_paper_positions", "0")
    generated_at = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")

    return f"""<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Strategy Dashboard</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="masthead">
  <div><span class="eyebrow">Decision support / read-only</span><h1>Strategy Dashboard</h1></div>
  <span class="eyebrow">Generated {generated_at}</span>
</div>
<h2>Backtest comparison &mdash; strategy_compact_report.csv</h2>
{strategy_section}
<h2>Live paper trading &mdash; filled trades: {total_filled_trades}, open positions: {open_positions}</h2>
{paper_section}
</body>
</html>
"""


def write_strategy_dashboard(data_folder: str = "hisseler", report_output_dir: str = "reports") -> dict:
    try:
        from .runner import _report_output_paths
    except ImportError:
        from runner import _report_output_paths

    latest_dir, run_dir = _report_output_paths(report_output_dir, data_folder)
    page_html = build_strategy_dashboard(latest_dir)
    output_path = latest_dir / "strategy_dashboard.html"
    output_path.write_text(page_html, encoding="utf-8")
    (run_dir / "strategy_dashboard.html").write_text(page_html, encoding="utf-8")
    print(f"Strategy dashboard: {output_path}")
    return {"output_path": str(output_path)}
