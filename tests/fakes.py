"""Test doubles that replace only the network call; caching and parsing stay real."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from layoffs_tracker.sec import SEARCH_URL, Fetcher


def hit(
    adsh: str,
    file_date: str,
    *,
    name: str = "d1.htm",
    items: tuple[str, ...] = ("2.05", "9.01"),
    file_type: str = "8-K",
    form: str = "8-K",
    company: str = "Acme Corp",
) -> dict[str, Any]:
    """Return one EDGAR full-text-search hit shaped like the real API's."""
    return {
        "_id": f"{adsh}:{name}",
        "_source": {
            "adsh": adsh,
            "ciks": ["0000012345"],
            "display_names": [f"{company}  (ACME)  (CIK 0000012345)"],
            "file_date": file_date,
            "form": form,
            "items": list(items),
            "biz_states": ["UT"],
            "inc_states": ["DE"],
            "sics": ["7372"],
            "file_type": file_type,
        },
    }


class FakeFetcher(Fetcher):
    """A Fetcher whose downloads come from dicts instead of SEC.

    `search` maps "<phrase>|<startdt>" to every hit for that month; `docs` maps a URL
    suffix to its body. Unknown URLs return None, like a 404.
    """

    def __init__(
        self,
        cache_dir: Path,
        search: dict[str, list[dict[str, Any]]] | None = None,
        docs: dict[str, str] | None = None,
        relation: str = "eq",
    ) -> None:
        super().__init__(cache_dir, user_agent="Test Runner test@example.com")
        self.search = search or {}
        self.docs = docs or {}
        self.relation = relation
        self.urls: list[str] = []

    def _download(self, url: str) -> bytes | None:
        self.urls.append(url)
        if url.startswith(SEARCH_URL):
            q = parse_qs(urlsplit(url).query)
            hits = self.search.get(f"{q['q'][0]}|{q['startdt'][0]}", [])
            start = int(q["from"][0])
            total = {"value": len(hits), "relation": self.relation}
            page = {"hits": {"total": total, "hits": hits[start : start + 100]}}
            return json.dumps(page).encode()
        for suffix, body in self.docs.items():
            if url.endswith(suffix):
                return body.encode()
        return None
