import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quality_checks import run_quality_checks  # noqa: E402


def _valid_row(**overrides):
    row = {
        "city": "Sydney",
        "date": "2026-01-01",
        "temp_max_c": 25.0,
        "temp_min_c": 18.0,
        "precip_mm": 0.0,
        "wind_max_kmh": 15.0,
        "humidity_pct": 60.0,
    }
    row.update(overrides)
    return row


def test_valid_dataframe_passes():
    df = pd.DataFrame([_valid_row(), _valid_row(date="2026-01-02")])
    report = run_quality_checks(df)
    assert report.passed
    assert report.errors == []


def test_missing_column_fails():
    df = pd.DataFrame([_valid_row()]).drop(columns=["humidity_pct"])
    report = run_quality_checks(df)
    assert not report.passed
    assert any(i.check == "schema" for i in report.errors)


def test_empty_dataframe_fails():
    df = pd.DataFrame(columns=["city", "date", "temp_max_c", "temp_min_c",
                                "precip_mm", "wind_max_kmh", "humidity_pct"])
    report = run_quality_checks(df)
    assert not report.passed
    assert any(i.check == "row_count" for i in report.errors)


def test_out_of_range_temperature_fails():
    df = pd.DataFrame([_valid_row(temp_max_c=88.0)])
    report = run_quality_checks(df)
    assert not report.passed
    assert any(i.check == "range" for i in report.errors)


def test_negative_precipitation_fails():
    df = pd.DataFrame([_valid_row(precip_mm=-5.0)])
    report = run_quality_checks(df)
    assert not report.passed
    assert any(i.check == "range" for i in report.errors)


def test_duplicate_city_date_fails():
    df = pd.DataFrame([_valid_row(), _valid_row()])
    report = run_quality_checks(df)
    assert not report.passed
    assert any(i.check == "duplicates" for i in report.errors)


def test_inverted_temps_fails():
    df = pd.DataFrame([_valid_row(temp_max_c=10.0, temp_min_c=20.0)])
    report = run_quality_checks(df)
    assert not report.passed
    assert any(i.check == "temp_consistency" for i in report.errors)


def test_non_required_null_is_warning_not_error():
    df = pd.DataFrame([_valid_row(precip_mm=None)])
    report = run_quality_checks(df)
    assert report.passed
    assert any(i.check == "nulls" and i.severity == "warning" for i in report.warnings)


def test_null_city_is_error():
    df = pd.DataFrame([_valid_row(city=None)])
    report = run_quality_checks(df)
    assert not report.passed
    assert any(i.check == "nulls" and i.severity == "error" for i in report.errors)
