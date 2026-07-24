import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gold import build_gold  # noqa: E402


def test_build_gold_produces_anomaly_and_summary(tmp_path):
    dates = pd.date_range("2026-01-01", periods=40, freq="D")
    rows = []
    for city in ["Sydney", "Melbourne"]:
        base = 20 if city == "Sydney" else 15
        for i, d in enumerate(dates):
            spike = 15 if i == 35 else 0  # inject one clear heat spike
            rows.append({
                "city": city, "date": d.date(),
                "temp_max_c": base + (i % 5) + spike,
                "temp_min_c": base - 5,
                "precip_mm": 1.0 if i % 7 == 0 else 0.0,
                "wind_max_kmh": 12.0,
                "humidity_pct": 55.0,
            })
    silver_df = pd.DataFrame(rows)
    silver_path = tmp_path / "silver.parquet"
    silver_df.to_parquet(silver_path, index=False)

    gold_path, summary_path = build_gold(silver_path, tmp_path)
    gold_df = pd.read_parquet(gold_path)
    summary_df = pd.read_parquet(summary_path)

    assert len(gold_df) == len(silver_df)
    assert "temp_anomaly_c" in gold_df.columns
    assert "is_extreme_heat" in gold_df.columns
    # the injected spike day should be flagged for at least one city
    assert gold_df["is_extreme_heat"].any()
    assert set(summary_df["city"]) == {"Sydney", "Melbourne"}
    assert (summary_df["total_days"] == 40).all()
