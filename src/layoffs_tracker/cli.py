"""`layoffs-tracker <step> --work DIR`: one subcommand per Airflow task, in DAG order.

Each step reads the previous step's files from the work directory (the cluster's shared
volume) and writes its own, so any step can be retried alone.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from layoffs_tracker.jev.api import MODEL, Transport, http_transport, load_api_key
from layoffs_tracker.jev.label import MAX_CALLS, CallCapExceeded, label_new
from layoffs_tracker.metrics import build_latest
from layoffs_tracker.months import METHOD_SWITCH, parse_month, rescan_window
from layoffs_tracker.publish import checkout, merge_filings, publish
from layoffs_tracker.schema import FILINGS_SCHEMA
from layoffs_tracker.sec import FILINGS_RAW_SCHEMA, Fetcher, fetch_month_filings
from layoffs_tracker.seed import FILINGS_PATH, write_seed
from layoffs_tracker.warn import FIRST_YEAR, balanced_states, download, latest_sha, load_notices
from layoffs_tracker.warn import national_month as warn_national_month

__all__ = ["CallCapExceeded", "main"]
LOG = logging.getLogger("layoffs_tracker")
UA_VAR = "SEC_USER_AGENT"


# Factories the tests replace; production reads the environment.
def make_fetcher(work: Path) -> Fetcher:
    return Fetcher(work / "sec_cache", os.environ.get(UA_VAR, ""))


def make_transport() -> Transport:
    return http_transport(load_api_key())


# --- paths in the work directory ---------------------------------------------------
def repo(work: Path) -> Path:
    return work / "repo"


def filings_file(work: Path) -> Path:
    return repo(work) / "data" / "filings.parquet"


def latest_file(work: Path) -> Path:
    return repo(work) / "data" / "latest.json"


NEW, LABELED = "new_filings.parquet", "labeled.parquet"
NOTICES, KADOA_SHA = "warn_notices.parquet", "kadoa_sha.txt"


# --- steps -------------------------------------------------------------------------
def cmd_checkout(a: argparse.Namespace) -> None:
    checkout(a.repo_url, repo(a.work))


def cmd_fetch_sec(a: argparse.Namespace) -> None:
    """Filings in the run month's rescan window whose accession number is not yet known."""
    months = rescan_window(a.run_month)
    found = (
        fetch_month_filings(make_fetcher(a.work), months)
        if months
        else pl.DataFrame(schema=FILINGS_RAW_SCHEMA)
    )
    known = pl.read_parquet(filings_file(a.work), columns=["adsh"])["adsh"]
    # EDGAR can return a filing dated before the window; paper months are never relabeled.
    new = found.filter(
        (pl.col("file_date") >= METHOD_SWITCH) & ~pl.col("adsh").is_in(known.implode())
    )
    new.write_parquet(a.work / NEW)
    LOG.info("%d filings in %d months, %d new", found.height, len(months), new.height)


def cmd_label(a: argparse.Namespace) -> None:
    new = pl.read_parquet(a.work / NEW)
    if new.height == 0:  # a quiet month needs neither Jev nor its key
        pl.DataFrame(schema=FILINGS_SCHEMA).write_parquet(a.work / LABELED)
        LOG.info("nothing new to label")
        return
    out = label_new(
        new,
        make_fetcher(a.work),
        make_transport(),
        a.work / "jev_cache",
        today=date.today(),
        max_calls=a.max_calls,
    )
    out.write_parquet(a.work / LABELED)
    LOG.info("labeled %d filings", out.height)


def cmd_fetch_warn(a: argparse.Namespace) -> None:
    sha = latest_sha()
    notices = load_notices(download(sha, a.work / "kadoa"))
    notices.write_parquet(a.work / NOTICES)
    (a.work / KADOA_SHA).write_text(sha + "\n")
    LOG.info("%d WARN notices at Kadoa %s", notices.height, sha[:7])


def keep_generated_at(new: dict[str, Any], path: Path) -> dict[str, Any]:
    """Reuse the old timestamp when nothing else changed, so a re-run publishes nothing."""
    if not path.exists():
        return new
    old = json.loads(path.read_text())
    same = {k: v for k, v in old.items() if k != "generated_at"} == {
        k: v for k, v in new.items() if k != "generated_at"
    }
    return {**new, "generated_at": old["generated_at"]} if same else new


def cmd_build_metrics(a: argparse.Namespace) -> None:
    old = pl.read_parquet(filings_file(a.work))
    merged = merge_filings(old, pl.read_parquet(a.work / LABELED))
    merged.write_parquet(filings_file(a.work))
    notices = pl.read_parquet(a.work / NOTICES)
    # Re-running an older month must not move the page backwards.
    through = a.run_month
    if latest_file(a.work).exists():
        prior = json.loads(latest_file(a.work).read_text())["data_through"]
        through = max(through, parse_month(prior))
    states = balanced_states(notices, FIRST_YEAR, through)
    sources = {
        "jev_model": MODEL,
        "kadoa_sha": (a.work / KADOA_SHA).read_text().strip(),
        "warn_states": states,
    }
    warn = warn_national_month(notices, states, through)
    latest = build_latest(merged, warn, sources, through, datetime.now(UTC))
    latest = keep_generated_at(json.loads(json.dumps(latest)), latest_file(a.work))
    latest_file(a.work).write_text(json.dumps(latest, indent=1) + "\n")
    LOG.info(
        "%d filings (%d added), %d WARN states",
        merged.height,
        merged.height - old.height,
        len(states),
    )


def cmd_publish(a: argparse.Namespace) -> None:
    through = json.loads(latest_file(a.work).read_text())["data_through"]
    pushed = publish(repo(a.work), f"data: refresh through {through}")
    LOG.info("pushed" if pushed else "no change; nothing pushed")


def cmd_seed(a: argparse.Namespace) -> None:
    df = write_seed(a.out)
    LOG.info("wrote %s (%d rows)", a.out, df.height)


def month_arg(s: str) -> date:
    try:
        return parse_month(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM, got {s!r}") from None


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="layoffs-tracker", description=(__doc__ or "").split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    work = argparse.ArgumentParser(add_help=False)
    work.add_argument("--work", type=Path, required=True, help="shared work directory")
    month = argparse.ArgumentParser(add_help=False)
    month.add_argument("--run-month", type=month_arg, required=True, help="data month, YYYY-MM")

    def add(name: str, fn: Callable[[argparse.Namespace], None], help_: str, *parents: Any) -> Any:
        p = sub.add_parser(name, help=help_, parents=list(parents))
        p.set_defaults(fn=fn)
        return p

    add("checkout", cmd_checkout, "clone or reset the data repo", work).add_argument(
        "--repo-url", required=True
    )
    add("fetch-sec", cmd_fetch_sec, "find new 8-K Item 2.05 filings", work, month)
    add("label", cmd_label, "label new filings with Jev", work).add_argument(
        "--max-calls", type=int, default=MAX_CALLS
    )
    add("fetch-warn", cmd_fetch_warn, "download Kadoa's WARN notices", work)
    add("build-metrics", cmd_build_metrics, "merge filings, write latest.json", work, month)
    add("publish", cmd_publish, "commit and push data/", work)
    add("seed", cmd_seed, "build data/filings.parquet from the paper").add_argument(
        "--out", type=Path, default=FILINGS_PATH
    )
    return ap


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parser().parse_args(argv)
    args.fn(args)
