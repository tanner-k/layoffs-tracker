"""Merge new filings into the history, and sync the data repo with git.

Git auth comes from the environment (`GIT_SSH_COMMAND` with the mounted deploy key); this
code never reads the key.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import polars as pl

from layoffs_tracker.months import METHOD_SWITCH
from layoffs_tracker.schema import FILINGS_SCHEMA

BRANCH = "main"
BOT = ["-c", "user.name=layoffs-tracker bot", "-c", "user.email=layoffs-tracker@users.noreply"]


def merge_filings(old: pl.DataFrame, new: pl.DataFrame) -> pl.DataFrame:
    """Add `new` rows to `old`: an accession number already in `old` keeps its old row, and a
    `new` row dated before METHOD_SWITCH is dropped (paper rows are frozen)."""
    fresh = new.filter(
        (pl.col("file_date") >= METHOD_SWITCH) & ~pl.col("adsh").is_in(old["adsh"].implode())
    )
    return (
        pl.concat([old, fresh])
        .cast(FILINGS_SCHEMA)  # type: ignore[arg-type]
        .sort("file_date", "adsh")
    )


def git(repo: Path, *args: str) -> str:
    """Run git in `repo`; raise RuntimeError with git's own message on failure."""
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode:
        verb = next(a for a in args if not a.startswith("-") and "=" not in a)
        raise RuntimeError(f"git {verb} failed: {r.stderr.strip()}")
    return r.stdout


def checkout(repo_url: str, dest: Path) -> None:
    """Clone `repo_url` into `dest`, or reset an existing clone to the remote's main."""
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        git(dest.parent, "clone", "-q", "--branch", BRANCH, repo_url, dest.name)
        return
    git(dest, "fetch", "-q", "origin", BRANCH)
    git(dest, "reset", "-q", "--hard", f"origin/{BRANCH}")


def publish(repo_dir: Path, message: str) -> bool:
    """Commit changes under `data/` and push them. Returns False when nothing changed."""
    if not (repo_dir / "data").is_dir():
        return False
    git(repo_dir, "add", "--", "data")
    if not git(repo_dir, "diff", "--cached", "--name-only").strip():
        return False
    git(repo_dir, *BOT, "commit", "-q", "-m", message)
    git(repo_dir, "push", "-q", "origin", f"HEAD:{BRANCH}")
    return True
