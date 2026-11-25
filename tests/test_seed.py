from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from layoffs_tracker.months import METHOD_SWITCH
from layoffs_tracker.schema import FILINGS_SCHEMA
from layoffs_tracker.seed import AI_REASON_SEED, LAYOFFS_SEED, build_seed

FILINGS = Path("data/filings.parquet")


def paper_rows() -> pl.DataFrame:
    """The committed file's rows from the paper's months (later rows are the pipeline's)."""
    return pl.read_parquet(FILINGS).filter(pl.col("file_date") < METHOD_SWITCH)


def test_seed_matches_paper_counts() -> None:
    df = paper_rows().filter(~pl.col("is_amendment"))
    by_year = (
        df.group_by(pl.col("file_date").dt.year()).agg(pl.col("ai_reason").sum()).sort("file_date")
    )
    assert by_year["ai_reason"].to_list() == [1, 0, 1, 2, 3, 7, 10, 26]
    assert df.filter(pl.col("file_date").dt.year() == 2026).height == 153


def test_paper_rows_are_the_seed_unchanged() -> None:
    df = paper_rows()
    assert df.height == 2217 and df["label_method"].unique().to_list() == ["paper_audited"]
    assert df["ai_reason"].null_count() == 0 and df["file_date"].max() == date(2026, 9, 30)
    assert build_seed(AI_REASON_SEED, LAYOFFS_SEED).equals(df)


def test_committed_file_schema() -> None:
    df = pl.read_parquet(FILINGS)
    assert dict(df.schema) == FILINGS_SCHEMA and df["adsh"].is_unique().all()


def test_seed_joins_cik_and_headcount() -> None:
    df = build_seed(AI_REASON_SEED, LAYOFFS_SEED)
    assert df["cik"].null_count() == 0
    assert df["headcount"].drop_nulls().len() == 741  # the paper extracted a count for ~1 in 3
