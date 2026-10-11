"""Claude Haiku as a ``choose()`` backend for ``csfd fill --ai-match``.

Same question and options as Jev (``api/typesafe.py``), answered with structured output:
the reply is JSON whose ``choice`` is an enum of the option keys, so it cannot name an
option that does not exist. Haiku gives no per-option probabilities, so the question is
asked ``votes`` times in parallel and the confidence is the share of votes for the modal
choice. A failed vote counts against the pick, and a tie goes to ``none``.

Settings measured 2026-10-08 on the labelled ČSFD set (logs/haiku-poc/EVALUATION.md):
effort ``medium`` beat ``low``, 5 votes, p50 1.6 s per call, ≈ $0.0002 per call.
The vote share is NOT calibrated: both wrong picks in that test were unanimous. Never
auto-accept on it alone (``CrossCheckClient`` exists for that).

Needs the optional ``anthropic`` SDK: ``pip install 'emby-curator[claude]'``.
"""
from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from emby_dedupe.api.ai_choice import NONE_CHOICE, AiChoiceError, ChoiceAnswer
from emby_dedupe.utils.logging import logger

if TYPE_CHECKING:
    import anthropic

DEFAULT_CLAUDE_MODEL = "claude-haiku-5-5"
CLAUDE_EFFORT = "medium"
CLAUDE_VOTES = 5
MAX_TOKENS = 4000  # thinking is on by default and counts toward max_tokens
REQUEST_TIMEOUT = 60.0
SDK_RETRIES = 3  # the SDK retries 408/409/429/5xx and connection errors itself


class ClaudeChoiceError(AiChoiceError):
    """A Claude call failed (``fatal``: the key was rejected)."""


def _new_sdk_client(api_key: str) -> anthropic.Anthropic:
    try:
        import anthropic
    except ImportError as exc:  # optional extra not installed
        raise ClaudeChoiceError(
            "the Claude backend needs the anthropic SDK: pip install 'emby-curator[claude]'", fatal=True
        ) from exc
    return anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT, max_retries=SDK_RETRIES)


def render_prompt(state: object, instructions: str, criteria: dict[str, str | None]) -> str:
    """The question as text: instructions, the item as JSON, then one line per option."""
    options = "\n".join(f"- {key}: {text or key}" for key, text in criteria.items())
    item = json.dumps(state, ensure_ascii=False, indent=1)
    return f"{instructions}\n\nLibrary item:\n{item}\n\nOptions (key: description):\n{options}"


def answer_schema(keys: list[str]) -> dict[str, Any]:
    """JSON schema of the reply: a one-sentence reason first, then the chosen option key."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["reason", "choice"],
        "properties": {
            "reason": {"type": "string", "description": "One short sentence."},
            "choice": {"type": "string", "enum": keys},
        },
    }


def tally(votes: list[str | None], requested: int) -> ChoiceAnswer:
    """Modal vote and its share of the votes asked for. A tie for first goes to ``none``."""
    counts = Counter(v for v in votes if v is not None)
    if not counts:
        return ChoiceAnswer(NONE_CHOICE, 0.0)
    share = {choice: n / requested for choice, n in counts.items()}
    top = max(counts.values())
    leaders = [choice for choice, n in counts.items() if n == top]
    if len(leaders) > 1:
        return ChoiceAnswer(NONE_CHOICE, share.get(NONE_CHOICE, 0.0), share)
    return ChoiceAnswer(leaders[0], top / requested, share)


class ClaudeChoiceClient:
    """Ask Claude ``choice`` questions, ``votes`` times each."""

    def __init__(self, api_key: str, model: str = DEFAULT_CLAUDE_MODEL, votes: int = CLAUDE_VOTES,
                 sdk: Any = None) -> None:
        self._model = model
        self._votes = votes
        # Any: tests inject a fake with the same ``messages.create`` / ``close`` surface.
        self._sdk: Any = sdk if sdk is not None else _new_sdk_client(api_key)
        self._pool = ThreadPoolExecutor(max_workers=votes)

    @property
    def label(self) -> str:
        """What the review file says produced the suggestions."""
        return f"{self._model} x{self._votes}"

    def choose(self, state: object, instructions: str, criteria: dict[str, str | None]) -> ChoiceAnswer:
        """Ask ``votes`` times in parallel and return the tally.

        Raises:
            ClaudeChoiceError: when every vote failed (``fatal`` when the key was rejected).
        """
        prompt = render_prompt(state, instructions, criteria)
        schema = answer_schema(list(criteria))
        results = list(self._pool.map(lambda _: self._vote(prompt, schema), range(self._votes)))
        errors = [r for r in results if isinstance(r, ClaudeChoiceError)]
        if len(errors) == len(results):
            raise ClaudeChoiceError(f"all {len(results)} Claude votes failed: {errors[0]}",
                                    fatal=any(e.fatal for e in errors))
        return tally([r if isinstance(r, str) else None for r in results], self._votes)

    def _vote(self, prompt: str, schema: dict[str, Any]) -> str | ClaudeChoiceError | None:
        """One call: the chosen key, None for no usable answer, or the error."""
        import anthropic

        try:
            response = self._sdk.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                output_config={"effort": CLAUDE_EFFORT, "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": prompt}],
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            return ClaudeChoiceError(f"Claude rejected the API key: {exc}", fatal=True)
        except anthropic.APIError as exc:
            logger.debug(f"Claude vote failed: {exc}")
            return ClaudeChoiceError(f"{type(exc).__name__}: {exc}")
        if response.stop_reason != "end_turn":  # refusal or max_tokens: no vote
            logger.debug(f"Claude vote without an answer (stop_reason={response.stop_reason})")
            return None
        text = next((block.text for block in response.content if block.type == "text"), "")
        try:
            choice = json.loads(text)["choice"]
        except (ValueError, KeyError, TypeError):
            return None
        return choice if isinstance(choice, str) else None

    def close(self) -> None:
        """Stop the vote pool and the SDK's connection pool."""
        self._pool.shutdown(wait=False)
        self._sdk.close()
