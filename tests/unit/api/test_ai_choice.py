"""Tests for emby_dedupe.api.ai_choice — the Claude + Jev cross-check."""
from __future__ import annotations

import pytest

from emby_dedupe.api.ai_choice import (
    AiChoiceError,
    ChoiceAnswer,
    CrossCheckClient,
    combine,
)

CRITERIA: dict[str, str | None] = {"c1": "Film A (2020), film", "none": "None of them."}


class _Backend:
    """Replays answers (ChoiceAnswer or exception) and records how often it was asked."""

    def __init__(self, *answers, label="fake"):
        self.answers, self.calls, self.closed, self.label = list(answers), 0, False, label

    def choose(self, state, instructions, criteria):
        self.calls += 1
        nxt = self.answers.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def close(self):
        self.closed = True


# --- combine -------------------------------------------------------------------

def test_agreement_keeps_claudes_confidence_and_notes_jevs():
    got = combine(ChoiceAnswer("c1", 1.0), ChoiceAnswer("c1", 0.92))
    assert (got.choice, got.confidence, got.note) == ("c1", 1.0, "both-agree, jev 0.92")


def test_claude_pick_jev_none_is_claude_only():
    """Regression 2026-10-08 (logs/haiku-poc): Claude picked a different French series for
    *Israel and Palestine - Birth of a Conflict* 5/5; Jev said none. Such a pick must stay
    visibly unbacked."""
    got = combine(ChoiceAnswer("c1", 1.0), ChoiceAnswer("none", 0.64))
    assert got.choice == "c1" and got.note == "claude-only, jev 0.64 for none"


def test_jev_pick_below_review_floor_does_not_count_as_agreement():
    got = combine(ChoiceAnswer("c1", 0.8), ChoiceAnswer("c1", 0.3))
    assert got.note.startswith("claude-only")


def test_different_picks_are_a_conflict_led_by_claude():
    got = combine(ChoiceAnswer("c1", 0.8), ChoiceAnswer("c2", 0.9))
    assert got.choice == "c1" and got.note == "conflict, jev picked c2 (0.90)"


def test_jev_pick_used_when_claude_has_none():
    got = combine(ChoiceAnswer("none", 1.0), ChoiceAnswer("c1", 0.7))
    assert (got.choice, got.confidence, got.note) == ("c1", 0.7, "jev-only")


def test_both_none_is_none_without_note():
    got = combine(ChoiceAnswer("none", 1.0), ChoiceAnswer("none", 0.9))
    assert got.choice == "none" and got.note == ""


def test_one_failed_backend_leaves_the_other_alone():
    claude = combine(ChoiceAnswer("c1", 0.8), None)
    jev = combine(None, ChoiceAnswer("c1", 0.95))
    assert claude.note == "claude-only (other backend failed)"
    assert jev.note == "jev-only (other backend failed)"
    assert combine(None, ChoiceAnswer("none", 0.9)).note == ""


# --- CrossCheckClient -------------------------------------------------------------

def test_both_asked_with_the_same_question():
    claude, jev = _Backend(ChoiceAnswer("c1", 1.0)), _Backend(ChoiceAnswer("c1", 0.9))
    got = CrossCheckClient(claude, jev).choose({"title": "A"}, "which?", CRITERIA)
    assert got.note.startswith("both-agree") and claude.calls == jev.calls == 1


def test_a_rejected_key_drops_that_backend_for_the_run():
    claude = _Backend(AiChoiceError("bad key", fatal=True))
    jev = _Backend(ChoiceAnswer("c1", 0.9), ChoiceAnswer("c1", 0.95))
    client = CrossCheckClient(claude, jev)
    assert client.choose({}, "q", CRITERIA).note == "jev-only (other backend failed)"
    assert client.choose({}, "q", CRITERIA).confidence == 0.95
    assert claude.calls == 1  # not asked again


def test_a_one_off_failure_does_not_drop_the_backend():
    claude = _Backend(AiChoiceError("overloaded"), ChoiceAnswer("c1", 1.0))
    jev = _Backend(ChoiceAnswer("c1", 0.9), ChoiceAnswer("c1", 0.9))
    client = CrossCheckClient(claude, jev)
    client.choose({}, "q", CRITERIA)
    assert client.choose({}, "q", CRITERIA).note.startswith("both-agree")


def test_both_failing_raises_and_is_fatal_only_when_both_keys_are_rejected():
    flaky = CrossCheckClient(_Backend(AiChoiceError("down")), _Backend(AiChoiceError("down")))
    with pytest.raises(AiChoiceError) as exc:
        flaky.choose({}, "q", CRITERIA)
    assert not exc.value.fatal
    dead = CrossCheckClient(_Backend(AiChoiceError("key", fatal=True)), _Backend(AiChoiceError("key", fatal=True)))
    with pytest.raises(AiChoiceError) as exc:
        dead.choose({}, "q", CRITERIA)
    assert exc.value.fatal


def test_close_closes_both_even_after_one_was_dropped():
    claude, jev = _Backend(AiChoiceError("key", fatal=True), label="haiku"), _Backend(ChoiceAnswer("c1", 0.9), label="jev")
    client = CrossCheckClient(claude, jev)
    client.choose({}, "q", CRITERIA)
    client.close()
    assert claude.closed and jev.closed
    assert client.label == "haiku checked by jev"


class _Log:
    """Stand-in for the module logger: keeps every message."""

    def __init__(self):
        self.lines: list[str] = []

    def __getattr__(self, level):
        return lambda msg: self.lines.append(f"{level}: {msg}")


def test_failures_are_counted_and_logged_per_backend(monkeypatch):
    """A dead Claude key must not hide behind Jev still answering (the Sunday job fails silently)."""
    from emby_dedupe.api import ai_choice

    log = _Log()
    monkeypatch.setattr(ai_choice, "logger", log)
    claude = _Backend(AiChoiceError("overloaded"), AiChoiceError("bad key", fatal=True))
    jev = _Backend(*[ChoiceAnswer("c1", 0.9)] * 3)
    client = CrossCheckClient(claude, jev)
    for _ in range(3):
        client.choose({}, "q", CRITERIA)
    assert client.failures == {"Claude": 2}
    assert log.lines == ["warning: Claude failed on this item: overloaded",
                         "error: Claude dropped for the rest of this run: bad key"]
