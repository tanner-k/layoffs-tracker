from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

import polars as pl
import pytest

from layoffs_tracker.metrics import build_latest
from layoffs_tracker.schema import FILINGS_SCHEMA
from layoffs_tracker.seed import AI_REASON_SEED, LAYOFFS_SEED, build_seed

GENERATED = datetime(2026, 11, 3, 16, 5, tzinfo=UTC)
SOURCES = {"jev_model": "jev-1.13.0", "kadoa_sha": "abc", "warn_states": ["AZ"]}
_n = iter(range(10_000))


def row(day: str, ai: bool | None, method: str, amendment: bool = False) -> dict[str, Any]:
    return {
        "adsh": f"x-{next(_n)}",
        "cik": "1",
        "company": "Co",
        "file_date": date.fromisoformat(day),
        "is_amendment": amendment,
        "headcount": None,
        "ai_reason": ai,
        "ai_role": None,
        "label_method": method,
        "jev_model": None,
        "labeled_at": date(2026, 11, 3),
    }


def filings() -> pl.DataFrame:
    paper = [row("2026-07-15", True, "paper_audited"), row("2026-08-01", False, "paper_audited")]
    jev = [
        row("2026-10-02", True, "jev"),
        *(row("2026-10-03", None, "jev") for _ in range(2)),
        *(row("2026-10-04", False, "jev") for _ in range(7)),
        row("2026-10-05", True, "jev", amendment=True),  # amendments never count
        row("2026-11-02", True, "jev"),  # after data_through
    ]
    return pl.DataFrame([*paper, *jev], schema=FILINGS_SCHEMA)


def warn() -> pl.DataFrame:
    months = pl.date_range(date(2019, 1, 1), date(2026, 11, 1), "1mo", eager=True)
    n = len(months)
    return pl.DataFrame(
        {
            "month": months,
            "notices": [2] * n,
            "workers": [100] * n,
            "notices_missing_workers": [1] * n,
        }
    )


@pytest.fixture
def out() -> dict[str, Any]:
    return build_latest(filings(), warn(), SOURCES, date(2026, 10, 1), GENERATED)


def quarter(out: dict[str, Any], label: str) -> dict[str, Any]:
    (q,) = [q for q in out["ai_quarterly"] if q["quarter"] == label]
    return q


def test_quarter_shares(out: dict[str, Any]) -> None:
    q = quarter(out, "2026Q4")
    assert (q["total"], q["ai_true"], q["unresolved"]) == (10, 1, 2)
    assert (q["share_low"], q["share_high"]) == (0.1, 0.3) and q["method"] == "jev"


def test_paper_quarters_have_no_band(out: dict[str, Any]) -> None:
    paper = [q for q in out["ai_quarterly"] if q["method"] == "paper_audited"]
    assert paper and all(q["unresolved"] == 0 for q in paper)
    q3 = quarter(out, "2026Q3")
    assert (q3["total"], q3["share_low"], q3["share_high"]) == (2, 0.5, 0.5)


def test_2026_yearly_splits_by_method(out: dict[str, Any]) -> None:
    rows = {y["method"]: y for y in out["ai_yearly"] if y["year"] == 2026}
    assert set(rows) == {"paper_audited", "jev"}
    assert (rows["paper_audited"]["total"], rows["jev"]["total"]) == (2, 10)


def test_amendments_excluded_and_empty_quarter_has_null_share(out: dict[str, Any]) -> None:
    assert quarter(out, "2026Q4")["ai_true"] == 1
    q2 = quarter(out, "2026Q2")
    assert q2["total"] == 0 and q2["share_low"] is None and q2["share_high"] is None


def test_series_end_at_data_through(out: dict[str, Any]) -> None:
    assert out["sec_monthly"][-1] == {"month": "2026-10", "filings": 10}
    assert out["sec_monthly"][0]["month"] == "2019-01" and len(out["sec_monthly"]) == 94
    assert out["warn_monthly"][-1]["month"] == "2026-10" and len(out["warn_monthly"]) == 94
    assert out["ai_quarterly"][-1]["quarter"] == "2026Q4" and out["ai_yearly"][-1]["year"] == 2026


def test_header_and_json(out: dict[str, Any]) -> None:
    assert out["schema_version"] == 1 and out["generated_at"] == "2026-11-03T16:05:00Z"
    assert (out["data_through"], out["method_switch"]) == ("2026-10", "2026-10")
    assert out["sources"]["kadoa_sha"] == "abc" and out["sources"]["sec_phrases"]
    assert out["warn_monthly"][0] == {
        "month": "2019-01",
        "notices": 2,
        "workers": 100,
        "notices_missing_workers": 1,
    }
    json.dumps(out)


def test_paper_history_reads_as_in_the_paper() -> None:
    seed = build_seed(AI_REASON_SEED, LAYOFFS_SEED)
    out = build_latest(seed, warn(), SOURCES, date(2026, 9, 1), GENERATED)
    (y2026,) = [y for y in out["ai_yearly"] if y["year"] == 2026]
    assert (y2026["method"], y2026["total"], y2026["ai_true"]) == ("paper_audited", 153, 26)
    assert y2026["share_low"] == y2026["share_high"] == 0.1699
    assert [y["ai_true"] for y in out["ai_yearly"]] == [1, 0, 1, 2, 3, 7, 10, 26]
