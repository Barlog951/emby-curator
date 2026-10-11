"""Shared shape of an AI "pick one option" answer, and the Claude + Jev cross-check.

``csfd fill --ai-match`` asks a model which ČSFD candidate (or ``none``) is the library
item. Two backends answer that question with the same ``choose()`` call: TypeSafe's Jev
(``api/typesafe.py``) and Claude Haiku (``api/claude_choice.py``). ``CrossCheckClient``
asks both: Claude proposes, Jev checks, and the answer carries a ``note`` saying whether
they agreed.

Why this shape (measured 2026-10-08, logs/haiku-poc/EVALUATION.md): on 43 labelled titles
Claude found 30 of 31 matches against Jev's 19, but made 2 confident wrong picks. Every
pick both models agreed on was right (19/19); all of Claude's mistakes were picks Jev did
not back. So agreement is the signal worth showing a reviewer, and the only one an
automatic accept may rely on.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

from emby_dedupe.utils.logging import logger

NONE_CHOICE = "none"
AGREE = "both-agree"


class AiChoiceError(RuntimeError):
    """An AI backend call failed. ``fatal`` means no later call can succeed either (bad key)."""

    def __init__(self, message: str, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


@dataclass
class ChoiceAnswer:
    """One backend's pick among the options.

    ``note`` is empty for a single backend. ``CrossCheckClient`` sets it to ``both-agree``,
    ``claude-only``, ``jev-only`` or ``conflict …`` (see the module docstring).
    """

    choice: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)
    note: str = ""


class ChoiceClient(Protocol):
    """What ``csfd fill`` needs from an AI backend."""

    def choose(self, state: object, instructions: str, criteria: dict[str, str | None]) -> ChoiceAnswer:
        """Pick one key of ``criteria`` for ``state``."""

    def close(self) -> None:
        """Release the connection."""


class CrossCheckClient:
    """Claude proposes, Jev checks; the answer's ``note`` records how they compared.

    A backend whose key is rejected is dropped for the rest of the run and the other one
    answers alone; a one-off failure only affects that question. Only when neither backend
    can answer does ``choose`` raise. Every failure is logged and counted in ``failures``
    (by backend), so a dead backend can't hide behind the one still answering.
    """

    def __init__(self, proposer: ChoiceClient, checker: ChoiceClient, review_min: float = 0.5) -> None:
        self._proposer: ChoiceClient | None = proposer
        self._checker: ChoiceClient | None = checker
        self._review_min = review_min
        self._clients = (proposer, checker)
        self.failures: Counter[str] = Counter()

    @property
    def label(self) -> str:
        """What the review file says produced the suggestions."""
        names = [getattr(c, "label", type(c).__name__) for c in self._clients]
        return f"{names[0]} checked by {names[1]}"

    def choose(self, state: object, instructions: str, criteria: dict[str, str | None]) -> ChoiceAnswer:
        """Ask both backends and combine their answers (see ``combine``)."""
        claude, claude_error = self._ask("_proposer", "Claude", state, instructions, criteria)
        jev, jev_error = self._ask("_checker", "Jev", state, instructions, criteria)
        if claude is None and jev is None:
            error = claude_error or jev_error or AiChoiceError("no AI backend left")
            raise AiChoiceError(f"both AI backends failed: {error}",
                                fatal=self._proposer is None and self._checker is None)
        return combine(claude, jev, self._review_min)

    def _ask(self, slot: str, name: str, state: object, instructions: str,
             criteria: dict[str, str | None]) -> tuple[ChoiceAnswer | None, AiChoiceError | None]:
        client: ChoiceClient | None = getattr(self, slot)
        if client is None:
            return None, None
        try:
            return client.choose(state, instructions, criteria), None
        except AiChoiceError as exc:
            self.failures[name] += 1
            if exc.fatal:
                setattr(self, slot, None)
                logger.error(f"{name} dropped for the rest of this run: {exc}")
            else:
                logger.warning(f"{name} failed on this item: {exc}")
            return None, exc

    def close(self) -> None:
        """Close both backends, including one dropped mid-run."""
        for client in self._clients:
            client.close()


def _picks(answer: ChoiceAnswer | None, review_min: float) -> bool:
    return answer is not None and answer.choice != NONE_CHOICE and answer.confidence >= review_min


def combine(claude: ChoiceAnswer | None, jev: ChoiceAnswer | None, review_min: float = 0.5) -> ChoiceAnswer:
    """Merge the two answers; ``None`` stands for a backend that could not answer.

    Claude's pick leads (it finds more). Jev's own pick is used only when Claude makes none.
    The confidence stays the leading backend's own number: the two are different scales.
    """
    if claude is None or jev is None:
        lone = claude if claude is not None else jev
        assert lone is not None  # CrossCheckClient raises before calling with two Nones
        tag = "claude-only" if claude is not None else "jev-only"
        return ChoiceAnswer(lone.choice, lone.confidence, lone.probabilities,
                            note=f"{tag} (other backend failed)" if _picks(lone, review_min) else "")
    if _picks(claude, review_min):
        if _picks(jev, review_min) and jev.choice == claude.choice:
            note = f"{AGREE}, jev {jev.confidence:.2f}"
        elif _picks(jev, review_min):
            note = f"conflict, jev picked {jev.choice} ({jev.confidence:.2f})"
        else:
            note = f"claude-only, jev {jev.confidence:.2f} for {jev.choice}"
        return ChoiceAnswer(claude.choice, claude.confidence, claude.probabilities, note=note)
    if _picks(jev, review_min):
        return ChoiceAnswer(jev.choice, jev.confidence, jev.probabilities, note="jev-only")
    return ChoiceAnswer(claude.choice, claude.confidence, claude.probabilities)
