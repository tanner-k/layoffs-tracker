"""Test doubles that replace only the network call; caching and parsing stay real."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from layoffs_tracker.jev.api import MODEL
from layoffs_tracker.sec import SEARCH_URL, Fetcher

AI_ROLE_NAMES = ("ai_does_work", "reinvest_in_ai", "ai_strategy_framing", "mention_only", "unknown")


def ai_answer(**probs: float) -> dict[str, Any]:
    """A valid Jev choice answer for the AI question; unnamed roles share what is left."""
    rest = [r for r in AI_ROLE_NAMES if r not in probs]
    share = max(0.0, 1 - sum(probs.values())) / len(rest) if rest else 0.0
    full = {r: share for r in rest} | probs
    top = max(full, key=full.__getitem__)
    return {"type": "choice", "choice": top, "probabilities": full, "confidence": full[top]}


class FakeTransport:
    """Answers every AI question locally. `by_adsh` sets one filing's probabilities."""

    def __init__(self, by_adsh: dict[str, dict[str, float]] | None = None) -> None:
        self.by_adsh = by_adsh or {}
        self.calls = 0
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, body: dict[str, Any]) -> Any:
        self.calls += 1
        self.bodies.append(body)
        answers = {
            key: ai_answer(**self.by_adsh.get(key.rsplit("_", 1)[0], {"ai_does_work": 0.9}))
            for key in body["questions"]
        }
        n = len(answers)
        usage = {"input_tokens": 100 * n, "output_tokens": 5 * n}
        return {"model": MODEL, "answers": answers, "usage": usage}


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
