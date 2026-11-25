from __future__ import annotations

import io
import json
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from layoffs_tracker.warn import balanced_states, download, latest_sha, load_notices, national_month

_ids = iter(range(1, 10_000))


def notice(state: str, received: str | None, workers: int | None = 10, **kw: Any) -> dict[str, Any]:
    n = next(_ids)
    return {
        "id": n,
        "state": state,
        "company": kw.get("company", f"Co {n}"),
        "city": "Town",
        "county": "County",
        "pdf_url": f"https://example.com/{n}.pdf",
        "num_affected": workers,
        "received_date": received,
        "effective_date": kw.get("effective"),
        "event_type": kw.get("event_type", "layoff"),
    }


def yearly(state: str, years: range) -> list[dict[str, Any]]:
    return [notice(state, f"{y}-03-01") for y in years]


def fixture_notices() -> list[dict[str, Any]]:
    oct_a = notice("AZ", "2026-10-05", 100, company="Same")
    dup = {**oct_a, "id": 9_999, "pdf_url": "https://example.com/dup.pdf"}  # same notice twice
    return [
        *yearly("AZ", range(2019, 2027)),
        oct_a,
        dup,
        notice("AZ", "2026-10-06", 50),
        notice("AZ", None, None, effective="2026-12-20"),  # notice date imputed: 2026-10-21
        notice("AZ", "2026-10-07", 999, event_type="amendment"),  # never counted
        *yearly("CO", range(2019, 2021)),
        *yearly("CO", range(2022, 2027)),  # CO has no 2021 notice
        *yearly("TX", range(2019, 2027)),
        notice("TX", None),
        notice("TX", None),  # 2 of 10 TX notices undated: share 0.2
        notice("ZZ", "2026-10-05"),  # not a US state
    ]


@pytest.fixture
def notices(tmp_path: Path) -> pl.DataFrame:
    path = tmp_path / "notices.json"
    path.write_text(json.dumps(fixture_notices()))
    return load_notices(path)


def test_balanced_states_uses_last_complete_year(notices: pl.DataFrame) -> None:
    # AZ has notices 2019-2026, none yet in 2027; CO is missing 2021
    assert balanced_states(notices, 2019, data_through=date(2027, 1, 1)) == ["AZ"]
    assert balanced_states(notices, 2019, data_through=date(2026, 12, 1)) == ["AZ"]


def test_balanced_states_drops_mostly_undated_state(notices: pl.DataFrame) -> None:
    assert "TX" not in balanced_states(notices, 2019, data_through=date(2026, 10, 1))
    assert balanced_states(notices, 2022, data_through=date(2026, 10, 1)) == ["AZ", "CO"]


def test_national_month_sums_known_workers_and_counts_missing(notices: pl.DataFrame) -> None:
    out = national_month(notices, ["AZ"], data_through=date(2026, 10, 1))
    row = out.filter(pl.col("month") == date(2026, 10, 1)).row(0, named=True)
    assert (row["notices"], row["workers"], row["notices_missing_workers"]) == (3, 150, 1)


def test_national_month_covers_every_month_and_stops_at_data_through(
    notices: pl.DataFrame,
) -> None:
    out = national_month(notices, ["AZ"], data_through=date(2026, 10, 1))
    assert out["month"].to_list()[0] == date(2019, 1, 1)
    assert out["month"].to_list()[-1] == date(2026, 10, 1) and out.height == 94
    assert out.filter(pl.col("month") == date(2019, 2, 1))["notices"].item() == 0
    assert out.schema["notices"] == pl.Int64 and out.schema["month"] == pl.Date


def test_load_notices_names_missing_column(tmp_path: Path) -> None:
    rows = [{k: v for k, v in notice("AZ", "2026-01-01").items() if k != "num_affected"}]
    path = tmp_path / "notices.json"
    path.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match="num_affected"):
        load_notices(path)


class _Resp(io.BytesIO):
    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


SHA = "a" * 40


def test_latest_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(req: urllib.request.Request, timeout: float) -> _Resp:
        assert req.full_url.endswith("/commits/main")
        return _Resp(json.dumps({"sha": SHA}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert latest_sha() == SHA


def test_latest_sha_rejects_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: _Resp(b'{"sha": "x"}'))
    with pytest.raises(ValueError, match="SHA"):
        latest_sha()


def test_download_caches_and_rejects_html(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bodies = [b"[]", b"<html>rate limited</html>"]
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: _Resp(bodies.pop(0)))
    path = download(SHA, tmp_path)
    assert path.read_text() == "[]" and download(SHA, tmp_path) == path  # second call: cached
    with pytest.raises(ValueError, match="not JSON"):
        download("b" * 40, tmp_path)
