"""Before go-live: does the tracker's labeler (AI question only) agree with the paper's Jev run?

Lists the Jan-Sep 2026 Item 2.05 filings from SEC, labels them with the tracker's `label_new`
(live Jev calls, at most 100), and compares with the paper's full-pipeline Jev labels
(`jev_ai_reason`) and audited labels in data/seed/. Original filings only. Prints counts only;
caches live in a temp dir.

    set -a; source path/to/.env; set +a   # SEC_USER_AGENT and TYPESAFE_API_KEY
    uv run python scripts/jev_check.py
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

import polars as pl

from layoffs_tracker.jev.api import http_transport, load_api_key
from layoffs_tracker.jev.label import label_new
from layoffs_tracker.months import month_end
from layoffs_tracker.sec import Fetcher, fetch_month_filings

MONTHS = [(date(2026, m, 1), month_end(date(2026, m, 1))) for m in range(1, 10)]
SEED = Path("data/seed/sec_8k_ai_reason_2019.parquet")


def main() -> None:
    transport = http_transport(load_api_key())
    with tempfile.TemporaryDirectory() as tmp:
        fetch = Fetcher(Path(tmp) / "sec", os.environ.get("SEC_USER_AGENT", ""))
        found = fetch_month_filings(fetch, MONTHS)
        ours = label_new(found, fetch, transport, Path(tmp) / "jev", today=date.today())
    paper = pl.read_parquet(SEED).select(
        "adsh", "is_amendment", paper_jev="jev_ai_reason", audited="ai_reason_audited"
    )
    both = (
        ours.select("adsh", tracker="ai_reason")
        .join(paper, on="adsh")
        .filter(~pl.col("is_amendment"))
    )
    print(f"{both.height} original Jan-Sep 2026 filings found by both")
    for col, name in (
        ("audited", "paper, audited"),
        ("paper_jev", "paper's Jev run"),
        ("tracker", "tracker's Jev"),
    ):
        print(f"{name:>16}: {both[col].sum()} AI-cited, {both[col].null_count()} unresolved")
    same = both.filter(pl.col("tracker").eq_missing(pl.col("paper_jev"))).height
    print(f"tracker agrees with the paper's Jev run on {same} of {both.height}")


if __name__ == "__main__":
    main()
