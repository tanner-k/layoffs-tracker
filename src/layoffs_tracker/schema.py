"""The schema of `data/filings.parquet`: one row per 8-K Item 2.05 filing."""

from __future__ import annotations

import polars as pl

FILINGS_SCHEMA: dict[str, pl.DataType] = {
    "adsh": pl.Utf8(),
    "cik": pl.Utf8(),
    "company": pl.Utf8(),
    "file_date": pl.Date(),
    "is_amendment": pl.Boolean(),
    "headcount": pl.Int64(),
    "ai_reason": pl.Boolean(),  # null = Jev left it unresolved (live months only)
    "ai_role": pl.Utf8(),
    "label_method": pl.Utf8(),  # "paper_audited" or "jev"
    "jev_model": pl.Utf8(),
    "labeled_at": pl.Date(),
}

PAPER_METHOD = "paper_audited"
JEV_METHOD = "jev"
