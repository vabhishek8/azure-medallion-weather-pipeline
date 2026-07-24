"""
Gold layer: analytics-ready aggregates, built with DuckDB SQL rather than
pandas. This is a deliberate stack choice, not decoration: DuckDB gives a
real, EXPLAIN-able SQL engine over the silver Parquet file, which is the
same mental model as querying Synapse Serverless SQL over ADLS Gen2 -- the
production target this pipeline is designed to map onto (see infra/main.bicep).

Gold answers three analytical questions per city/day:
  1. temp_anomaly_c   -- deviation of max temp from a 30-day trailing mean
  2. is_extreme_heat   -- max temp > 95th percentile of trailing 90-day window
  3. rain_flag         -- binary wet/dry day
"""
from __future__ import annotations

import logging
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

GOLD_SQL = """
with base as (
    select * from silver
),
windowed as (
    select
        city,
        date,
        temp_max_c,
        temp_min_c,
        precip_mm,
        wind_max_kmh,
        humidity_pct,
        avg(temp_max_c) over (
            partition by city order by date
            rows between 29 preceding and current row
        ) as temp_max_30d_avg,
        stddev_samp(temp_max_c) over (
            partition by city order by date
            rows between 29 preceding and current row
        ) as temp_max_30d_stddev,
        quantile_cont(temp_max_c, 0.95) over (
            partition by city order by date
            rows between 89 preceding and current row
        ) as temp_max_90d_p95
    from base
)
select
    city,
    date,
    temp_max_c,
    temp_min_c,
    precip_mm,
    wind_max_kmh,
    humidity_pct,
    round(temp_max_30d_avg, 2) as temp_max_30d_avg,
    round(temp_max_c - temp_max_30d_avg, 2) as temp_anomaly_c,
    case
        when temp_max_30d_stddev is null or temp_max_30d_stddev = 0 then null
        else round((temp_max_c - temp_max_30d_avg) / temp_max_30d_stddev, 2)
    end as temp_anomaly_zscore,
    (temp_max_c >= temp_max_90d_p95) as is_extreme_heat,
    (precip_mm >= 1.0) as rain_flag
from windowed
order by city, date
"""

CITY_SUMMARY_SQL = """
select
    city,
    max(date) as latest_date,
    round(avg(temp_max_c), 1) as avg_temp_max_c,
    round(max(temp_max_c), 1) as max_temp_max_c,
    sum(case when is_extreme_heat then 1 else 0 end) as extreme_heat_days,
    sum(case when rain_flag then 1 else 0 end) as rain_days,
    count(*) as total_days
from gold
group by city
order by city
"""


def build_gold(silver_path: Path, gold_dir: Path) -> tuple[Path, Path]:
    gold_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"create view silver as select * from read_parquet('{silver_path.as_posix()}')")

    gold_df = con.execute(GOLD_SQL).df()
    con.register("gold", gold_df)
    summary_df = con.execute(CITY_SUMMARY_SQL).df()

    gold_path = gold_dir / "weather_gold.parquet"
    summary_path = gold_dir / "city_summary.parquet"
    gold_df.to_parquet(gold_path, index=False)
    summary_df.to_parquet(summary_path, index=False)

    logger.info("wrote gold table: %s (%d rows)", gold_path, len(gold_df))
    logger.info("wrote summary table: %s (%d rows)", summary_path, len(summary_df))
    con.close()
    return gold_path, summary_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    root = Path(__file__).resolve().parents[1]
    build_gold(root / "data" / "silver" / "weather_daily.parquet", root / "data" / "gold")
