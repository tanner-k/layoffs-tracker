"""Label new filings with Jev: is AI cited as a reason for the layoff?

Ported from the paper's AI path (`jev_spans.ai_view`, `jev_questions` AI question and packing,
`jev_assemble.ai_fields`, `verify_sec_jev.run_batches`). The span, plan and stage-2 questions are
left out: the tracker stores only the AI label, and headcount comes from the regex, as it does
for the paper's rows.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from layoffs_tracker import sec
from layoffs_tracker.jev.api import (
    MAX_BODY_BYTES,
    MAX_QUESTIONS,
    MODEL,
    Transport,
    check_request,
    encode,
    validate_response,
)
from layoffs_tracker.schema import FILINGS_SCHEMA, JEV_METHOD

LOG = logging.getLogger(__name__)

MAX_CALLS = 100  # live Jev requests per run; more fails the run before publish
MIN_CONFIDENCE = 0.7
# AI counts as a reason when these options together reach MIN_CONFIDENCE; the role is the
# likeliest of them. Jev often splits probability across them (pilot: Block 0.62/0.12/0.15).
AI_REASON_ROLES = ("ai_does_work", "reinvest_in_ai", "ai_strategy_framing")
MAX_SENT_CHARS = 1_200
AI_TEXT_CHARS, AI_LEAD_SENTENCES, AI_LEAD_CHARS = 4_000, 3, 1_500
PACK_HEADROOM_BYTES = 5_000
MAX_STATE_CHARS = 30_000  # TypeSafe's documented source limit per request

AI_ROLES = {
    "ai_does_work": "AI or automation will do work the affected employees did, or makes their "
    "roles unnecessary.",
    "reinvest_in_ai": "Savings or headcount are moved into AI investment or AI roles.",
    "ai_strategy_framing": "General 'AI era' positioning without substitution or reallocation.",
    "mention_only": "AI appears only in product, company, risk or forward-looking text.",
    "unknown": "The sentences do not settle it.",
}


class CallCapExceeded(RuntimeError):
    """More Jev requests were needed than the run's cap allows; nothing is labeled."""


@dataclass(frozen=True)
class View:
    """What Jev sees for one filing."""

    adsh: str
    company: str
    filed: str
    ai_sentences: tuple[dict[str, str], ...]
    has_text: bool


@dataclass(frozen=True)
class Unit:
    """One filing's AI question and the state it reads."""

    adsh: str
    company: str
    filed: str
    ai_sentences: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class BatchResult:
    """One validated request/response pair, from the cache or a live call."""

    path: Path
    answers: dict[str, Any]
    usage: dict[str, int]
    live: bool


# --- text --------------------------------------------------------------------------
def split_sentences(text: str) -> list[str]:
    """Split with the SEC module's splitter; drop empties and cap each sentence."""
    parts = (p.strip() for p in sec.SENT_SPLIT.split(text))
    return [s[:MAX_SENT_CHARS] for s in parts if s]


def take_within(pairs: Sequence[tuple[str, str]], limit: int) -> list[tuple[str, str]]:
    """Leading (source, text) pairs whose total length fits in `limit`."""
    out: list[tuple[str, str]] = []
    used = 0
    for source, text in pairs:
        if used + len(text) > limit:
            break
        out.append((source, text))
        used += len(text)
    return out


def ai_view(section: Sequence[str], ex99: Sequence[str]) -> tuple[dict[str, str], ...]:
    """First section sentences plus every sentence with an AI term, each marked with its source."""
    lead = take_within([("item_2_05", s) for s in section[:AI_LEAD_SENTENCES]], AI_LEAD_CHARS)
    hits = [("item_2_05", s) for s in section] + [("ex99", s) for s in ex99]
    merged = list(dict.fromkeys([*lead, *(p for p in hits if sec.AI_TERMS.search(p[1]))]))
    picked = take_within(merged, AI_TEXT_CHARS)
    return tuple({"id": f"a{i}", "source": src, "text": t} for i, (src, t) in enumerate(picked))


def build_view(listing: sec.Listing, fetch: sec.Fetcher) -> View:
    """The Item 2.05 section and EX-99 sentences that mention AI, for one filing."""
    head = (listing.adsh, listing.company, listing.file_date.isoformat())
    text = sec.main_doc_text(fetch, listing)
    if text is None:
        return View(*head, ai_sentences=(), has_text=False)
    section = split_sentences(sec.item_205_section(text))
    try:
        ex99 = split_sentences(sec.ex99_text(fetch, listing))
    except (json.JSONDecodeError, KeyError, TypeError):  # a malformed index.json
        ex99 = []
    asked = any(sec.AI_TERMS.search(s) for s in [*section, *ex99])
    return View(
        *head, ai_sentences=ai_view(section, ex99) if asked else (), has_text=bool(section or ex99)
    )


# --- questions and packing -----------------------------------------------------------
def question(adsh: str) -> dict[str, Any]:
    """The AI-role question for one filing, worded as in the paper."""
    text = (
        f"What role does AI play in this restructuring? Read state.items[{adsh}].ai_sentences; "
        "each sentence is marked with its source (item_2_05 is the 8-K, ex99 is the press "
        "release)."
    )
    return {
        "type": "choice",
        "instructions": f"For state.items[{adsh}]: {text}",
        "criteria": AI_ROLES,
    }


def item(unit: Unit) -> dict[str, Any]:
    return {"company": unit.company, "filed": unit.filed, "ai_sentences": list(unit.ai_sentences)}


def make_body(group: Sequence[Unit]) -> dict[str, Any]:
    return {
        "model": MODEL,
        "state": {"items": {u.adsh: item(u) for u in group}},
        "questions": {f"{u.adsh}_ai": question(u.adsh) for u in group},
    }


def pack(units: Sequence[Unit]) -> list[dict[str, Any]]:
    """Pack units into request bodies: <= 32 questions, < 500 KB, <= 30,000 state characters."""
    bodies: list[dict[str, Any]] = []
    group: list[Unit] = []
    size = chars = 0
    for u in units:
        u_chars = len(encode(item(u)))
        u_size = u_chars + len(encode(question(u.adsh)))
        if group and (
            len(group) + 1 > MAX_QUESTIONS
            or size + u_size > MAX_BODY_BYTES - PACK_HEADROOM_BYTES
            or chars + u_chars > MAX_STATE_CHARS
        ):
            bodies.append(make_body(group))
            group, size, chars = [], 0, 0
        group.append(u)
        size, chars = size + u_size, chars + u_chars
    return [*bodies, make_body(group)] if group else bodies


# --- cached batch runner ---------------------------------------------------------------
def cache_path(body: Mapping[str, Any], cache_dir: Path) -> Path:
    """Cache file named by a hash of the request body."""
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    return cache_dir / f"{hashlib.sha256(raw).hexdigest()[:32]}.json"


def read_cached(path: Path, questions: Mapping[str, Any]) -> BatchResult | None:
    """Load and re-validate a cached response; None if there is no cache file."""
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))["response"]
    try:
        answers = validate_response(payload, questions)
    except ValueError as err:
        raise ValueError(f"{err} in cached {path.name}; delete it to refetch") from None
    return BatchResult(path, answers, payload["usage"], live=False)


def call_live(body: dict[str, Any], transport: Transport, path: Path) -> BatchResult:
    """Send one request, validate the response, then cache request + response atomically."""
    check_request(body)
    payload = transport(body)
    answers = validate_response(payload, body["questions"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    record = {"request": body, "response": payload}
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return BatchResult(path, answers, payload["usage"], live=True)


def run_batches(
    bodies: Sequence[dict[str, Any]], transport: Transport, cache_dir: Path, max_calls: int
) -> list[BatchResult | None]:
    """Resolve every body from cache or a live call; None where the call budget ran out."""
    results: list[BatchResult | None] = []
    live = 0
    for body in bodies:
        path = cache_path(body, cache_dir)
        result = read_cached(path, body["questions"])
        if result is None and live < max_calls:
            result, live = call_live(body, transport, path), live + 1
        results.append(result)
    LOG.info("Jev requests: %d live, %d cached", live, sum(1 for r in results if r) - live)
    return results


# --- answers to labels -----------------------------------------------------------------
def ai_fields(answer: Mapping[str, Any] | None) -> tuple[bool | None, str | None]:
    """(AI is a reason, role). A reason if the AI_REASON_ROLES sum to >= MIN_CONFIDENCE; not one
    if mention_only alone reaches it; otherwise unresolved (None, None)."""
    if answer is None:
        return None, None
    probs = answer["probabilities"]
    if sum(probs.get(r, 0.0) for r in AI_REASON_ROLES) >= MIN_CONFIDENCE:
        return True, max(AI_REASON_ROLES, key=lambda r: probs.get(r, 0.0))
    if probs.get("mention_only", 0.0) >= MIN_CONFIDENCE:
        return False, "mention_only"
    return None, None


def view_label(view: View, answers: Mapping[str, Any]) -> tuple[bool | None, str | None]:
    """No text: unresolved. No AI term: not a reason, without asking Jev. Else Jev's answer."""
    if not view.has_text:
        return None, None
    if not view.ai_sentences:
        return False, "absent"
    return ai_fields(answers.get(f"{view.adsh}_ai"))


def listing_from_row(row: Mapping[str, Any]) -> sec.Listing:
    return sec.Listing(**{f.name: row[f.name] for f in fields(sec.Listing)})


def label_new(
    new: pl.DataFrame,
    fetch: sec.Fetcher,
    transport: Transport,
    cache_dir: Path,
    *,
    today: date,
    max_calls: int = MAX_CALLS,
) -> pl.DataFrame:
    """Label the SEC step's new filings with Jev; return FILINGS_SCHEMA rows.

    Raises CallCapExceeded, before returning anything, if more than `max_calls` live requests
    would be needed.
    """
    rows = new.to_dicts()
    views = [build_view(listing_from_row(r), fetch) for r in rows]
    units = [Unit(v.adsh, v.company, v.filed, v.ai_sentences) for v in views if v.ai_sentences]
    bodies = pack(units)
    results = run_batches(bodies, transport, cache_dir, max_calls)
    if skipped := results.count(None):
        raise CallCapExceeded(
            f"Jev call cap of {max_calls} reached: {skipped} of {len(bodies)} requests not sent"
        )
    answers = {k: a for r in results if r for k, a in r.answers.items()}
    out = []
    for r, v in zip(rows, views, strict=True):
        reason, role = view_label(v, answers)
        out.append(
            {
                "adsh": r["adsh"],
                "cik": r["cik"],
                "company": r["company"],
                "file_date": r["file_date"],
                "is_amendment": r["is_amendment"],
                "headcount": r["headcount"],
                "ai_reason": reason,
                "ai_role": role,
                "label_method": JEV_METHOD,
                "jev_model": MODEL,
                "labeled_at": today,
            }
        )
    return pl.DataFrame(out, schema=FILINGS_SCHEMA)
