from __future__ import annotations

import io
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from layoffs_tracker.months import METHOD_SWITCH, previous_month, rescan_window
from layoffs_tracker.sec import (
    PHRASES,
    SELF_CHECKS,
    Fetcher,
    extract,
    fetch_month_filings,
    find_headcount,
    list_filings,
    search_month,
)
from tests.fakes import FakeFetcher, hit

OCT = (date(2026, 10, 1), date(2026, 10, 31))
ITEM_205 = '"Item 2.05"'

MAIN_DOC = """<html><body>
<p>Item 2.05 Costs Associated with Exit or Disposal Activities.</p>
<p>On October 5, 2026, the Company approved a plan to reduce its workforce by approximately
300 employees as it adopts artificial intelligence tools.</p>
<p>Item 9.01 Financial Statements and Exhibits.</p>
</body></html>"""

NO_COUNT_DOC = """<html><body>
<p>Item 2.05 Costs Associated with Exit or Disposal Activities.</p>
<p>The Company approved a restructuring plan. See the press release.</p>
<p>Item 9.01 Financial Statements and Exhibits.</p>
</body></html>"""

PRESS_RELEASE = "<html><body><p>Acme will lay off 1,200 people, about 6% of its workforce.</p>"


def test_rescan_window_clips_at_method_switch() -> None:
    assert rescan_window(date(2026, 10, 1)) == [(date(2026, 10, 1), date(2026, 10, 31))]
    assert rescan_window(date(2026, 12, 1)) == [
        (date(2026, 10, 1), date(2026, 10, 31)),
        (date(2026, 11, 1), date(2026, 11, 30)),
        (date(2026, 12, 1), date(2026, 12, 31)),
    ]


def test_rescan_window_crosses_a_year() -> None:
    assert rescan_window(date(2027, 2, 1)) == [
        (date(2026, 12, 1), date(2026, 12, 31)),
        (date(2027, 1, 1), date(2027, 1, 31)),
        (date(2027, 2, 1), date(2027, 2, 28)),
    ]


def test_rescan_window_before_switch_is_empty() -> None:
    assert rescan_window(date(2026, 9, 1)) == []
    assert date(2026, 10, 1) == METHOD_SWITCH


def test_previous_month() -> None:
    assert previous_month(date(2027, 1, 3)) == date(2026, 12, 1)
    assert previous_month(date(2026, 11, 30)) == date(2026, 10, 1)


@pytest.mark.parametrize(("sentence", "expected"), SELF_CHECKS)
def test_find_headcount(sentence: str, expected: tuple[int | None, float | None]) -> None:
    assert find_headcount(sentence)[:2] == expected


def test_search_month_raises_on_truncated_result(tmp_path: Path) -> None:
    fake = FakeFetcher(tmp_path, {f"{ITEM_205}|2026-10-01": [hit("a-1", "2026-10-02")]}, {}, "gte")
    with pytest.raises(RuntimeError, match="truncated"):
        search_month(fake, "item205", ITEM_205, *OCT)


def test_search_month_pages_by_100(tmp_path: Path) -> None:
    hits = [hit(f"a-{i:03}", "2026-10-02") for i in range(150)]
    fake = FakeFetcher(tmp_path, {f"{ITEM_205}|2026-10-01": hits})
    assert len(search_month(fake, "item205", ITEM_205, *OCT)) == 150
    assert len(fake.urls) == 2


def test_list_filings_keeps_one_main_doc_per_item_205_filing(tmp_path: Path) -> None:
    search = {
        f"{ITEM_205}|2026-10-01": [
            hit("a-1", "2026-10-02", name="d1.htm"),
            hit("a-1", "2026-10-02", name="ex99.htm", file_type="EX-99.1"),
            hit("a-2", "2026-10-03", items=("8.01",)),
        ],
        f"{PHRASES['exitdisposal']}|2026-10-01": [hit("a-1", "2026-10-02", name="d1.htm")],
    }
    listings = list_filings(FakeFetcher(tmp_path, search), [OCT])
    assert [(f.adsh, f.main_doc, f.company) for f in listings] == [("a-1", "d1.htm", "Acme Corp")]


def test_search_pages_are_not_cached(tmp_path: Path) -> None:
    # A rescan must see filings EDGAR indexed after the last run.
    fake = FakeFetcher(tmp_path, {f"{ITEM_205}|2026-10-01": [hit("a-1", "2026-10-02")]})
    list_filings(fake, [OCT])
    fake.search[f"{ITEM_205}|2026-10-01"].append(hit("a-2", "2026-10-30"))
    assert [f.adsh for f in list_filings(fake, [OCT])] == ["a-1", "a-2"]


def test_extract_reads_item_205_headcount_and_ai_flag(tmp_path: Path) -> None:
    fake = FakeFetcher(tmp_path, {f"{ITEM_205}|2026-10-01": [hit("a-1", "2026-10-02")]})
    fake.docs = {"/d1.htm": MAIN_DOC}
    (listing,) = list_filings(fake, [OCT])
    x = extract(fake, listing)
    assert (x["headcount"], x["headcount_source"], x["ai_mentioned"]) == (300, "item_2_05", True)


def test_extract_falls_back_to_ex99(tmp_path: Path) -> None:
    fake = FakeFetcher(tmp_path, {f"{ITEM_205}|2026-10-01": [hit("a-1", "2026-10-02")]})
    index = '{"directory": {"item": [{"name": "d1.htm"}, {"name": "ex99-1.htm"}]}}'
    fake.docs = {"/d1.htm": NO_COUNT_DOC, "/index.json": index, "/ex99-1.htm": PRESS_RELEASE}
    (listing,) = list_filings(fake, [OCT])
    x = extract(fake, listing)
    assert (x["headcount"], x["headcount_pct"], x["headcount_source"]) == (1200, 6.0, "ex99")
    assert x["ai_mentioned"] is False


def test_documents_are_cached(tmp_path: Path) -> None:
    fake = FakeFetcher(tmp_path, {f"{ITEM_205}|2026-10-01": [hit("a-1", "2026-10-02")]})
    fake.docs = {"/d1.htm": MAIN_DOC}
    (listing,) = list_filings(fake, [OCT])
    extract(fake, listing)
    fake.docs = {}
    assert extract(fake, listing)["headcount"] == 300


def test_fetch_month_filings_schema(tmp_path: Path) -> None:
    fake = FakeFetcher(tmp_path, {f"{ITEM_205}|2026-10-01": [hit("a-1", "2026-10-02")]})
    fake.docs = {"/d1.htm": MAIN_DOC}
    df = fetch_month_filings(fake, [OCT])
    assert df.height == 1 and df["headcount"].to_list() == [300]
    assert {"adsh", "file_date", "is_amendment", "url", "snippet"} <= set(df.columns)


def test_fetch_month_filings_empty(tmp_path: Path) -> None:
    df = fetch_month_filings(FakeFetcher(tmp_path), [OCT])
    assert df.height == 0 and "adsh" in df.columns


def test_fetcher_requires_user_agent(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="SEC_USER_AGENT"):
        Fetcher(tmp_path, user_agent="")


class _Resp(io.BytesIO):
    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://www.sec.gov/x", code, "err", {}, None)  # type: ignore[arg-type]


def test_download_sends_user_agent_and_returns_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _Resp:
        seen.append(req.get_header("User-agent") or "")
        return _Resp(b"body")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    f = Fetcher(tmp_path, user_agent="Jane Doe jane@example.com")
    assert f.get("https://www.sec.gov/x", tmp_path / "x") == "body"
    assert seen == ["Jane Doe jane@example.com"]


def test_download_404_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: Any, timeout: float) -> _Resp:
        raise _http_error(404)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert Fetcher(tmp_path, user_agent="a b@c.d").get("https://www.sec.gov/x", None) is None


def test_download_403_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: Any, timeout: float) -> _Resp:
        raise _http_error(403)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    f = Fetcher(tmp_path, user_agent="a b@c.d")
    with pytest.raises(RuntimeError, match="SEC_USER_AGENT"):
        f.get("https://www.sec.gov/x", None)
    with pytest.raises(RuntimeError, match="SEC_USER_AGENT"):  # later calls stop too
        f.get("https://www.sec.gov/y", None)


def test_download_gives_up_after_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: Any, timeout: float) -> _Resp:
        raise _http_error(429)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="giving up"):
        Fetcher(tmp_path, user_agent="a b@c.d").get("https://www.sec.gov/x", None)
