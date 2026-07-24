"""
Orchestrator: bronze -> silver -> gold -> dashboard.

Run directly (`python src/pipeline.py`) or via the scheduled GitHub Actions
workflow (.github/workflows/pipeline.yml). Each stage is independently
importable/testable; this module just sequences them and turns any stage
failure into a non-zero exit code so CI reflects real pipeline health.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest import ingest_all          # noqa: E402
from transform import build_silver, write_silver   # noqa: E402
from gold import build_gold            # noqa: E402
from dashboard import render_dashboard  # noqa: E402

logger = logging.getLogger("pipeline")


def run() -> None:
    root = Path(__file__).resolve().parents[1]
    bronze_dir = root / "data" / "bronze"
    silver_dir = root / "data" / "silver"
    gold_dir = root / "data" / "gold"
    dashboard_path = root / "docs" / "index.html"

    logger.info("stage 1/4: ingest -> bronze")
    ingest_all(bronze_dir)

    logger.info("stage 2/4: transform -> silver")
    silver_df = build_silver(bronze_dir)
    silver_path = write_silver(silver_df, silver_dir)

    logger.info("stage 3/4: aggregate -> gold")
    gold_path, summary_path = build_gold(silver_path, gold_dir)

    logger.info("stage 4/4: render -> dashboard")
    render_dashboard(gold_path, summary_path, dashboard_path)

    logger.info("pipeline complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        run()
    except Exception:
        logger.exception("pipeline failed")
        sys.exit(1)
