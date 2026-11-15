"""National WARN Act layoff notices per month, from Kadoa's open layoffs tracker.

Ported from the paper's `research/build_warn.py`. The paper pins one Kadoa commit; the tracker
follows Kadoa's main and records the SHA it used. Only states covered every year since 2019
(and mostly dated) are counted, so a state joining Kadoa's data does not fake a trend.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import urllib.request
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import IO

import polars as pl

from layoffs_tracker.months import month_end

LOG = logging.getLogger(__name__)

REPO = "kadoa-org/layoffs-tracker"
RAW_URL = "https://raw.githubusercontent.com/" + REPO + "/{sha}/public/data/notices.json"
LATEST_URL = "https://api.github.com/repos/" + REPO + "/commits/main"
USER_AGENT = "layoffs-tracker/1.0"
TIMEOUT_S = 120
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

STATES = tuple(
    [
        "AK",
        "AL",
        "AR",
        "AZ",
        "CA",
        "CO",
        "CT",
        "DC",
        "DE",
        "FL",
        "GA",
        "HI",
        "IA",
        "ID",
        "IL",
        "IN",
        "KS",
        "KY",
        "LA",
        "MA",
        "MD",
        "ME",
        "MI",
        "MN",
        "MO",
        "MS",
        "MT",
        "NC",
        "ND",
        "NE",
        "NH",
        "NJ",
        "NM",
        "NV",
        "NY",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VA",
        "VT",
        "WA",
        "WI",
        "WV",
        "WY",
    ]
)
FIRST_YEAR = 2019  # the series and the balance rule both start here (the paper's balanced_2019)
IMPUTE_DAYS = 60  # WARN notices are usually filed 60 days before the effective date
MAX_UNDATED_SHARE = 0.10  # a state counts only if <= this share of its notices are undated
DEDUPE_IGNORE = ("id", "pdf_url")  # notices equal on every other column are one notice
REQUIRED = (
    "id",
    "state",
    "company",
    "city",
    "county",
    "pdf_url",
    "num_affected",
    "received_date",
    "effective_date",
    "event_type",
)
NOTICE_COLS = [
    *("id", "state", "company", "city", "county", "received_date", "effective_date"),
    *("notice_date", "notice_date_imputed", "num_affected", "is_amendment", "pdf_url"),
]


def _open(url: str) -> IO[bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    resp: IO[bytes] = urllib.request.urlopen(req, timeout=TIMEOUT_S)
    return resp


def latest_sha() -> str:
    """The current SHA of Kadoa's main branch, from the GitHub API."""
    with _open(LATEST_URL) as resp:
        sha = json.load(resp).get("sha", "")
    if not isinstance(sha, str) or not SHA_RE.match(sha):
        raise ValueError(f"GitHub returned no commit SHA for {REPO} main")
    return sha


def download(sha: str, dest: Path) -> Path:
    """Download `notices.json` at `sha` into `dest/<sha7>/` (kept if already there)."""
    path = dest / sha[:7] / "notices.json"
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    with _open(RAW_URL.format(sha=sha)) as resp, part.open("wb") as f:
        shutil.copyfileobj(resp, f)
    if part.read_bytes()[:1].lstrip() not in (b"[", b"{"):
        part.unlink()
        raise ValueError(f"notices.json @ {sha[:7]} is not JSON")
    part.replace(path)
    return path


def load_notices(path: Path) -> pl.DataFrame:
    """One row per notice in the 50 states + DC, identical notices collapsed.

    `notice_date` is `received_date`, else `effective_date` minus 60 days. Notices with
    neither keep a null `notice_date`. Raises ValueError naming any missing column.
    """
    raw = pl.read_json(path, infer_schema_length=None)
    if missing := sorted(set(REQUIRED) - set(raw.columns)):
        raise ValueError(f"notices.json lacks columns: {missing}")
    if not raw["id"].is_unique().all():
        raise ValueError("notices.json has duplicate notice ids")
    notices = (
        raw.filter(pl.col("state").is_in(STATES))
        .with_columns(
            received_date=pl.col("received_date").cast(pl.Utf8).str.to_date(),
            effective_date=pl.col("effective_date").cast(pl.Utf8).str.to_date(),
            num_affected=pl.col("num_affected").cast(pl.Int64),
        )
        .with_columns(
            notice_date=pl.coalesce(
                "received_date", pl.col("effective_date") - pl.duration(days=IMPUTE_DAYS)
            ),
            notice_date_imputed=pl.col("received_date").is_null()
            & pl.col("effective_date").is_not_null(),
            is_amendment=pl.col("event_type").eq_missing("amendment"),
        )
        .select(NOTICE_COLS)
        .sort("state", "notice_date", "id")
    )
    keys = [c for c in notices.columns if c not in DEDUPE_IGNORE]
    return notices.unique(subset=keys, keep="first", maintain_order=True)


def _in_window(notices: pl.DataFrame, data_through: date) -> pl.DataFrame:
    end = month_end(data_through)
    return notices.filter(pl.col("notice_date").is_between(date(FIRST_YEAR, 1, 1), end))


def last_complete_year(data_through: date) -> int:
    return data_through.year if data_through.month == 12 else data_through.year - 1


def balanced_states(notices: pl.DataFrame, first_year: int, data_through: date) -> list[str]:
    """States with a notice (any type) in every year from `first_year` through the last
    complete year, and at most MAX_UNDATED_SHARE of notices undated.

    The last complete year, not the current one: in January no state has a notice yet.
    """
    last = last_complete_year(data_through)
    covered = (
        _in_window(notices, data_through)
        .select("state", year=pl.col("notice_date").dt.year())
        .filter(pl.col("year").is_between(first_year, last))
        .group_by("state")
        .agg(n=pl.col("year").n_unique())
        .filter(pl.col("n") == last - first_year + 1)
    )
    dated = notices.group_by("state").agg(undated=pl.col("notice_date").is_null().mean())
    eligible = set(dated.filter(pl.col("undated") <= MAX_UNDATED_SHARE)["state"])
    return sorted(set(covered["state"]) & eligible)


def national_month(
    notices: pl.DataFrame, states: Sequence[str], data_through: date
) -> pl.DataFrame:
    """Notices, workers affected (known counts) and notices missing a count, per month, for
    `states`, Jan 2019 through `data_through`. Amendments are not counted; empty months are 0."""
    counts = (
        _in_window(notices, data_through)
        .filter(~pl.col("is_amendment") & pl.col("state").is_in(list(states)))
        .group_by(month=pl.col("notice_date").dt.month_start())
        .agg(
            notices=pl.len(),
            workers=pl.col("num_affected").fill_null(0).sum(),
            notices_missing_workers=pl.col("num_affected").is_null().sum(),
        )
    )
    months = pl.date_range(date(FIRST_YEAR, 1, 1), data_through, "1mo", eager=True)
    return (
        pl.DataFrame({"month": months})
        .join(counts, on="month", how="left")
        .with_columns(pl.exclude("month").fill_null(0).cast(pl.Int64))
        .sort("month")
    )
