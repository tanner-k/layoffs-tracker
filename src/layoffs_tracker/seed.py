"""Build the history rows (Jan 2019 – Sep 2026) from the paper's audited labels."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from layoffs_tracker.schema import FILINGS_SCHEMA, PAPER_METHOD

AI_REASON_SEED = Path("data/seed/sec_8k_ai_reason_2019.parquet")
LAYOFFS_SEED = Path("data/seed/sec_8k_layoffs.parquet")
FILINGS_PATH = Path("data/filings.parquet")
PAPER_LABELED_AT = date(2026, 10, 16)  # the day the paper's audited labels were final


def build_seed(ai_reason_path: Path, layoffs_path: Path) -> pl.DataFrame:
    """Return the paper's filings as FILINGS_SCHEMA rows.

    The 12 filings with a null audited label had no text; the paper counts them as not
    AI-cited, so they become False here.
    """
    labels = pl.read_parquet(ai_reason_path)
    extra = pl.read_parquet(layoffs_path).select("adsh", "cik", "headcount")
    return (
        labels.join(extra, on="adsh", how="left")
        .select(
            "adsh",
            "cik",
            "company",
            "file_date",
            "is_amendment",
            "headcount",
            pl.col("ai_reason_audited").fill_null(False).alias("ai_reason"),
            pl.col("ai_subtype_audited").alias("ai_role"),
            pl.lit(PAPER_METHOD).alias("label_method"),
            pl.lit(None, dtype=pl.Utf8).alias("jev_model"),
            pl.lit(PAPER_LABELED_AT).alias("labeled_at"),
        )
        .cast(FILINGS_SCHEMA)  # type: ignore[arg-type]
        .sort("file_date", "adsh")
    )


def write_seed(dest: Path = FILINGS_PATH) -> pl.DataFrame:
    """Build the seed from `data/seed/` and write it to `dest`."""
    df = build_seed(AI_REASON_SEED, LAYOFFS_SEED)
    df.write_parquet(dest)
    return df
