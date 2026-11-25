"""Bounded, pinned TypeSafe Jev transport and response validation (stdlib only).

Ported from the paper's `research/jev_api.py`. The API key lives only in HttpTransport and the
environment; it is never printed, logged or written to disk.
"""

from __future__ import annotations

import http.client
import json
import logging
import math
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

LOG = logging.getLogger(__name__)

MODEL = "jev-1.13.0"
ENDPOINT = "https://api.typesafe.ai/v1/systemone"
KEY_VAR = "TYPESAFE_API_KEY"
MAX_QUESTIONS, MAX_BODY_BYTES = 32, 500_000
MAX_CHOICES = 255
MAX_RESPONSE_BYTES, TIMEOUT_S = 1_000_000, 25
MAX_TRIES, BACKOFF_S, DEFAULT_RPS = 5, 2.0, 2.0
PROB_TOL = 0.02

Transport = Callable[[dict[str, Any]], Any]  # request body -> parsed JSON response


def encode(body: Mapping[str, Any]) -> bytes:
    """The exact bytes sent (and size-checked) for a request body."""
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _probability(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def _well_formed(answer: Mapping[str, Any], criteria: Mapping[str, Any]) -> bool:
    """Probabilities cover exactly the criteria, sum to 1, and the choice is one of them."""
    probs, choice = answer.get("probabilities"), answer.get("choice")
    return (
        isinstance(probs, dict)
        and set(probs) == set(criteria)
        and all(_probability(p) for p in probs.values())
        and math.isclose(sum(probs.values()), 1, abs_tol=PROB_TOL)
        and isinstance(choice, str)
        and choice in probs
        and _probability(answer.get("confidence"))
    )


def _valid_choice(answer: Mapping[str, Any], criteria: Mapping[str, Any]) -> bool:
    if not _well_formed(answer, criteria):
        return False
    probs = answer["probabilities"]
    return bool(probs[answer["choice"]] >= max(probs.values()))


def _argmax_fix(answer: Mapping[str, Any], criteria: Mapping[str, Any]) -> dict[str, Any] | None:
    """Jev sometimes names a choice that is not its own most probable option (seen once in ~1,100
    requests). When the probabilities are otherwise well formed, trust them: return the answer
    with the argmax as the choice. Return None if anything else is wrong."""
    if not _well_formed(answer, criteria):
        return None
    probs = answer["probabilities"]
    top = max(probs, key=probs.__getitem__)
    return {**answer, "choice": top, "confidence": probs[top], "normalized_from": answer["choice"]}


def validate_response(payload: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a Jev response; a choice that is not the argmax of valid probabilities is fixed."""
    if not isinstance(payload, dict) or payload.get("model") != MODEL:
        raise ValueError("Invalid Jev model or response")
    answers, usage = payload.get("answers"), payload.get("usage")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise ValueError("Invalid Jev answer set")
    if not isinstance(usage, dict) or any(
        type(usage.get(k)) is not int or usage[k] < 0 for k in ("input_tokens", "output_tokens")
    ):
        raise ValueError("Invalid Jev usage")
    out = dict(answers)
    for key, question in questions.items():
        answer = answers[key]
        if not isinstance(answer, dict) or answer.get("type") != question["type"]:
            raise ValueError("Invalid Jev answer type")
        if question["type"] == "noul":
            if not _probability(answer.get("noul")):
                raise ValueError("Invalid Jev probability")
        elif not _valid_choice(answer, question["criteria"]):
            fixed = _argmax_fix(answer, question["criteria"])
            if fixed is None:
                raise ValueError(f"Invalid Jev choice for {key}: {answer.get('choice')!r}")
            LOG.warning(
                "Jev choice for %s was %r, not its argmax; using %r",
                key,
                answer.get("choice"),
                fixed["choice"],
            )
            out[key] = fixed
    return out


def check_request(body: Mapping[str, Any]) -> None:
    """Reject a request Jev would refuse: bad question shape, > 32 questions, >= 500 KB."""
    questions = body["questions"]
    if not 0 < len(questions) <= MAX_QUESTIONS:
        raise ValueError("Jev takes 1 to 32 questions per request")
    for q in questions.values():
        if not isinstance(q, dict) or q.get("type") not in {"choice", "noul"}:
            raise ValueError("Unsupported Jev question")
        criteria = q.get("criteria")
        if q["type"] == "choice" and not (
            isinstance(criteria, dict) and 1 <= len(criteria) <= MAX_CHOICES
        ):
            raise ValueError("Invalid Jev choices")
    if len(encode(body)) >= MAX_BODY_BYTES:
        raise ValueError("Jev request budget exceeded (500 KB)")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: the Authorization header must only go to ENDPOINT."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class HttpTransport:
    """Stdlib POST to the Jev endpoint: throttled, retrying on 429/5xx, response capped at 1 MB."""

    def __init__(
        self,
        api_key: str,
        rps: float = DEFAULT_RPS,
        opener: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._key = api_key  # never logged or written
        self._interval = 1.0 / rps
        self._next = 0.0
        self._sleep = sleep
        no_proxy = urllib.request.ProxyHandler({})
        self._open = opener or urllib.request.build_opener(no_proxy, _NoRedirect).open

    def __repr__(self) -> str:
        return "HttpTransport(key hidden)"

    def _wait_turn(self) -> None:
        now = time.monotonic()
        if now < self._next:
            self._sleep(self._next - now)
        self._next = max(now, self._next) + self._interval

    def _post(self, data: bytes) -> Any:
        req = urllib.request.Request(
            ENDPOINT,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "layoffs-tracker/1.0",
            },
        )
        with self._open(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("Jev response exceeds size limit")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Invalid Jev response JSON") from None

    def _fail(self, err: urllib.error.HTTPError) -> RuntimeError:
        try:
            detail = err.read(300).decode("utf-8", "replace").replace(self._key, "***")
        except (OSError, AttributeError, ValueError):
            detail = ""
        return RuntimeError(f"Jev request returned HTTP {err.code} {detail}".strip())

    def __call__(self, body: dict[str, Any]) -> Any:
        data = encode(body)
        for attempt in range(MAX_TRIES):
            self._wait_turn()
            try:
                return self._post(data)
            except urllib.error.HTTPError as err:
                if err.code != 429 and err.code < 500:
                    raise self._fail(err) from None
                reason = f"HTTP {err.code}"
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                http.client.HTTPException,
            ):
                reason = "network error"
            LOG.warning("Jev %s, retry %d", reason, attempt + 1)
            self._sleep(BACKOFF_S * 2**attempt)
        raise RuntimeError("Jev request failed after retries; rerun to resume from the cache")


def http_transport(api_key: str) -> Transport:
    """The live Jev transport."""
    return HttpTransport(api_key)


def load_api_key() -> str:
    """Return TYPESAFE_API_KEY from the environment (never printed)."""
    key = os.environ.get(KEY_VAR, "").strip()
    if not key:
        raise ValueError(f"{KEY_VAR} is not set")
    return key
