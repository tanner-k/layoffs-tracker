"""The AI-path checks of the paper's `verify_sec_jev2 --self-test`, as tests."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from layoffs_tracker.jev.api import (
    MAX_QUESTIONS,
    MODEL,
    HttpTransport,
    check_request,
    http_transport,
    validate_response,
)
from layoffs_tracker.jev.label import AI_ROLES, Unit, ai_fields, label_new, pack
from tests.fakes import FakeTransport, ai_answer
from tests.test_label import TODAY, new_filings

AI_Q = {"type": "choice", "instructions": "x", "criteria": AI_ROLES}


def test_ai_two_level() -> None:
    # Block in the pilot: probability split across subtypes, but AI is clearly a reason.
    block = ai_answer(
        ai_does_work=0.62, reinvest_in_ai=0.12, ai_strategy_framing=0.15, mention_only=0.10
    )
    assert ai_fields(block) == (True, "ai_does_work")
    assert ai_fields(ai_answer(mention_only=0.98, ai_strategy_framing=0.02)) == (
        False,
        "mention_only",
    )
    cargurus = ai_answer(mention_only=0.50, ai_strategy_framing=0.44, ai_does_work=0.06)
    assert ai_fields(cargurus) == (None, None)
    assert ai_fields(None) == (None, None)


def test_ai_reason_at_exactly_the_threshold_is_accepted() -> None:
    assert ai_fields(ai_answer(ai_does_work=0.7, mention_only=0.3))[0] is True


def payload(answers: dict[str, Any]) -> dict[str, Any]:
    return {"model": MODEL, "answers": answers, "usage": {"input_tokens": 1, "output_tokens": 1}}


def test_validate_response_rejects_bad_payloads() -> None:
    qs = {"a_ai": AI_Q}
    good = ai_answer(ai_does_work=0.9, unknown=0.1)
    assert validate_response(payload({"a_ai": good}), qs)["a_ai"]["choice"] == "ai_does_work"
    for bad in (
        {**payload({"a_ai": good}), "model": "jev-0"},
        payload({}),
        payload({"a_ai": {**good, "confidence": 7}}),
        payload({"a_ai": {**good, "probabilities": {"ai_does_work": 1.0}}}),
        {**payload({"a_ai": good}), "usage": {"input_tokens": -1, "output_tokens": 0}},
    ):
        with pytest.raises(ValueError):
            validate_response(bad, qs)


def test_validate_response_uses_argmax_when_choice_is_not_it() -> None:
    off = {**ai_answer(ai_does_work=0.8, mention_only=0.2), "choice": "mention_only"}
    fixed = validate_response(payload({"a_ai": off}), {"a_ai": AI_Q})["a_ai"]
    assert fixed["choice"] == "ai_does_work" and fixed["normalized_from"] == "mention_only"


def test_pack_keeps_at_most_32_questions_and_loses_none() -> None:
    sents = ({"id": "a0", "source": "item_2_05", "text": "AI will do the work."},)
    units = [Unit(f"f{i}", "Co", "2026-10-01", sents) for i in range(70)]
    bodies = pack(units)
    assert len(bodies) == 3 and all(len(b["questions"]) <= MAX_QUESTIONS for b in bodies)
    asked = [k for b in bodies for k in b["questions"]]
    assert sorted(asked) == sorted(f"f{i}_ai" for i in range(70))
    item = bodies[0]["state"]["items"]["f0"]
    assert item["ai_sentences"] == list(sents) and "sentences" not in item
    for b in bodies:
        check_request(b)


def test_check_request_rejects_empty_and_too_many() -> None:
    with pytest.raises(ValueError, match="1 to 32"):
        check_request({"questions": {}})
    many = {f"q{i}": AI_Q for i in range(MAX_QUESTIONS + 1)}
    with pytest.raises(ValueError, match="1 to 32"):
        check_request({"questions": many})
    with pytest.raises(ValueError, match="Unsupported"):
        check_request({"questions": {"q": {"type": "free_text"}}})


def test_malformed_response_raises_and_caches_nothing(tmp_path: Path) -> None:
    new, fake = new_filings(tmp_path)

    class Broken(FakeTransport):
        def __call__(self, body: dict[str, Any]) -> Any:
            p = super().__call__(body)
            first = next(iter(p["answers"]))
            p["answers"][first] = {**p["answers"][first], "confidence": 7}
            return p

    with pytest.raises(ValueError):
        label_new(new, fake, Broken(), tmp_path / "jev", today=TODAY)
    assert not list((tmp_path / "jev").glob("*.json"))


def test_cache_revalidates_and_holds_no_key(tmp_path: Path) -> None:
    new, fake = new_filings(tmp_path)
    label_new(new, fake, FakeTransport(), tmp_path / "jev", today=TODAY)
    files = list((tmp_path / "jev").glob("*.json"))
    assert files
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        validate_response(data["response"], data["request"]["questions"])
        assert "Bearer" not in f.read_text(encoding="utf-8")


class _Resp(io.BytesIO):
    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _err(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.typesafe.ai/v1/systemone",
        code,
        "err",
        {},  # type: ignore[arg-type]
        io.BytesIO(body),
    )


def test_http_transport_sends_bearer_and_retries_429() -> None:
    seen: list[urllib.request.Request] = []
    replies: list[Any] = [_err(429), _Resp(b'{"ok": true}')]

    def opener(req: urllib.request.Request, timeout: float) -> _Resp:
        seen.append(req)
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r  # type: ignore[no-any-return]

    t = HttpTransport("k-secret", opener=opener, sleep=lambda s: None)
    assert t({"questions": {}}) == {"ok": True}
    assert len(seen) == 2 and seen[0].get_header("Authorization") == "Bearer k-secret"
    assert "k-secret" not in repr(t)


def test_http_transport_redacts_key_in_errors() -> None:
    def opener(req: urllib.request.Request, timeout: float) -> _Resp:
        raise _err(400, b"bad token k-secret")

    t = HttpTransport("k-secret", opener=opener, sleep=lambda s: None)
    with pytest.raises(RuntimeError, match="HTTP 400") as exc:
        t({"questions": {}})
    assert "k-secret" not in str(exc.value)


def test_http_transport_gives_up_after_retries() -> None:
    def opener(req: urllib.request.Request, timeout: float) -> _Resp:
        raise _err(503)

    with pytest.raises(RuntimeError, match="after retries"):
        HttpTransport("k", opener=opener, sleep=lambda s: None)({"questions": {}})


def test_http_transport_factory() -> None:
    assert isinstance(http_transport("k"), HttpTransport)
