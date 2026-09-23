"""Tests for emby_dedupe.api.typesafe — request shape, retries, error classes."""
from __future__ import annotations

import json

import httpx
import pytest

from emby_dedupe.api.typesafe import (
    CONNECT_TIMEOUT,
    DEFAULT_MODEL,
    MAX_ATTEMPTS,
    TYPESAFE_URL,
    TypesafeClient,
    TypesafeError,
)

API_KEY = "your-api-key"  # gitleaks-allowlisted placeholder shape
CRITERIA = {"csfd_1": "Film A (2020), film", "none": "None of them."}


def _answer(choice="csfd_1", confidence=0.93):
    return {"model": DEFAULT_MODEL, "answers": {"q": {
        "type": "choice", "choice": choice, "confidence": confidence,
        "probabilities": {"csfd_1": 0.95, "none": 0.05}}},
        "usage": {"input_tokens": 300, "output_tokens": 50}}


def _client(responses, sleeps=None):
    """A TypesafeClient whose transport replays ``responses`` (Response or exception) in order."""
    requests: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        nxt = queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    sleeps = sleeps if sleeps is not None else []
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return TypesafeClient(API_KEY, http=http, sleep=sleeps.append), requests


def test_choose_sends_pinned_model_and_parses_answer():
    client, requests = _client([httpx.Response(200, json=_answer())])
    got = client.choose({"title": "X"}, "Which one?", CRITERIA)
    assert (got.choice, got.confidence) == ("csfd_1", 0.93)
    assert got.probabilities["csfd_1"] == 0.95
    req = requests[0]
    assert str(req.url) == TYPESAFE_URL
    assert req.headers["Authorization"] == f"Bearer {API_KEY}"
    body = json.loads(req.content)
    assert body["model"] == DEFAULT_MODEL == "jev-1.13.0"  # pinned, not the moving jev-latest alias
    assert body["state"] == {"title": "X"}
    assert body["questions"]["q"] == {"type": "choice", "instructions": "Which one?", "criteria": CRITERIA}


def test_overload_is_retried_with_backoff():
    sleeps: list[float] = []
    client, requests = _client([httpx.Response(529), httpx.Response(429), httpx.Response(200, json=_answer())], sleeps)
    assert client.choose("s", "q", CRITERIA).choice == "csfd_1"
    assert len(requests) == 3 and sleeps == [1, 2]


def test_connect_failure_is_retried():
    """Regression: one API address never answers; a connect timeout must move on, not fail."""
    client, requests = _client([httpx.ConnectTimeout("dead address"), httpx.Response(200, json=_answer())])
    assert client.choose("s", "q", CRITERIA).choice == "csfd_1"
    assert len(requests) == 2


def test_gives_up_after_max_attempts():
    sleeps: list[float] = []
    client, requests = _client([httpx.ConnectError("down")] * MAX_ATTEMPTS, sleeps)
    with pytest.raises(TypesafeError) as err:
        client.choose("s", "q", CRITERIA)
    assert not err.value.fatal
    assert len(requests) == MAX_ATTEMPTS and sleeps == [1, 2, 4]


def test_bad_key_is_fatal_and_not_retried():
    client, requests = _client([httpx.Response(401)])
    with pytest.raises(TypesafeError) as err:
        client.choose("s", "q", CRITERIA)
    assert err.value.fatal and len(requests) == 1


def test_validation_error_is_not_fatal_and_not_retried():
    client, requests = _client([httpx.Response(422, text="criteria needs 2 options")])
    with pytest.raises(TypesafeError) as err:
        client.choose("s", "q", {"none": None})
    assert not err.value.fatal and "422" in str(err.value) and len(requests) == 1


def test_unexpected_response_shape_raises():
    client, _ = _client([httpx.Response(200, json={"answers": {}})])
    with pytest.raises(TypesafeError, match="unexpected"):
        client.choose("s", "q", CRITERIA)


def test_default_client_uses_short_connect_timeout():
    client = TypesafeClient(API_KEY)
    try:
        assert client._http.timeout.connect == CONNECT_TIMEOUT == 3.0
    finally:
        client.close()
