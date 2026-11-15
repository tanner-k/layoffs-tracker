"""Build `data/latest.json`: the numbers the portfolio page shows."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl

from layoffs_tracker.months import METHOD_SWITCH, add_months, month_end
from layoffs_tracker.schema import JEV_METHOD, PAPER_METHOD
from layoffs_tracker.sec import PHRASES

SCHEMA_VERSION = 1
FIRST_MONTH = date(2019, 1, 1)
SHARE_DIGITS = 4


def method_for(d: date) -> str:
    """The label method of filings dated `d`."""
    return JEV_METHOD if d >= METHOD_SWITCH else PAPER_METHOD


def period_stats(orig: pl.DataFrame, start: date, end: date) -> dict[str, Any]:
    """AI-cited counts and the low/high share for original filings dated in [start, end]."""
    part = orig.filter(pl.col("file_date").is_between(start, end))["ai_reason"]
    total, ai_true, unresolved = part.len(), int(part.sum()), part.null_count()

    def share(n: int) -> float | None:
        return round(n / total, SHARE_DIGITS) if total else None

    return {
        "total": total,
        "ai_true": ai_true,
        "unresolved": unresolved,
        "share_low": share(ai_true),
        "share_high": share(ai_true + unresolved),
    }


def ai_quarterly(orig: pl.DataFrame, last_day: date) -> list[dict[str, Any]]:
    """One row per quarter from 2019Q1 to the quarter holding `last_day`. The method switch is
    on a quarter boundary, so a quarter never mixes methods."""
    rows = []
    start = FIRST_MONTH
    while start <= last_day:
        end = min(month_end(add_months(start, 2)), last_day)
        label = f"{start.year}Q{(start.month - 1) // 3 + 1}"
        rows.append(
            {"quarter": label, "method": method_for(start), **period_stats(orig, start, end)}
        )
        start = add_months(start, 3)
    return rows


def ai_yearly(orig: pl.DataFrame, last_day: date) -> list[dict[str, Any]]:
    """One row per year and method: the switch year has a paper row and a Jev row."""
    rows = []
    for year in range(FIRST_MONTH.year, last_day.year + 1):
        jan1, dec31 = date(year, 1, 1), min(date(year, 12, 31), last_day)
        cuts = [(jan1, dec31)]
        if jan1 < METHOD_SWITCH <= dec31:
            cuts = [(jan1, METHOD_SWITCH - timedelta(days=1)), (METHOD_SWITCH, dec31)]
        for start, end in cuts:
            rows.append(
                {"year": year, "method": method_for(start), **period_stats(orig, start, end)}
            )
    return rows


def sec_monthly(orig: pl.DataFrame, data_through: date) -> list[dict[str, Any]]:
    counts = orig.group_by(month=pl.col("file_date").dt.month_start()).agg(filings=pl.len())
    months = pl.date_range(FIRST_MONTH, data_through, "1mo", eager=True)
    table = (
        pl.DataFrame({"month": months})
        .join(counts, on="month", how="left")
        .with_columns(pl.col("filings").fill_null(0).cast(pl.Int64))
        .sort("month")
    )
    return [{"month": f"{m:%Y-%m}", "filings": n} for m, n in table.iter_rows()]


def warn_monthly(warn: pl.DataFrame, data_through: date) -> list[dict[str, Any]]:
    rows = warn.filter(pl.col("month") <= data_through).sort("month").to_dicts()
    return [{**r, "month": f"{r['month']:%Y-%m}"} for r in rows]


def build_latest(
    filings: pl.DataFrame,
    warn: pl.DataFrame,
    sources: Mapping[str, object],
    data_through: date,
    generated_at: datetime,
) -> dict[str, Any]:
    """The `latest.json` payload: original filings only, every series ending at `data_through`.

    `warn` is `warn.national_month`'s frame; `data_through` is the first day of the last
    complete month.
    """
    last_day = month_end(data_through)
    orig = filings.filter(
        ~pl.col("is_amendment") & pl.col("file_date").is_between(FIRST_MONTH, last_day)
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_through": f"{data_through:%Y-%m}",
        "method_switch": f"{METHOD_SWITCH:%Y-%m}",
        "sources": {"sec_phrases": list(PHRASES.values()), **sources},
        "sec_monthly": sec_monthly(orig, data_through),
        "ai_quarterly": ai_quarterly(orig, last_day),
        "ai_yearly": ai_yearly(orig, last_day),
        "warn_monthly": warn_monthly(warn, data_through),
    }
