from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from layoffs_tracker import cli
from layoffs_tracker.cli import main
from layoffs_tracker.months import METHOD_SWITCH
from tests.fakes import FakeFetcher, FakeTransport, hit
from tests.gitrepo import commit_count, make_origin
from tests.test_label import filing_doc
from tests.test_warn import fixture_notices

STEPS = ("checkout", "fetch-sec", "label", "fetch-warn", "build-metrics", "publish", "seed")
SHA = "c" * 40


def test_help_lists_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    for cmd in STEPS:
        assert cmd in out


def test_seed_writes_the_history(tmp_path: Path) -> None:
    dest = tmp_path / "filings.parquet"
    main(["seed", "--out", str(dest)])
    assert pl.read_parquet(dest).height == 2217


@pytest.fixture
def pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A local 'origin' holding the seed, and fakes for SEC, Jev and Kadoa."""
    seed = Path("data/filings.parquet").read_bytes()
    bare = make_origin(tmp_path, {"data/filings.parquet": seed})
    docs = {"a-1": "We will cut 300 employees as AI tools take over.", "a-2": "We cut 40 jobs."}
    hits = [hit("a-1", "2026-10-02"), hit("a-2", "2026-10-09")]

    def fake_fetcher(work: Path) -> FakeFetcher:
        f = FakeFetcher(work / "sec_cache", {f'"Item 2.05"|{METHOD_SWITCH}': hits})
        f.docs = {f"/{a.replace('-', '')}/d1.htm": filing_doc(t) for a, t in docs.items()}
        return f

    def fake_download(sha: str, dest: Path) -> Path:
        path = dest / sha[:7] / "notices.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(fixture_notices()))
        return path

    monkeypatch.setattr(cli, "make_fetcher", fake_fetcher)
    monkeypatch.setattr(cli, "make_transport", lambda: FakeTransport())
    monkeypatch.setattr(cli, "latest_sha", lambda: SHA)
    monkeypatch.setattr(cli, "download", fake_download)
    return bare, tmp_path / "work"


def run_month(bare: Path, work: Path, month: str) -> None:
    w = ["--work", str(work)]
    main(["checkout", *w, "--repo-url", str(bare)])
    main(["fetch-sec", *w, "--run-month", month])
    main(["label", *w])
    main(["fetch-warn", *w])
    main(["build-metrics", *w, "--run-month", month])
    main(["publish", *w])


def test_pipeline_publishes_once_and_reruns_are_no_ops(pipeline: tuple[Path, Path]) -> None:
    bare, work = pipeline
    run_month(bare, work, "2026-10")
    assert commit_count(bare) == 2
    latest = json.loads((work / "repo" / "data" / "latest.json").read_text())
    assert latest["data_through"] == "2026-10" and latest["sources"]["kadoa_sha"] == SHA
    filings = pl.read_parquet(work / "repo" / "data" / "filings.parquet")
    oct_rows = filings.filter(pl.col("file_date") >= METHOD_SWITCH)
    assert oct_rows["adsh"].to_list() == ["a-1", "a-2"]
    assert oct_rows["ai_reason"].to_list() == [True, False]
    assert filings.height == 2219

    run_month(bare, work, "2026-10")  # an Airflow retry or a catch-up overlap
    assert commit_count(bare) == 2
    again = pl.read_parquet(work / "repo" / "data" / "filings.parquet")
    assert again.equals(filings)


def test_fetch_sec_skips_known_filings(pipeline: tuple[Path, Path]) -> None:
    bare, work = pipeline
    run_month(bare, work, "2026-10")
    main(["fetch-sec", "--work", str(work), "--run-month", "2026-11"])  # rescans Oct too
    assert pl.read_parquet(work / "new_filings.parquet").height == 0


def test_label_respects_max_calls(pipeline: tuple[Path, Path]) -> None:
    bare, work = pipeline
    main(["checkout", "--work", str(work), "--repo-url", str(bare)])
    main(["fetch-sec", "--work", str(work), "--run-month", "2026-10"])
    with pytest.raises(cli.CallCapExceeded):
        main(["label", "--work", str(work), "--max-calls", "0"])


def test_bad_run_month_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["fetch-sec", "--work", str(tmp_path), "--run-month", "October"])
