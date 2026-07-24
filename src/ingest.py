"""
Bronze layer: raw ingestion from Open-Meteo's historical archive API
(ERA5/ERA5-Land reanalysis), not the forecast endpoint.

This was a deliberate correction after testing: the forecast endpoint's
`past_days` parameter returns sparse/null daily aggregates for humidity and
other fields beyond a short lookback window -- a real data-quality footgun
that the quality-check layer (src/quality_checks.py) is designed to catch,
not paper over. The archive endpoint returns complete, reanalysis-grade
daily aggregates with roughly a 1-day publication lag, so bronze always
requests up to `yesterday`, not `today`.

Raw JSON responses are persisted verbatim (one file per city per run) before
any parsing happens -- the medallion-architecture bronze principle: never
let a transform bug destroy your ability to replay from source.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

DAILY_FIELDS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "windspeed_10m_max",
    "relative_humidity_2m_mean",
]

# Archive API publishes with ~1 day lag; requesting up to "today" reliably
# returns trailing nulls, which is exactly the kind of silent data-quality
# hole this pipeline is built to catch instead of ignore.
PUBLICATION_LAG_DAYS = 1


@dataclass(frozen=True)
class City:
    name: str
    latitude: float
    longitude: float
    timezone: str


# Five most populous Australian capitals. Geographically spread (tropical to
# temperate to Mediterranean climate zones) so the gold-layer anomaly
# detection has genuinely different baselines to work against, rather than
# five near-identical time series.
CITIES: list[City] = [
    City("Sydney", -33.8688, 151.2093, "Australia/Sydney"),
    City("Melbourne", -37.8136, 144.9631, "Australia/Melbourne"),
    City("Brisbane", -27.4698, 153.0251, "Australia/Brisbane"),
    City("Perth", -31.9523, 115.8613, "Australia/Perth"),
    City("Adelaide", -34.9285, 138.6007, "Australia/Adelaide"),
]

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2


def fetch_city(city: City, start_date: date, end_date: date) -> dict:
    """Fetch raw daily weather JSON for one city, with retry on transient failure.

    Raises requests.HTTPError / requests.ConnectionError after exhausting
    retries -- callers must not swallow this. A missing city in bronze must
    be visible, not silently interpolated later.
    """
    params = {
        "latitude": city.latitude,
        "longitude": city.longitude,
        "daily": ",".join(DAILY_FIELDS),
        "timezone": city.timezone,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }

    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(ARCHIVE_URL, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except (requests.HTTPError, requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            logger.warning(
                "fetch_city(%s) attempt %d/%d failed: %s",
                city.name, attempt, MAX_RETRIES, exc,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    assert last_exc is not None
    raise last_exc


def ingest_all(
    bronze_dir: Path,
    run_ts: datetime | None = None,
    historical_days: int = 120,
) -> list[Path]:
    """Fetch all cities and write one raw JSON snapshot per city.

    Returns the list of file paths written. Partial failure (one city down)
    does not abort the others -- it's collected and raised at the end so a
    single upstream API hiccup doesn't blank out four healthy cities' worth
    of data, but the run still fails loudly overall.
    """
    run_ts = run_ts or datetime.now(timezone.utc)
    end_date = run_ts.date() - timedelta(days=PUBLICATION_LAG_DAYS)
    start_date = end_date - timedelta(days=historical_days)

    bronze_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    failures: list[tuple[str, Exception]] = []

    for city in CITIES:
        try:
            payload = fetch_city(city, start_date, end_date)
        except Exception as exc:  # noqa: BLE001 -- collected, not swallowed
            failures.append((city.name, exc))
            continue

        out_path = bronze_dir / f"{city.name.lower()}_{run_ts.strftime('%Y%m%dT%H%M%SZ')}.json"
        out_path.write_text(json.dumps(payload, indent=2))
        written.append(out_path)
        logger.info("wrote bronze snapshot: %s", out_path)

    if failures:
        detail = "; ".join(f"{name}: {exc}" for name, exc in failures)
        raise RuntimeError(f"ingest_all: {len(failures)}/{len(CITIES)} cities failed -- {detail}")

    return written


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    default_dir = Path(__file__).resolve().parents[1] / "data" / "bronze"
    paths = ingest_all(default_dir)
    print(f"wrote {len(paths)} bronze files to {default_dir}")
