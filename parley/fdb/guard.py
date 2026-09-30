"""Tool-call guard for the FDB-v3 agent -- `parley.kernel.ledger`, ported.

FDB-v3 scores tool selection by precision as well as recall, and its strict pass
rate fails a scenario on any *extra* call. The two ways a cascaded agent makes
an extra call are exactly the two PARLEY's kernel was built around:

1. **Duplicates.** The LLM re-issues a call it already made -- after an
   interruption, or when it re-reads its own history. The ledger's rule applies
   unchanged: the identity of an action is *what it does* -- the tool and its
   arguments, never the intent we attached to it at the time (`BUILD_LOG.md`,
   the double-booking bug). The key is claimed *before* the call executes, so a
   second copy racing the first is refused too.

2. **Acting on an abandoned value.** The turn ended on a pause inside a
   self-correction ("flights to Paris -- actually, no ... Berlin"), the LLM
   fired on "Paris", and the user was still talking. PARLEY's rule is *cancel
   before effect*: a call does not commit until a short grace window has passed
   with the user silent. If they start speaking inside it, the call is dropped
   before it executes and the LLM is told to wait for the rest.

Pure asyncio, no LiveKit import, one instance per conversation (the guide
forbids caching across scenarios).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

DEFAULT_GRACE_S = 0.3
# Speech not yet transcribed counts as "still talking" for at most this long
# after the user falls silent, in case a noise burst never yields a transcript.
PENDING_SPEECH_TTL_S = 4.0

DEFERRED = {
    "status": "not_executed",
    "reason": "The user started speaking again before this action ran, so it was "
              "cancelled. Wait for them to finish and act only on their final request.",
}


def _normalise(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.lower().split())
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def action_key(tool: str, args: dict[str, Any]) -> str:
    """What the action *does*: tool + normalised arguments. Nothing else."""
    norm = {k: _normalise(v) for k, v in sorted(args.items()) if v is not None}
    return json.dumps([tool, norm], sort_keys=True, default=str)


class ToolGuard:
    def __init__(
        self,
        grace_s: float = DEFAULT_GRACE_S,
        *,
        sleep: Callable[[float], Any] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.grace_s = grace_s
        self._sleep = sleep
        self._clock = clock
        self._speech_epoch = 0
        self._user_speaking = False
        self._finals = 0            # STT final transcripts received
        self._stopped_at = 0.0
        self._claimed: dict[str, Any] = {}
        self.suppressed: list[tuple[str, str]] = []  # (key, why) -- for logs

    def user_started_speaking(self) -> None:
        """Call on every VAD speech onset. Invalidates calls still in their grace window."""
        self._speech_epoch += 1
        self._user_speaking = True

    def user_stopped_speaking(self) -> None:
        """Call when VAD reports the user silent again."""
        self._user_speaking = False
        self._stopped_at = self._clock()

    def transcript_final(self) -> None:
        """Call on every final STT transcript (one per VAD speech segment)."""
        self._finals += 1

    def _speech_pending(self) -> bool:
        """The user spoke and STT has not delivered it yet: a correction may be in flight."""
        return (self._speech_epoch > self._finals
                and self._clock() - self._stopped_at < PENDING_SPEECH_TTL_S)

    async def run(
        self, tool: str, args: dict[str, Any], execute: Callable[[], Any]
    ) -> tuple[Any, bool]:
        """Return `(result, executed)`. `execute` runs at most once per distinct action."""
        key = action_key(tool, args)
        if key in self._claimed:
            self.suppressed.append((key, "duplicate"))
            prior = self._claimed[key]
            return {"status": "already_done", "note": "Identical request already "
                    "completed in this conversation; result repeated, not re-run.",
                    "result": prior}, False

        epoch = self._speech_epoch
        if self.grace_s > 0:
            await self._sleep(self.grace_s)
        # Resumed inside the window, never stopped, or said something STT has
        # not delivered yet: the LLM is acting on a turn the user already
        # talked past.
        if self._speech_epoch != epoch or self._user_speaking or self._speech_pending():
            self.suppressed.append((key, "user_resumed"))
            return dict(DEFERRED), False
        # A second copy of this call may have claimed it while we waited.
        if key in self._claimed:
            self.suppressed.append((key, "duplicate"))
            return {"status": "already_done", "result": self._claimed[key]}, False

        self._claimed[key] = None  # claim at issue time, before the effect
        result = execute()
        if asyncio.iscoroutine(result):
            result = await result
        self._claimed[key] = result
        return result, True
