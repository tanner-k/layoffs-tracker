from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from layoffs_tracker.jev.api import load_api_key
from layoffs_tracker.jev.label import CallCapExceeded, label_new
from layoffs_tracker.schema import FILINGS_SCHEMA
from layoffs_tracker.sec import fetch_month_filings
from tests.fakes import FakeFetcher, FakeTransport, hit

OCT = (date(2026, 10, 1), date(2026, 10, 31))
TODAY = date(2026, 11, 3)
DOCS = {
    "a-1": "We will reduce our workforce by 300 employees as AI tools take over this work.",
    "a-2": "We cut 120 positions; our AI products are unaffected.",
    "a-3": "We will lay off 50 employees and invest in artificial intelligence.",
    "a-4": "We will close our Ohio plant and lay off 80 workers.",  # no AI term: no question
}


def filing_doc(text: str) -> str:
    return (
        "<html><body><p>Item 2.05 Costs Associated with Exit or Disposal Activities.</p>"
        f"<p>{text}</p><p>Item 9.01 Financial Statements and Exhibits.</p></body></html>"
    )


def new_filings(tmp_path: Path, docs: dict[str, str] = DOCS) -> tuple[pl.DataFrame, FakeFetcher]:
    """Run the real SEC step over fake SEC responses; return its frame and the fetcher."""
    hits = [hit(a, f"2026-10-0{i}") for i, a in enumerate(docs, 1)]
    fake = FakeFetcher(tmp_path / "sec", {f'"Item 2.05"|{OCT[0]}': hits})
    fake.docs = {f"/{a.replace('-', '')}/d1.htm": filing_doc(t) for a, t in docs.items()}
    return fetch_month_filings(fake, [OCT]), fake


def test_label_new_with_nothing_new_makes_no_calls(tmp_path: Path) -> None:
    empty, fake = new_filings(tmp_path, {})
    t = FakeTransport()
    out = label_new(empty, fake, t, tmp_path / "jev", today=TODAY)
    assert out.height == 0 and t.calls == 0 and dict(out.schema) == FILINGS_SCHEMA


def test_label_new_maps_jev_answers(tmp_path: Path) -> None:
    new, fake = new_filings(tmp_path)
    split = {"mention_only": 0.5, "ai_strategy_framing": 0.44, "ai_does_work": 0.06}
    t = FakeTransport({"a-2": {"mention_only": 0.95, "unknown": 0.05}, "a-3": split})
    out = label_new(new, fake, t, tmp_path / "jev", today=TODAY)
    assert out["adsh"].to_list() == ["a-1", "a-2", "a-3", "a-4"]
    assert out["ai_reason"].to_list() == [True, False, None, False]
    assert out["ai_role"].to_list() == ["ai_does_work", "mention_only", None, "absent"]
    assert out["headcount"].to_list() == [300, 120, 50, 80]
    assert set(out["label_method"]) == {"jev"} and set(out["jev_model"]) == {"jev-1.13.0"}
    assert out["labeled_at"].to_list() == [TODAY] * 4 and dict(out.schema) == FILINGS_SCHEMA
    assert t.calls == 1  # three AI questions fit in one request


def test_cap_exceeded_raises(tmp_path: Path) -> None:
    new, fake = new_filings(tmp_path)
    with pytest.raises(CallCapExceeded, match="cap"):
        label_new(new, fake, FakeTransport(), tmp_path / "jev", max_calls=0, today=TODAY)


def test_second_run_is_served_from_cache(tmp_path: Path) -> None:
    new, fake = new_filings(tmp_path)
    first = label_new(new, fake, FakeTransport(), tmp_path / "jev", today=TODAY)
    again = FakeTransport()
    second = label_new(new, fake, again, tmp_path / "jev", max_calls=0, today=TODAY)
    assert again.calls == 0 and second.equals(first)


def test_missing_document_is_unresolved(tmp_path: Path) -> None:
    new, _ = new_filings(tmp_path, {"a-1": DOCS["a-1"]})
    empty_sec = FakeFetcher(tmp_path / "other")  # SEC no longer serves the document
    out = label_new(new, empty_sec, FakeTransport(), tmp_path / "jev", today=TODAY)
    assert out["ai_reason"].to_list() == [None] and out["ai_role"].to_list() == [None]


def test_missing_api_key_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TYPESAFE_API_KEY"):
        load_api_key()


def test_api_key_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", " k-123 ")
    assert load_api_key() == "k-123"


def test_cap_is_checked_before_any_call(tmp_path: Path) -> None:
    # 33 filings that mention AI need two requests; a cap of 1 must spend nothing,
    # or each Airflow retry would spend another cap's worth.
    adshs = [f"b-{i}" for i in range(33)]
    hits = [hit(a, f"2026-10-{i % 28 + 1:02d}") for i, a in enumerate(adshs)]
    fake = FakeFetcher(tmp_path / "sec", {f'"Item 2.05"|{OCT[0]}': hits})
    fake.docs = {f"/{a.replace('-', '')}/d1.htm": filing_doc(DOCS["a-1"]) for a in adshs}
    new = fetch_month_filings(fake, [OCT])
    t = FakeTransport()
    with pytest.raises(CallCapExceeded, match="2 requests"):
        label_new(new, fake, t, tmp_path / "jev", max_calls=1, today=TODAY)
    assert t.calls == 0
