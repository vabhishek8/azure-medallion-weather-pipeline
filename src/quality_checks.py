"""
Data quality gate for the silver layer.

Design intent: fail loudly and specifically. A pipeline that silently drops
bad rows or passes NaNs downstream is worse than one that stops. Every check
returns a structured QualityIssue instead of a bare bool so failures are
diagnosable from CI logs alone, without re-running locally.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

# Physically plausible bounds for Australian capital cities. Wide enough to
# never false-positive on real weather, tight enough to catch unit errors
# (e.g. a Fahrenheit value slipping through, or a percentage > 100).
BOUNDS = {
    "temp_max_c": (-10.0, 55.0),
    "temp_min_c": (-15.0, 45.0),
    "precip_mm": (0.0, 600.0),
    "wind_max_kmh": (0.0, 250.0),
    "humidity_pct": (0.0, 100.0),
}

REQUIRED_COLUMNS = {
    "city",
    "date",
    "temp_max_c",
    "temp_min_c",
    "precip_mm",
    "wind_max_kmh",
    "humidity_pct",
}


@dataclass
class QualityIssue:
    check: str
    severity: str  # "error" | "warning"
    message: str


@dataclass
class QualityReport:
    issues: list[QualityIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def add(self, check: str, severity: str, message: str) -> None:
        self.issues.append(QualityIssue(check, severity, message))

    def summary(self) -> str:
        lines = [f"Quality report: {len(self.errors)} error(s), {len(self.warnings)} warning(s)"]
        for i in self.issues:
            lines.append(f"  [{i.severity.upper()}] {i.check}: {i.message}")
        return "\n".join(lines)


def check_schema(df: pd.DataFrame, report: QualityReport) -> None:
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        report.add("schema", "error", f"missing required columns: {sorted(missing)}")


def check_not_empty(df: pd.DataFrame, report: QualityReport) -> None:
    if df.empty:
        report.add("row_count", "error", "dataframe has zero rows")


def check_nulls(df: pd.DataFrame, report: QualityReport) -> None:
    if df.empty:
        return
    for col in REQUIRED_COLUMNS & set(df.columns):
        n_null = int(df[col].isna().sum())
        if n_null:
            severity = "error" if col in ("city", "date") else "warning"
            report.add("nulls", severity, f"{col} has {n_null} null value(s)")


def check_ranges(df: pd.DataFrame, report: QualityReport) -> None:
    if df.empty:
        return
    for col, (lo, hi) in BOUNDS.items():
        if col not in df.columns:
            continue
        series = df[col].dropna()
        out_of_range = series[(series < lo) | (series > hi)]
        if not out_of_range.empty:
            report.add(
                "range",
                "error",
                f"{col} has {len(out_of_range)} value(s) outside [{lo}, {hi}]: "
                f"e.g. {out_of_range.iloc[0]}",
            )


def check_duplicates(df: pd.DataFrame, report: QualityReport) -> None:
    if df.empty or not {"city", "date"}.issubset(df.columns):
        return
    dupes = df.duplicated(subset=["city", "date"]).sum()
    if dupes:
        report.add("duplicates", "error", f"{dupes} duplicate (city, date) row(s)")


def check_temp_consistency(df: pd.DataFrame, report: QualityReport) -> None:
    if df.empty or not {"temp_max_c", "temp_min_c"}.issubset(df.columns):
        return
    inverted = df[df["temp_max_c"] < df["temp_min_c"]]
    if not inverted.empty:
        report.add(
            "temp_consistency",
            "error",
            f"{len(inverted)} row(s) where temp_max_c < temp_min_c",
        )


CHECKS: list[Callable[[pd.DataFrame, QualityReport], None]] = [
    check_schema,
    check_not_empty,
    check_nulls,
    check_ranges,
    check_duplicates,
    check_temp_consistency,
]


def run_quality_checks(df: pd.DataFrame) -> QualityReport:
    report = QualityReport()
    for check in CHECKS:
        check(df, report)
    return report
