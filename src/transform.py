"""
Silver layer: parse bronze JSON into a typed, validated, deduplicated table.

Every write to silver goes through quality_checks.run_quality_checks first.
An errored quality report aborts the write -- silver is a contract the gold
layer and dashboard trust blindly, so nothing enters it unvalidated.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from quality_checks import run_quality_checks

logger = logging.getLogger(__name__)

# Maps Open-Meteo's daily field names to our silver schema. Keeping this
# explicit (rather than a blanket rename) means a field Open-Meteo adds or
# renames upstream fails loudly here instead of silently vanishing.
FIELD_MAP = {
    "temperature_2m_max": "temp_max_c",
    "temperature_2m_min": "temp_min_c",
    "precipitation_sum": "precip_mm",
    "windspeed_10m_max": "wind_max_kmh",
    "relative_humidity_2m_mean": "humidity_pct",
}


def parse_bronze_file(path: Path) -> pd.DataFrame:
    payload = json.loads(path.read_text())
    daily = payload.get("daily")
    if not daily or "time" not in daily:
        raise ValueError(f"{path}: malformed payload, missing 'daily.time'")

    city_name = path.stem.split("_")[0].capitalize()
    df = pd.DataFrame({"date": daily["time"]})
    for src_field, dst_field in FIELD_MAP.items():
        if src_field not in daily:
            raise ValueError(f"{path}: missing expected field '{src_field}'")
        df[dst_field] = daily[src_field]

    df.insert(0, "city", city_name)
    return df


def build_silver(bronze_dir: Path) -> pd.DataFrame:
    """Parse every bronze file, keeping only the most recent snapshot per city
    (bronze accumulates historical snapshots over time; silver reflects
    latest-known-good state per city)."""
    files_by_city: dict[str, Path] = {}
    for path in sorted(bronze_dir.glob("*.json")):
        city = path.stem.split("_")[0]
        # sorted() + dict overwrite => last (most recent timestamp) wins
        files_by_city[city] = path

    if not files_by_city:
        raise FileNotFoundError(f"no bronze files found in {bronze_dir}")

    frames = [parse_bronze_file(p) for p in files_by_city.values()]
    df = pd.concat(frames, ignore_index=True)

    df["date"] = pd.to_datetime(df["date"]).dt.date
    numeric_cols = list(FIELD_MAP.values())
    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")

    df = df.drop_duplicates(subset=["city", "date"], keep="last")
    df = df.sort_values(["city", "date"]).reset_index(drop=True)
    return df


def write_silver(df: pd.DataFrame, silver_dir: Path) -> Path:
    report = run_quality_checks(df)
    logger.info("\n%s", report.summary())
    if not report.passed:
        raise ValueError(
            f"silver write aborted -- {len(report.errors)} quality error(s):\n{report.summary()}"
        )

    silver_dir.mkdir(parents=True, exist_ok=True)
    out_path = silver_dir / "weather_daily.parquet"
    df.to_parquet(out_path, index=False)
    logger.info("wrote silver table: %s (%d rows)", out_path, len(df))
    return out_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    root = Path(__file__).resolve().parents[1]
    silver_df = build_silver(root / "data" / "bronze")
    write_silver(silver_df, root / "data" / "silver")
