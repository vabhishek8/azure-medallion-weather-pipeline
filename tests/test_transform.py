import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transform import build_silver, parse_bronze_file, write_silver  # noqa: E402


def _fake_payload(days=3):
    return {
        "daily": {
            "time": [f"2026-01-0{i+1}" for i in range(days)],
            "temperature_2m_max": [25.0 + i for i in range(days)],
            "temperature_2m_min": [15.0 + i for i in range(days)],
            "precipitation_sum": [0.0] * days,
            "windspeed_10m_max": [10.0] * days,
            "relative_humidity_2m_mean": [55.0] * days,
        }
    }


def test_parse_bronze_file(tmp_path):
    p = tmp_path / "sydney_20260101T000000Z.json"
    p.write_text(json.dumps(_fake_payload()))
    df = parse_bronze_file(p)
    assert list(df["city"].unique()) == ["Sydney"]
    assert len(df) == 3
    assert set(df.columns) == {"city", "date", "temp_max_c", "temp_min_c",
                                "precip_mm", "wind_max_kmh", "humidity_pct"}


def test_parse_bronze_file_missing_field_raises(tmp_path):
    payload = _fake_payload()
    del payload["daily"]["windspeed_10m_max"]
    p = tmp_path / "perth_20260101T000000Z.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        parse_bronze_file(p)


def test_build_silver_keeps_latest_snapshot_per_city(tmp_path):
    old = _fake_payload(days=2)
    new = _fake_payload(days=3)
    (tmp_path / "sydney_20260101T000000Z.json").write_text(json.dumps(old))
    (tmp_path / "sydney_20260102T000000Z.json").write_text(json.dumps(new))

    df = build_silver(tmp_path)
    assert len(df) == 3  # newer snapshot wins, not concatenated


def test_build_silver_no_files_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_silver(tmp_path)


def test_write_silver_rejects_bad_data(tmp_path):
    df = pd.DataFrame([{
        "city": "Sydney", "date": "2026-01-01",
        "temp_max_c": 500.0, "temp_min_c": 15.0,
        "precip_mm": 0.0, "wind_max_kmh": 10.0, "humidity_pct": 55.0,
    }])
    with pytest.raises(ValueError):
        write_silver(df, tmp_path)
