from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from layoffs_tracker.publish import checkout, merge_filings, publish
from layoffs_tracker.schema import FILINGS_SCHEMA
from tests.gitrepo import commit_count, git, make_origin
from tests.test_metrics import row

MSG = "data: refresh through 2026-10"


def frame(*rows: dict[str, object]) -> pl.DataFrame:
    return pl.DataFrame(list(rows), schema=FILINGS_SCHEMA)


def test_merge_is_idempotent() -> None:
    old = frame(row("2026-09-30", False, "paper_audited"))
    new = frame(row("2026-10-02", True, "jev"), row("2026-10-03", None, "jev"))
    once = merge_filings(old, new)
    assert once.height == 3 and merge_filings(once, new).equals(once)


def test_merge_keeps_the_old_row_on_a_duplicate_accession() -> None:
    old = frame(row("2026-10-02", False, "jev"))
    relabeled = old.with_columns(pl.lit(True).alias("ai_reason"))
    assert merge_filings(old, relabeled)["ai_reason"].to_list() == [False]


def test_merge_drops_paper_era_rows_from_new() -> None:
    old = frame(row("2026-09-01", False, "paper_audited"))
    new = frame(row("2026-09-30", True, "jev"), row("2026-10-01", True, "jev"))
    out = merge_filings(old, new)
    assert out["file_date"].to_list() == [date(2026, 9, 1), date(2026, 10, 1)]


def test_merge_sorts_by_date_then_accession() -> None:
    old = frame(row("2026-10-05", False, "jev"))
    new = frame(row("2026-10-01", False, "jev"))
    out = merge_filings(old, new)
    assert out["file_date"].is_sorted() and dict(out.schema) == FILINGS_SCHEMA


@pytest.fixture
def clone(tmp_path: Path) -> tuple[Path, Path]:
    bare = make_origin(tmp_path, {"README.md": b"x\n"})
    work = tmp_path / "work"
    checkout(str(bare), work)
    return bare, work


def test_publish_twice_makes_one_commit(clone: tuple[Path, Path]) -> None:
    bare, repo = clone
    (repo / "data").mkdir()
    (repo / "data" / "latest.json").write_text("{}\n")
    assert publish(repo, MSG) is True
    assert publish(repo, MSG) is False
    assert commit_count(bare) == 2  # initial + one data commit
    assert git(bare, "log", "-1", "--format=%s").strip() == MSG


def test_publish_commits_only_data(clone: tuple[Path, Path]) -> None:
    bare, repo = clone
    (repo / "stray.txt").write_text("not data")
    assert publish(repo, MSG) is False and commit_count(bare) == 1


def test_checkout_resets_to_origin(clone: tuple[Path, Path], tmp_path: Path) -> None:
    bare, repo = clone
    (repo / "README.md").write_text("local edit")
    git(repo, "commit", "-qam", "local only")  # e.g. a publish whose push failed
    checkout(str(bare), repo)
    assert (repo / "README.md").read_text() == "x\n" and commit_count(repo) == 1


def test_publish_fails_loudly_when_push_is_refused(clone: tuple[Path, Path]) -> None:
    bare, repo = clone
    (repo / "data").mkdir()
    (repo / "data" / "latest.json").write_text("{}\n")
    subprocess.run(["chmod", "-R", "a-w", str(bare)], check=True)
    try:
        with pytest.raises(RuntimeError, match="git push failed"):
            publish(repo, MSG)
    finally:
        subprocess.run(["chmod", "-R", "u+w", str(bare)], check=True)


def test_publish_retry_pushes_a_commit_left_by_a_failed_push(clone: tuple[Path, Path]) -> None:
    bare, repo = clone
    (repo / "data").mkdir()
    (repo / "data" / "latest.json").write_text("{}\n")
    subprocess.run(["chmod", "-R", "a-w", str(bare)], check=True)
    try:
        with pytest.raises(RuntimeError, match="git push failed"):
            publish(repo, MSG)
    finally:
        subprocess.run(["chmod", "-R", "u+w", str(bare)], check=True)
    assert publish(repo, MSG) is True  # Airflow's retry
    assert commit_count(bare) == 2
