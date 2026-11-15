"""SEC 8-K Item 2.05 (exit or disposal costs) filings: search, fetch and headcount extraction.

Ported from the paper's `research/build_sec_layoffs.py`. Lists filings through EDGAR full-text
search one calendar month at a time, fetches each main 8-K document, isolates the Item 2.05
section and extracts the number of affected employees (falling back to the first EX-99 press
release). Documents are cached on disk; search pages are not, so a rescan sees late filings.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import polars as pl

LOG = logging.getLogger(__name__)

SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data"

# --- request policy ----------------------------------------------------------------
MIN_INTERVAL_S = 0.2  # at most 5 requests per second
TIMEOUT_S = 60
MAX_TRIES = 5
BACKOFF_S = 2.0

# --- search scope ------------------------------------------------------------------
PHRASES = {"item205": '"Item 2.05"', "exitdisposal": '"Exit or Disposal Activities"'}
PAGE_SIZE = 100

# --- text extraction ---------------------------------------------------------------
SKIP_TAGS = {"script", "style", "head", "title", "ix:header"}
BLOCK_TAGS = {"p", "div", "br", "tr", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6"}
SECTION_MAX_CHARS = 15_000
SNIPPET_FALLBACK_CHARS, SNIPPET_MAX_CHARS = 400, 800
MIN_HEADCOUNT, MAX_HEADCOUNT = 10, 500_000

BLOCK_TAG_RE = re.compile(r"<(?:p|div|br|tr|table)\b", re.IGNORECASE)
ITEM_HEAD = re.compile(r"item\s*2\.05\b", re.IGNORECASE)
ITEM_LINE = re.compile(r"^\W*item\s*2\.05\b", re.IGNORECASE | re.MULTILINE)
SECTION_END = re.compile(
    r"^\W*item\s*(?!2\.05)\d\.\d\d\b|^\W*section\s+\d\b|^\W*signatures?\b"
    r"|pursuant to the requirements of the securities exchange act",
    re.IGNORECASE | re.MULTILINE,
)
SENT_SPLIT = re.compile(r"(?<=[a-z0-9)%\"”][.!?])\s+(?=[A-Z“\"(])|\n")
# A sentence is only searched for headcount if it talks about cutting or restructuring.
REDUCTION = re.compile(
    r"reduc|eliminat|lay\s?off|laid|terminat|sever|separat|\bcuts?\b|restructur|workforce"
    r"|headcount|redundan|downsiz|affect|impact|displac|notif|clos(?:e|ing|ure)|consolidat"
    r"|realign|streamlin|exit",
    re.IGNORECASE,
)
NUM = (
    r"(?<![\$\d,.])(\d{1,3}(?:,\d{3})+|\d{2,6})"
    r"(?![\d,]|\.\d|\s*(?:%|per\s?cent|million|billion|thousand))"
)
APPROX = r"(?:(?:approximately|about|roughly|nearly|up to|around|over|more than|as many as)\s+)*"
COUNT_FWD = re.compile(
    NUM + r"\s+(?:[\w.\-]+\s+){0,2}?(?:employees|positions|jobs|roles|workers|team members"
    r"|people|individuals|associates|persons|staff|full-time|FTEs?|headcount)\b",
    re.IGNORECASE,
)
COUNT_REV = re.compile(
    r"(?:(?:workforce|work force|headcount|staff)\s+by|(?:reductions?|layoffs?|cuts?)"
    r"\s+(?:in force\s+)?of)\s+" + APPROX + NUM,
    re.IGNORECASE,
)
# "had 4,000 employees", "from 10,000 people to": a total, not a number affected.
TOTAL_CTX = re.compile(
    r"(?:employs?|employed|employing|workforce of|work force of|headcount of|had|has|have"
    r"|totaling|total of|base of|from|out of)\s+" + APPROX + r"$",
    re.IGNORECASE,
)
_SMALL = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen"
_TEENS = "fifteen sixteen seventeen eighteen nineteen twenty"
WORD_NUMS = {w: i for i, w in enumerate(f"{_SMALL} {_TEENS}".split(), 1)} | {
    "twenty-five": 25,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
}
PCT_NUM = (
    r"(\d+(?:\.\d+)?|"
    + "|".join(sorted(WORD_NUMS, key=len, reverse=True))
    + r")\s*(?:%|per\s?cent)"
)
WORKFORCE = r"(?:workforce|work force|employees|headcount|staff|employee base|workers|employment)"
PCT_FWD = re.compile(
    PCT_NUM + r"\s+of\s+(?:[\w’'.\-]+\s+){0,4}?" + WORKFORCE + r"\b", re.IGNORECASE
)
PCT_REV = re.compile(
    WORKFORCE + r"(?:\s+(?:reduction|decrease|cut)s?)?\s+(?:by|of)\s+" + APPROX + PCT_NUM,
    re.IGNORECASE,
)
AI_TERMS = re.compile(r"(?i:artificial intelligence|automation|machine learning)|\bAI\b")
EX_SUFFIXES = (".htm", ".html", ".txt")
EX99_NAME = re.compile(r"ex(?:hibit)?[-_ ]?99", re.IGNORECASE)

FILINGS_RAW_SCHEMA: dict[str, Any] = {
    "adsh": pl.Utf8,
    "cik": pl.Utf8,
    "company": pl.Utf8,
    "file_date": pl.Date,
    "form": pl.Utf8,
    "is_amendment": pl.Boolean,
    "items": pl.List(pl.Utf8),
    "hq_state": pl.Utf8,
    "inc_state": pl.Utf8,
    "sic": pl.Utf8,
    "url": pl.Utf8,
    "main_doc": pl.Utf8,
    "headcount": pl.Int64,
    "headcount_pct": pl.Float64,
    "headcount_source": pl.Utf8,
    "ai_mentioned": pl.Boolean,
    "snippet": pl.Utf8,
}


@dataclass(frozen=True)
class Listing:
    """One Item 2.05 filing as listed by EDGAR full-text search."""

    adsh: str
    cik: str
    company: str
    file_date: date
    form: str
    is_amendment: bool
    items: list[str]
    hq_state: str | None
    inc_state: str | None
    sic: str | None
    url: str
    main_doc: str


class Fetcher:
    """Throttled, retrying HTTP GET for SEC endpoints, with an optional disk cache per call."""

    def __init__(self, cache_dir: Path, user_agent: str) -> None:
        if not user_agent.strip():
            # www.sec.gov returns 403 ("Undeclared Automated Tool") without a contact.
            raise ValueError("SEC_USER_AGENT is not set: use 'Your Name your@email'")
        self.cache_dir = cache_dir
        self.user_agent = user_agent
        self.requests = 0
        self._next_slot = 0.0
        self._blocked = ""  # set after the first 403 so later calls stop hitting SEC

    def _wait_turn(self) -> None:
        now = time.monotonic()
        slot = max(now, self._next_slot)
        self._next_slot = slot + MIN_INTERVAL_S
        self.requests += 1
        time.sleep(slot - now)

    def _download(self, url: str) -> bytes | None:
        """GET with retries on 429/5xx/timeouts. Returns None on 404."""
        for attempt in range(MAX_TRIES):
            if self._blocked:
                raise RuntimeError(self._blocked)
            self._wait_turn()
            reason = "network error"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
                with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                    body: bytes = resp.read()
                    return body
            except urllib.error.HTTPError as err:
                if err.code == 404:
                    return None
                if err.code == 403:
                    self._blocked = (
                        f"403 from {url}: SEC rejects this User-Agent. Set SEC_USER_AGENT to "
                        "'Your Name your@email' (SEC fair-access policy) and rerun."
                    )
                    raise RuntimeError(self._blocked) from err
                if err.code != 429 and err.code < 500:
                    raise
                reason = f"HTTP {err.code}"
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                pass
            LOG.warning("%s, retry %d for %s", reason, attempt + 1, url)
            time.sleep(BACKOFF_S * 2**attempt)
        raise RuntimeError(f"giving up on {url}")

    def get(self, url: str, cache: Path | None) -> str | None:
        """Return the response text, from `cache` if it exists; `None` cache means always fetch."""
        if cache is not None and cache.exists():
            return cache.read_text(encoding="utf-8", errors="replace")
        body = self._download(url)
        if body is None:
            return None
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_name(cache.name + ".part")
            tmp.write_bytes(body)
            tmp.replace(cache)
        return body.decode("utf-8", errors="replace")


# --- step 1: list filings ----------------------------------------------------------
def search_month(
    fetch: Fetcher, slug: str, phrase: str, start: date, end: date
) -> list[dict[str, Any]]:
    """Return every full-text-search hit for `phrase` filed in [start, end], paging by 100.

    `slug` names the phrase in log lines. Raises if SEC reports a truncated total.
    """
    hits: list[dict[str, Any]] = []
    while True:
        query = {
            "q": phrase,
            "forms": "8-K",
            "dateRange": "custom",
            "startdt": start.isoformat(),
            "enddt": end.isoformat(),
            "from": len(hits),
        }
        text = fetch.get(f"{SEARCH_URL}?{urllib.parse.urlencode(query)}", None)
        if text is None:
            raise RuntimeError(f"search page missing: {query}")
        data = json.loads(text)["hits"]
        if data["total"]["relation"] != "eq":
            raise RuntimeError(f"search truncated ({slug} {start:%Y-%m}): {query}")
        hits += data["hits"]
        if not data["hits"] or len(hits) >= data["total"]["value"]:
            LOG.info("%s %s: %d hits", slug, f"{start:%Y-%m}", len(hits))
            return hits


def archive_url(cik: str, adsh: str, name: str) -> str:
    """Return the EDGAR archive URL of one file inside a filing."""
    return f"{ARCHIVE_URL}/{int(cik)}/{adsh.replace('-', '')}/{name}"


def to_listing(hit: dict[str, Any]) -> Listing:
    """Convert one search hit (main 8-K document) into a Listing."""
    s = hit["_source"]

    def first(key: str) -> str | None:
        vals = s.get(key) or [None]
        return vals[0]  # type: ignore[no-any-return]

    name = hit["_id"].split(":", 1)[1]
    return Listing(
        adsh=s["adsh"],
        cik=s["ciks"][0],
        company=re.split(r"\s{2,}\(", s["display_names"][0])[0],
        file_date=date.fromisoformat(s["file_date"]),
        form=s["form"],
        is_amendment=s["form"] == "8-K/A",
        items=s["items"],
        hq_state=first("biz_states"),
        inc_state=first("inc_states"),
        sic=first("sics"),
        url=archive_url(s["ciks"][0], s["adsh"], name),
        main_doc=name,
    )


def list_filings(fetch: Fetcher, months: Sequence[tuple[date, date]]) -> list[Listing]:
    """Search both phrases for each month; keep one main-document hit per Item 2.05 filing."""
    hits = [
        h
        for start, end in months
        for slug, phrase in PHRASES.items()
        for h in search_month(fetch, slug, phrase, start, end)
    ]
    main: dict[str, dict[str, Any]] = {}
    for h in sorted(hits, key=lambda h: str(h["_id"])):
        s = h["_source"]
        if "2.05" in (s.get("items") or []) and s["file_type"].startswith("8-K"):
            main.setdefault(s["adsh"], h)
    return sorted((to_listing(h) for h in main.values()), key=lambda f: (f.file_date, f.adsh))


# --- step 2: text extraction -------------------------------------------------------
class _TextParser(HTMLParser):
    """Collect visible text, with newlines at block elements."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self._skip += 1
        self.parts.append("\n" if tag in BLOCK_TAGS else " " if tag in ("td", "th") else "")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        self.parts.append("\n" if tag in BLOCK_TAGS else "")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data.replace("\n", " "))


def html_to_text(raw: str) -> str:
    """Strip HTML to text, one paragraph per line, whitespace collapsed."""
    if BLOCK_TAG_RE.search(raw):
        parser = _TextParser()
        parser.feed(raw)
        parts = parser.parts
    else:  # plain-text filing: single newlines are hard wraps, blank lines end paragraphs
        raw = re.sub(r"<[^>]+>", "", raw)
        raw = re.sub(
            r"\n(?=[ \t]*(?:item\s*\d\.\d\d|signatures?\b))", "\n\n", raw, flags=re.IGNORECASE
        )
        raw = re.sub(r"(?<!\n)\n(?!\n)", " ", raw)
        parts = [raw.replace("\n\n", "\n")]
    text = "".join(parts).replace("\xa0", " ").replace("​", "")
    lines = (re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in text.split("\n"))
    return "\n".join(ln for ln in lines if ln)


def item_205_section(text: str) -> str:
    """Return the Item 2.05 section: heading up to the next Item heading or signature block."""
    heads = list(ITEM_LINE.finditer(text)) or list(ITEM_HEAD.finditer(text))
    if not heads:
        return ""
    start = next(
        (
            m
            for m in heads
            if re.search(r"cost|exit|disposal", text[m.end() : m.end() + 100], re.IGNORECASE)
        ),
        heads[0],
    )
    end = SECTION_END.search(text, start.end())
    return text[start.start() : end.start() if end else None][:SECTION_MAX_CHARS]


def first_count(sentence: str) -> int | None:
    """Return the first plausible 'N employees/positions/...' count in a sentence."""
    found = sorted(
        (m for rx in (COUNT_FWD, COUNT_REV) for m in rx.finditer(sentence)),
        key=lambda m: m.start(),
    )
    for m in found:
        n = int(m.group(1).replace(",", ""))
        before = sentence[max(0, m.start(1) - 45) : m.start(1)]
        if MIN_HEADCOUNT <= n <= MAX_HEADCOUNT and not TOTAL_CTX.search(before):
            return n
    return None


def first_pct(sentence: str) -> float | None:
    """Return the first 'X% of the workforce' percentage in a sentence (0 < X <= 100)."""
    for rx in (PCT_FWD, PCT_REV):
        for m in rx.finditer(sentence):
            tok = m.group(1).lower()
            if 0 < (pct := float(WORD_NUMS.get(tok, tok))) <= 100:
                return pct
    return None


def find_headcount(text: str) -> tuple[int | None, float | None, str]:
    """Return (headcount, pct of workforce, source sentences) from the first matching sentences."""
    count: int | None = None
    pct: float | None = None
    used: list[str] = []
    for sent in (s.strip() for s in SENT_SPLIT.split(text)):
        if not REDUCTION.search(sent):
            continue
        n = first_count(sent) if count is None else None
        p = first_pct(sent) if pct is None else None
        if n is not None or p is not None:
            count = count if n is None else n
            pct = pct if p is None else p
            used.append(sent)
        if count is not None and pct is not None:
            break
    return count, pct, " ".join(used)[:SNIPPET_MAX_CHARS]


def ex99_text(fetch: Fetcher, f: Listing) -> str:
    """Return text of the first EX-99 exhibit in the filing ('' if none can be found)."""
    index = fetch.get(
        archive_url(f.cik, f.adsh, "index.json"), fetch.cache_dir / "index" / f"{f.adsh}.json"
    )
    if index is None:
        return ""
    names = sorted(i["name"] for i in json.loads(index)["directory"]["item"])
    name = next((n for n in names if EX99_NAME.search(n) and n.lower().endswith(EX_SUFFIXES)), None)
    if name is None:
        return ""
    raw = fetch.get(archive_url(f.cik, f.adsh, name), fetch.cache_dir / "docs" / f"{f.adsh}_{name}")
    return html_to_text(raw) if raw else ""


def main_doc_text(fetch: Fetcher, f: Listing) -> str | None:
    """Return the main 8-K document as text, or None if SEC has no such file."""
    raw = fetch.get(f.url, fetch.cache_dir / "docs" / f"{f.adsh}_{f.main_doc}")
    return None if raw is None else html_to_text(raw)


def extract(fetch: Fetcher, f: Listing) -> dict[str, object]:
    """Return headcount, pct, source, AI flag and snippet for one filing."""
    text = main_doc_text(fetch, f)
    section = item_205_section(text) if text else ""
    count, pct, snippet = find_headcount(section)
    source = None if (count, pct) == (None, None) else "item_2_05"
    scope = section
    if count is None:  # no headcount in the 8-K item itself: try the EX-99 press release
        n, p, snip = find_headcount(ex99_text(fetch, f))
        if n is not None or (pct is None and p is not None):
            count, pct, source = n, (pct if pct is not None else p), "ex99"
            snippet, scope = " | ".join(x for x in (snippet, snip) if x), f"{section} {snip}"
    return {
        "headcount": count,
        "headcount_pct": pct,
        "headcount_source": source,
        "ai_mentioned": None if text is None else bool(AI_TERMS.search(scope)),
        "snippet": snippet or section[:SNIPPET_FALLBACK_CHARS],
    }


def fetch_month_filings(fetch: Fetcher, months: Sequence[tuple[date, date]]) -> pl.DataFrame:
    """List the months' Item 2.05 filings, extract their text features, return one row each."""
    # ponytail: sequential fetching; a month is ~25 filings at 5 req/s. Thread it if that grows.
    filings = list_filings(fetch, months)
    rows = [{**asdict(f), **extract(fetch, f)} for f in filings]
    return pl.DataFrame(rows, schema=FILINGS_RAW_SCHEMA)


# (sentence, expected (headcount, pct)): regex regression cases, run as tests.
SELF_CHECKS: list[tuple[str, tuple[int | None, float | None]]] = [
    ("we expect to reduce our current workforce by more than 40%.", (None, 40.0)),
    ("a global workforce reduction of up to approximately 10%.", (None, 10.0)),
    ("cut 225 positions, or roughly 15 percent of its workforce.", (225, 15.0)),
    ("a lay off of approximately 225 employees.", (225, None)),
    ("the elimination of approximately 2% of Workday's current workforce.", (None, 2.0)),
    ("reduce worldwide employment by approximately seven percent.", (None, 7.0)),
    ("laying off approximately 1,200 people, about 6% of its global workforce.", (1200, 6.0)),
    ("The Company had approximately 4,000 employees and will reduce headcount.", (None, None)),
    (
        "We're reducing Block by nearly half, from over 10,000 people to just under 6,000, which "
        "means that over 4,000 people are being asked to leave.",
        (4000, None),
    ),
    ("It will incur $25 million related employees severance for restructuring.", (None, None)),
]
