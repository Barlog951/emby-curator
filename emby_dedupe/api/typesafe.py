"""Minimal client for TypeSafe's Jev model (https://docs.typesafe.ai).

Jev answers typed questions about a piece of content instead of generating text.
This module only needs its ``choice`` question type: given a state and a map of
options, Jev returns the chosen option, per-option probabilities and a
confidence (0-1). One endpoint, so plain httpx rather than the vendor SDK.

Connection handling matters here: one of the API's DNS addresses has been seen
to never answer (connects hung 50-75 s, 2026-09-23). A short connect timeout
plus retry moves on to the working address instead of waiting for the OS
timeout, and one persistent client keeps the working connection alive.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from emby_dedupe.utils.logging import logger

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
# Pinned: the ČSFD thresholds were measured on this version; ``jev-latest`` can move.
DEFAULT_MODEL = "jev-1.13.0"
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 60.0
MAX_ATTEMPTS = 4
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 529})


class TypesafeError(RuntimeError):
    """A TypeSafe call failed. ``fatal`` means no later call can succeed either (bad key)."""

    def __init__(self, message: str, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


@dataclass
class ChoiceAnswer:
    """Jev's answer to one ``choice`` question."""

    choice: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)


class TypesafeClient:
    """Ask Jev ``choice`` questions over one persistent HTTP connection."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._model = model
        self._sleep = sleep
        self._http = http or httpx.Client(
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
        )
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def choose(self, state: object, instructions: str, criteria: dict[str, str | None]) -> ChoiceAnswer:
        """Ask one ``choice`` question and return Jev's pick.

        Args:
            state: The content to judge (text or JSON-serialisable data).
            instructions: The question, phrased to match the fields in ``state``.
            criteria: Option name -> description (or None). Needs at least two options.

        Raises:
            TypesafeError: on a rejected request, a bad key (``fatal``) or when
                every retry failed.
        """
        body = {
            "model": self._model,
            "state": state,
            "questions": {"q": {"type": "choice", "instructions": instructions, "criteria": criteria}},
        }
        data = self._post(body)
        try:
            answer = data["answers"]["q"]
            return ChoiceAnswer(
                choice=answer["choice"],
                confidence=float(answer.get("confidence") or 0.0),
                probabilities=answer.get("probabilities") or {},
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TypesafeError(f"unexpected TypeSafe response: {exc!r}") from exc

    def _post(self, body: dict) -> dict:
        last_error = "no attempt made"
        for attempt in range(MAX_ATTEMPTS):
            if attempt:
                self._sleep(2 ** (attempt - 1))  # 1 s, 2 s, 4 s
            try:
                response = self._http.post(TYPESAFE_URL, json=body, headers=self._headers)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.debug(f"TypeSafe attempt {attempt + 1} failed: {last_error}")
                continue
            if response.status_code in (401, 403):
                raise TypesafeError(f"TypeSafe rejected the API key (HTTP {response.status_code})", fatal=True)
            if response.status_code in RETRY_STATUSES:
                last_error = f"HTTP {response.status_code}"
                continue
            if response.status_code != 200:
                raise TypesafeError(f"TypeSafe request rejected (HTTP {response.status_code}): {response.text[:200]}")
            result: dict = response.json()
            return result
        raise TypesafeError(f"TypeSafe unreachable after {MAX_ATTEMPTS} attempts ({last_error})")

    def close(self) -> None:
        """Close the underlying HTTP connection."""
        self._http.close()
