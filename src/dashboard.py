"""
Renders the gold layer to a single static HTML file (Plotly, no server
required) so the pipeline's output is viewable via GitHub Pages without
standing up any hosting infrastructure.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

logger = logging.getLogger(__name__)

CITY_COLORS = {
    "Sydney": "#f2a541",
    "Melbourne": "#4fd8c4",
    "Brisbane": "#e8556f",
    "Perth": "#7c9cff",
    "Adelaide": "#b98ce8",
}


def render_dashboard(gold_path: Path, summary_path: Path, out_path: Path) -> Path:
    gold = pd.read_parquet(gold_path)
    summary = pd.read_parquet(summary_path)
    gold["date"] = pd.to_datetime(gold["date"])

    fig = make_subplots(
        rows=2, cols=1,
        row_heights=[0.65, 0.35],
        subplot_titles=(
            "Max temperature vs 30-day trailing average",
            "Temperature anomaly (z-score vs 30-day trailing mean/stddev)",
        ),
        vertical_spacing=0.12,
    )

    for city, colour in CITY_COLORS.items():
        city_df = gold[gold["city"] == city].sort_values("date")
        if city_df.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=city_df["date"], y=city_df["temp_max_c"], name=f"{city} max temp",
                line=dict(color=colour, width=1.6), legendgroup=city,
            ),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=city_df["date"], y=city_df["temp_max_30d_avg"], name=f"{city} 30d avg",
                line=dict(color=colour, width=1, dash="dot"), legendgroup=city, showlegend=False,
                opacity=0.55,
            ),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=city_df["date"], y=city_df["temp_anomaly_zscore"], name=f"{city} anomaly",
                line=dict(color=colour, width=1.4), legendgroup=city, showlegend=False,
            ),
            row=2, col=1,
        )

    fig.add_hline(y=0, line_width=1, line_dash="dot", line_color="rgba(255,255,255,0.25)", row=2, col=1)

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0a0e14",
        plot_bgcolor="#0a0e14",
        font=dict(family="Inter, system-ui, sans-serif", color="#c9d1d9", size=12),
        height=820,
        margin=dict(l=50, r=30, t=70, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.06, x=0),
        title=dict(
            text="Australian Capitals -- Weather Intelligence Pipeline (Gold Layer)",
            x=0.01, font=dict(size=20),
        ),
    )
    fig.update_xaxes(gridcolor="rgba(255,255,255,0.06)")
    fig.update_yaxes(gridcolor="rgba(255,255,255,0.06)", title_text="°C", row=1, col=1)
    fig.update_yaxes(gridcolor="rgba(255,255,255,0.06)", title_text="z-score", row=2, col=1)

    summary_rows = "".join(
        f"<tr><td>{r.city}</td><td>{r.latest_date}</td><td>{r.avg_temp_max_c}</td>"
        f"<td>{r.max_temp_max_c}</td><td>{r.extreme_heat_days}</td>"
        f"<td>{r.rain_days}</td><td>{r.total_days}</td></tr>"
        for r in summary.itertuples()
    )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    chart_html = fig.to_html(full_html=False, include_plotlyjs="cdn", config={"displaylogo": False})

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AU Weather Medallion Pipeline -- Gold Layer Dashboard</title>
<style>
  body {{ background:#0a0e14; color:#c9d1d9; font-family: Inter, system-ui, sans-serif; margin:0; padding:32px 24px 60px; }}
  h1 {{ font-size: 1.1rem; font-weight:600; color:#e6edf3; letter-spacing:.01em; margin:0 0 4px; }}
  p.meta {{ color:#7d8590; font-size:.85rem; margin:0 0 28px; }}
  table {{ border-collapse: collapse; width:100%; max-width:900px; margin-top:28px; font-size:.85rem; }}
  th, td {{ text-align:left; padding:8px 14px; border-bottom:1px solid rgba(255,255,255,0.08); }}
  th {{ color:#7d8590; font-weight:500; text-transform:uppercase; font-size:.72rem; letter-spacing:.04em; }}
  a {{ color:#4fd8c4; }}
</style>
</head>
<body>
  <h1>Bronze &rarr; Silver &rarr; Gold weather intelligence pipeline</h1>
  <p class="meta">Data: Open-Meteo API &middot; Generated {generated_at} by scheduled GitHub Actions run &middot;
     <a href="https://github.com/">source</a></p>
  {chart_html}
  <table>
    <thead><tr><th>City</th><th>Latest</th><th>Avg max &deg;C</th><th>Peak max &deg;C</th>
    <th>Extreme heat days</th><th>Rain days</th><th>Days tracked</th></tr></thead>
    <tbody>{summary_rows}</tbody>
  </table>
</body>
</html>"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html)
    logger.info("wrote dashboard: %s", out_path)
    return out_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    root = Path(__file__).resolve().parents[1]
    render_dashboard(
        root / "data" / "gold" / "weather_gold.parquet",
        root / "data" / "gold" / "city_summary.parquet",
        root / "docs" / "index.html",
    )
