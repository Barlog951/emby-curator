"""Tests for emby_dedupe.api.claude_choice — Claude Haiku as a choose() backend (fake SDK)."""
from __future__ import annotations

import json
from threading import Lock
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from emby_dedupe.api import claude_choice
from emby_dedupe.api.claude_choice import (
    CLAUDE_EFFORT,
    DEFAULT_CLAUDE_MODEL,
    ClaudeChoiceClient,
    ClaudeChoiceError,
    answer_schema,
    render_prompt,
    tally,
)

CRITERIA: dict[str, str | None] = {"c1": "Hrabě (2023), film; country: Čile", "none": "None of them."}
STATE = {"title": "The Count", "year": 2023, "type": "Movie", "folder_name": "El Conde (2023)"}
_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _reply(choice: str | None = "c1", stop: str = "end_turn") -> SimpleNamespace:
    text = json.dumps({"reason": "same film", "choice": choice}) if choice is not None else "not json"
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="thinking", thinking=""),
                                                      SimpleNamespace(type="text", text=text)])


class _Sdk:
    """Fake anthropic.Anthropic: replays replies (or raises exceptions), records request kwargs."""

    def __init__(self, *replies):
        self.replies, self.requests, self.closed, self._lock = list(replies), [], False, Lock()
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        with self._lock:
            self.requests.append(kwargs)
            nxt = self.replies.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def close(self):
        self.closed = True


def _client(*replies, votes=5):
    sdk = _Sdk(*replies)
    return ClaudeChoiceClient("unused", votes=votes, sdk=sdk), sdk


# --- pure helpers --------------------------------------------------------------------

def test_tally_majority_and_share_of_votes_asked():
    got = tally(["c1", "c1", "c1", "none", None], requested=5)  # one vote failed
    assert (got.choice, got.confidence) == ("c1", 0.6)
    assert got.probabilities == {"c1": 0.6, "none": 0.2}


def test_tally_tie_goes_to_none():
    assert tally(["c1", "c1", "none", "none", None], 5).choice == "none"
    two_films = tally(["c1", "c2"], 2)
    assert (two_films.choice, two_films.confidence) == ("none", 0.0)
    assert tally([None, None], 2).choice == "none"


def test_prompt_and_schema():
    prompt = render_prompt(STATE, "Which is it?", CRITERIA)
    assert prompt.startswith("Which is it?") and '"folder_name": "El Conde (2023)"' in prompt
    assert "- c1: Hrabě (2023), film; country: Čile" in prompt and "- none: None of them." in prompt
    schema = answer_schema(list(CRITERIA))
    assert schema["properties"]["choice"]["enum"] == ["c1", "none"]
    assert schema["required"] == ["reason", "choice"] and schema["additionalProperties"] is False


# --- ClaudeChoiceClient ----------------------------------------------------------------

def test_request_shape_matches_what_was_measured():
    client, sdk = _client(*[_reply()] * 5)
    got = client.choose(STATE, "Which?", CRITERIA)
    assert (got.choice, got.confidence) == ("c1", 1.0) and len(sdk.requests) == 5
    req = sdk.requests[0]
    assert req["model"] == DEFAULT_CLAUDE_MODEL == "claude-haiku-5-5"
    assert req["output_config"]["effort"] == CLAUDE_EFFORT == "medium"
    assert req["output_config"]["format"]["schema"]["properties"]["choice"]["enum"] == ["c1", "none"]
    # Haiku 5.5 rejects non-default sampling parameters and assistant prefill with a 400
    assert not {"temperature", "top_p", "top_k", "thinking"} & set(req)
    assert [m["role"] for m in req["messages"]] == ["user"]
    assert req["max_tokens"] >= 2000  # thinking counts toward max_tokens


def test_refusal_or_unparseable_reply_is_no_vote():
    client, _ = _client(_reply(), _reply(), _reply(stop="refusal"), _reply(stop="max_tokens"), _reply(None))
    got = client.choose(STATE, "Which?", CRITERIA)
    assert (got.choice, got.confidence) == ("c1", 0.4)  # 2 of 5 asked


def test_some_failed_votes_lower_the_share():
    err = anthropic.APIConnectionError(request=_REQUEST)
    client, _ = _client(_reply(), _reply(), _reply(), err, err)
    assert client.choose(STATE, "Which?", CRITERIA).confidence == 0.6


def test_all_votes_failing_raises_non_fatal():
    err = anthropic.APIConnectionError(request=_REQUEST)
    client, _ = _client(err, err, votes=2)
    with pytest.raises(ClaudeChoiceError) as exc:
        client.choose(STATE, "Which?", CRITERIA)
    assert not exc.value.fatal


def test_rejected_key_is_fatal():
    err = anthropic.AuthenticationError("invalid x-api-key", response=httpx2.Response(401, request=_REQUEST), body=None)
    client, _ = _client(err, err, votes=2)
    with pytest.raises(ClaudeChoiceError) as exc:
        client.choose(STATE, "Which?", CRITERIA)
    assert exc.value.fatal


def test_label_and_close():
    client, sdk = _client(votes=3)
    assert client.label == "claude-haiku-5-5 x3"
    client.close()
    assert sdk.closed


def test_missing_sdk_gives_an_install_hint(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_anthropic(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("No module named 'anthropic'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_anthropic)
    with pytest.raises(ClaudeChoiceError, match=r"emby-curator\[claude\]") as exc:
        claude_choice._new_sdk_client("key")
    assert exc.value.fatal
