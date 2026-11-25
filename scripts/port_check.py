"""Live check of the SEC port: list Jul-Sep 2026 filings and compare with the paper's.

Run from the repo root with SEC_USER_AGENT exported ("Your Name your@email"):
    uv run python scripts/port_check.py
Prints only counts and accession numbers, never the User-Agent. Exit 1 on any difference.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import polars as pl

from layoffs_tracker.months import month_end
from layoffs_tracker.sec import Fetcher, list_filings

MONTHS = [(date(2026, m, 1), month_end(date(2026, m, 1))) for m in (7, 8, 9)]
SEED = Path("data/seed/sec_8k_layoffs.parquet")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        live = {
            f.adsh
            for f in list_filings(Fetcher(Path(tmp), os.environ.get("SEC_USER_AGENT", "")), MONTHS)
        }
    paper = set(
        pl.read_parquet(SEED).filter(pl.col("file_date").is_between(MONTHS[0][0], MONTHS[-1][1]))[
            "adsh"
        ]
    )
    print(f"live {len(live)}, paper {len(paper)}, both {len(live & paper)}")
    print("only live:", sorted(live - paper))
    print("only paper:", sorted(paper - live))
    return 0 if live == paper else 1


if __name__ == "__main__":
    sys.exit(main())
