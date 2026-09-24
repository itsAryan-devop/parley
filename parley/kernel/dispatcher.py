"""The coordination layer: dispatch, cancel, join, verify, compensate.

75% of every scenario (task completion 40% + interruption recovery 35%) is
decided here rather than by model quality, so this module is the product.

Four guarantees, each of which exists because of a specific way agents fail:

1. **No call ever leaves the trace unaccounted for.** Dispatch is wrapped so the
   cancellation path writes its outcome on the way out. Scoring reads trace
   logs; a call that silently disappears is unscoreable and plausibly scored as
   a failure.

2. **A state-modifying call is claimed before it is dispatched.** Claiming after
   dispatch is a race two re-plans 5 ms apart will lose.

3. **Confirming a call that is already in flight joins it instead of
   re-issuing it.** Without join, speculation manufactures exactly the duplicate
   state changes the 10% block penalises (PASTE, arXiv 2603.18897).

4. **Cancelling a state-modifying call leaves uncertainty, and uncertainty is
   resolved rather than assumed away.** The cancel may have landed after the
   effect committed. We probe with the manifest's declared verifier, compensate
   if the effect is there, and — when no verifier exists — say so out loud. What
   we never do is record it as clean.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Iterable, Protocol

from ..protocol.manifest import ToolManifest, ToolSpec
from ..protocol.state import SessionState
from .calls import CallOutcome, CallRecord, CallRegistry
from .ledger import ClaimResult, EntryState, IdempotencyLedger, derive_key
from .policy import InterruptionPolicy, WorkPolicy


class ToolExecutor(Protocol):
    """The adapter boundary between the kernel and whatever runs the tools.

    Our mock environment satisfies this; so will the real evaluation kit. The
    kernel never imports either.
    """

    def __call__(
        self, tool: str, args: dict[str, Any], call_id: str, *, mutating: bool
    ) -> Awaitable[Any]: ...


class SpeculationRefused(RuntimeError):
    """Raised on any attempt to speculate a state-modifying tool.

    A hard error rather than a warning: speculative state changes are the one
    failure this design must make structurally impossible.
    """


class Dispatcher:
    def __init__(
        self,
        *,
        clock: Any,
        trace: Any,
        state: SessionState,
        manifest: ToolManifest,
        executor: ToolExecutor,
        on_action: Callable[[Any], None] | None = None,
    ) -> None:
        self.clock = clock
        self.trace = trace
        self.state = state
        self.manifest = manifest
        self.executor = executor
        self.registry = CallRegistry()
        self.ledger = IdempotencyLedger()
        self._on_action = on_action

    # ------------------------------------------------------------------ dispatch

    async def dispatch(
        self,
        tool: str,
        args: dict[str, Any],
        read_slots: Iterable[str] = (),
        *,
        speculative: bool = False,
        intent: str | None = None,
    ) -> CallRecord:
        """Issue a non-blocking tool call, or join an equivalent one already running."""
        now = self.clock.now
        spec = self.manifest.get(tool)

        if spec is None:
            return self._stillborn(tool, args, read_slots, f"unknown tool {tool!r}", now)

        if speculative and spec.mutating:
            self.trace.kernel(
                now, "speculation_refused", tool=tool,
                reason="state-modifying tools are never speculated",
            )
            raise SpeculationRefused(f"{tool} is state-modifying")

        # --- join an equivalent live call ---------------------------------
        live = self.registry.live_match(tool, args)
        if live is not None:
            return self._join(live, confirming=not speculative, now=now)

        # --- reuse an equivalent answer we already have --------------------
        if not spec.mutating:
            prior = self.registry.completed_match(tool, args)
            if prior is not None and not self.state.is_stale(prior.read_slots, prior.state_revision):
                self.trace.kernel(
                    now, "result_reused",
                    call_id=prior.call_id, tool=tool, args=args,
                    answered_at=prior.settled_at,
                )
                return prior

        # --- retire guesses this dispatch makes obsolete ------------------
        if not speculative:
            for stale_guess in self.registry.superseded_speculations(tool, args):
                await self.cancel(stale_guess, "superseded_by_confirmation")

        # --- idempotency claim, before dispatch ---------------------------
        key: str | None = None
        if spec.mutating:
            key = derive_key(spec, intent or self.state.intent, args)
            call_id = self.registry.next_call_id()
            claim, entry = self.ledger.claim(key, tool, call_id, now)

            if claim is ClaimResult.ALREADY_SUCCEEDED:
                return self._suppress(tool, args, read_slots, key, entry.call_ids, now)

            if claim is ClaimResult.IN_FLIGHT:
                # Same key, different arguments spelling — e.g. the manifest
                # restricts identity to a subset of params. Join the live call
                # if we can find it; otherwise suppress rather than duplicate.
                for prior in entry.call_ids:
                    rec = self.registry.get(prior)
                    if rec is not None and rec.in_flight:
                        return self._join(rec, confirming=True, now=now)
                return self._suppress(tool, args, read_slots, key, entry.call_ids, now)
        else:
            call_id = self.registry.next_call_id()

        record = self.registry.add(
            CallRecord(
                call_id=call_id,
                tool=tool,
                args=dict(args),
                read_slots=set(read_slots),
                state_revision=self.state.revision,
                mutating=spec.mutating,
                dispatched_at=now,
                speculative=speculative,
                idempotency_key=key,
            )
        )

        self._emit_tool_call(record)
        record.task = asyncio.create_task(self._run(record), name=f"call:{call_id}")
        return record

    async def _run(self, record: CallRecord) -> Any:
        """The cancellation-safe wrapper. Every exit writes an outcome."""
        try:
            result = await self.executor(
                record.tool, record.args, record.call_id, mutating=record.mutating
            )
        except asyncio.CancelledError:
            # A cancel landed. For a read-only tool that is the end of it. For a
            # state-modifying tool we do not know whether the effect committed,
            # and saying otherwise is the lie that desynchronises the snapshot.
            self._settle(
                record,
                CallOutcome.CANCELLED_UNCERTAIN if record.mutating else CallOutcome.CANCELLED_BEFORE_EFFECT,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - tool faults are data, not bugs
            self._settle(record, CallOutcome.FAILED, error=str(exc))
            if record.idempotency_key:
                self.ledger.failed(record.idempotency_key, self.clock.now)
            return None
        else:
            stale = self.state.is_stale(record.read_slots, record.state_revision)
            self._settle(
                record,
                CallOutcome.COMPLETED_NOW_STALE if stale else CallOutcome.COMPLETED_STILL_VALID,
                result=result,
            )
            if record.idempotency_key:
                self.ledger.succeeded(record.idempotency_key, self.clock.now)
            return result

    # ------------------------------------------------------------------ cancel

    async def cancel(self, record: CallRecord, reason: str, by_slots: Iterable[str] = ()) -> CallRecord:
        """Request cancellation of one call and wait for it to settle."""
        if not record.in_flight:
            return record

        record.cancel_reason = reason
        record.invalidated_by = sorted(by_slots)

        self._emit(
            "cancel",
            call_id=record.call_id,
            tool=record.tool,
            reason=reason,
            invalidated_by_slots=record.invalidated_by,
        )

        if record.task is not None:
            record.task.cancel()
            await asyncio.gather(record.task, return_exceptions=True)
        return record

    async def apply_work_policy(
        self, policy: InterruptionPolicy, changed_slots: Iterable[str] = ()
    ) -> list[CallRecord]:
        """Turn an interruption policy into cancellations — or into nothing.

        All cancel requests are issued in the same instant before any is awaited,
        so the grace period the guide asks for ("within a few ms") does not grow
        with the number of in-flight calls.
        """
        changed = set(changed_slots)
        now = self.clock.now

        if policy.work is WorkPolicy.KEEP_ALL:
            self.trace.kernel(
                now, "work_policy",
                policy=policy.to_payload(), cancelled=[],
                kept=[c.call_id for c in self.registry.in_flight()],
            )
            return []

        if policy.work is WorkPolicy.SELECTIVE:
            victims = self.registry.invalidated_by(changed)
            reason = f"slot_correction:{','.join(sorted(changed))}"
        else:
            victims = self.registry.in_flight()
            reason = "goal_switch"

        survivors = [c.call_id for c in self.registry.in_flight() if c not in victims]
        self.trace.kernel(
            now, "work_policy",
            policy=policy.to_payload(),
            changed_slots=sorted(changed),
            cancelled=[c.call_id for c in victims],
            kept=survivors,
        )

        await asyncio.gather(*(self.cancel(v, reason, changed) for v in victims))
        return victims

    async def cancel_all(self, reason: str) -> list[CallRecord]:
        victims = self.registry.in_flight()
        await asyncio.gather(*(self.cancel(v, reason) for v in victims))
        return victims

    # ------------------------------------------- resolve uncertain / stale effects

    async def resolve_effects(self) -> list[CallRecord]:
        """Close out every call whose effect may disagree with our snapshot.

        Must run to completion before a final response is emitted, because the
        snapshot in that response is compared against the environment.

        Returns the records that could not be resolved mechanically and must
        therefore be disclosed in the transcript.
        """
        needs_disclosure: list[CallRecord] = []

        for record in self.registry.unresolved():
            if not record.mutating:
                record.resolution = "read_only_no_effect"
                continue

            landed = await self._verify_effect(record)

            if landed is None:
                # No verifier in the manifest. We cannot know, so we say so.
                record.resolution = "disclosed"
                self.trace.kernel(
                    self.clock.now, "effect_undetermined",
                    call_id=record.call_id, tool=record.tool,
                    reason="no verifier declared for this tool",
                )
                needs_disclosure.append(record)
                continue

            if not landed:
                record.outcome = CallOutcome.CANCELLED_BEFORE_EFFECT
                record.resolution = "verified_no_effect"
                if record.idempotency_key:
                    self.ledger.failed(record.idempotency_key, self.clock.now)
                self.trace.kernel(
                    self.clock.now, "effect_verified_absent",
                    call_id=record.call_id, tool=record.tool,
                )
                continue

            # The effect is real and the plan no longer wants it.
            record.outcome = CallOutcome.COMPLETED_NOW_STALE
            compensated = await self._compensate(record)
            if compensated:
                record.outcome = CallOutcome.COMPENSATED
                record.resolution = "compensated"
                if record.idempotency_key:
                    self.ledger.compensated(record.idempotency_key, self.clock.now)
            else:
                record.resolution = "disclosed"
                needs_disclosure.append(record)

        return needs_disclosure

    async def _verify_effect(self, record: CallRecord) -> bool | None:
        """Ask the environment whether `record`'s effect actually landed.

        Returns None when the manifest declares no verifier for this tool — an
        honest "cannot know", distinct from a verified False.
        """
        verifier = self.manifest.verifier_for(record.tool)
        if verifier is None:
            return None

        probe = await self.dispatch(verifier.name, {}, ())
        if probe.task is not None:
            await asyncio.gather(probe.task, return_exceptions=True)

        if probe.outcome is not CallOutcome.COMPLETED_STILL_VALID:
            return None

        landed = _contains_match(probe.result, record.args)
        self.trace.kernel(
            self.clock.now, "effect_probe",
            call_id=record.call_id, tool=record.tool,
            verifier=verifier.name, landed=landed,
        )
        return landed

    async def _compensate(self, record: CallRecord) -> bool:
        inverse = self.manifest.inverse_for(record.tool)
        if inverse is None:
            self.trace.kernel(
                self.clock.now, "compensation_unavailable",
                call_id=record.call_id, tool=record.tool,
            )
            return False

        undo = await self.dispatch(inverse.name, dict(record.args), (), intent=self.state.intent)
        if undo.task is not None:
            await asyncio.gather(undo.task, return_exceptions=True)

        ok = undo.outcome.had_effect
        self.trace.kernel(
            self.clock.now, "compensated" if ok else "compensation_failed",
            call_id=record.call_id, tool=record.tool,
            via=inverse.name, undo_call_id=undo.call_id,
        )
        return ok

    # ------------------------------------------------------------------ helpers

    def _join(self, live: CallRecord, *, confirming: bool, now: float) -> CallRecord:
        """Adopt an in-flight call rather than issuing an identical second one.

        When a speculative call is confirmed, its observation is ready at
        max(plan, tool) instead of plan + tool — the whole point of speculating.
        """
        was_speculative = live.speculative
        if confirming and was_speculative:
            live.speculative = False

        self.trace.kernel(
            now,
            "speculation_join" if was_speculative else "duplicate_join",
            call_id=live.call_id, tool=live.tool, args=live.args,
            confirmed=confirming,
            saved_ms=round(now - live.dispatched_at, 3),
        )
        return live

    def _suppress(
        self,
        tool: str,
        args: dict[str, Any],
        read_slots: Iterable[str],
        key: str,
        prior_call_ids: list[str],
        now: float,
    ) -> CallRecord:
        """Block a duplicate state change before it reaches the tool."""
        record = self.registry.add(
            CallRecord(
                call_id=self.registry.next_call_id("suppressed"),
                tool=tool,
                args=dict(args),
                read_slots=set(read_slots),
                state_revision=self.state.revision,
                mutating=True,
                dispatched_at=now,
                idempotency_key=key,
                outcome=CallOutcome.DUPLICATE_SUPPRESSED,
                settled_at=now,
                resolution="suppressed",
            )
        )
        self.trace.kernel(
            now, "duplicate_suppressed",
            call_id=record.call_id, tool=tool, args=args,
            idempotency_key=key, prior_call_ids=prior_call_ids,
        )
        return record

    def _stillborn(
        self, tool: str, args: dict[str, Any], read_slots: Iterable[str], error: str, now: float
    ) -> CallRecord:
        record = self.registry.add(
            CallRecord(
                call_id=self.registry.next_call_id("invalid"),
                tool=tool,
                args=dict(args),
                read_slots=set(read_slots),
                state_revision=self.state.revision,
                mutating=False,
                dispatched_at=now,
                outcome=CallOutcome.FAILED,
                error=error,
                settled_at=now,
                resolution="not_dispatched",
            )
        )
        self.trace.kernel(now, "dispatch_rejected", call_id=record.call_id, tool=tool, error=error)
        return record

    def _settle(
        self, record: CallRecord, outcome: CallOutcome, *, result: Any = None, error: str | None = None
    ) -> None:
        record.outcome = outcome
        record.result = result
        record.error = error
        record.settled_at = self.clock.now
        self.trace.kernel(record.settled_at, "call_settled", **record.to_payload())

    def _emit_tool_call(self, record: CallRecord) -> None:
        from ..protocol.actions import ToolCall

        action = ToolCall(
            t=record.dispatched_at,
            call_id=record.call_id,
            tool=record.tool,
            args=record.args,
            read_slots=sorted(record.read_slots),
            state_revision=record.state_revision,
            mutating=record.mutating,
            idempotency_key=record.idempotency_key,
            speculative=record.speculative,
        )
        self.trace.action(record.dispatched_at, "tool_call", **action.model_dump(mode="json"))
        if self._on_action:
            self._on_action(action)

    def _emit(self, name: str, **payload: Any) -> None:
        from ..protocol.actions import Cancel

        now = self.clock.now
        self.trace.action(now, name, t=now, **payload)
        if self._on_action and name == "cancel":
            self._on_action(
                Cancel(
                    t=now,
                    call_id=payload["call_id"],
                    reason=payload["reason"],
                    invalidated_by_slots=payload.get("invalidated_by_slots", []),
                )
            )


def _contains_match(result: Any, args: dict[str, Any]) -> bool:
    """Does `result` contain a record matching every key/value in `args`?

    Structural rather than tool-specific, because the kernel must work with
    unseen tools. Walks dicts and lists looking for any mapping that agrees with
    the dispatched arguments on every key they share.
    """
    if not args:
        return False

    def walk(node: Any) -> bool:
        if isinstance(node, dict):
            shared = [k for k in args if k in node]
            if shared and all(node[k] == args[k] for k in shared):
                return True
            return any(walk(v) for v in node.values())
        if isinstance(node, (list, tuple)):
            return any(walk(v) for v in node)
        return False

    return walk(result)
