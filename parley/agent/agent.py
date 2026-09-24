"""The agent: one async loop over the inbound queue.

Everything the theme asks for meets here, and the ordering inside
`_on_transcript` is the whole design compressed into ten lines:

    1. interpret the chunk               (incremental, at repair onset)
    2. apply the FLOOR policy            (speak / adapt / yield)
    3. apply the WORK policy             (keep / selective cancel / cancel all)
    4. update state                      (patch slots, or switch goal)
    5. re-plan                           (manifest-driven)
    6. dispatch                          (speculatively if the turn is unfinished)
    7. acknowledge                       (naming the slots that fed the call)

Steps 2 and 3 are separate because they are separate decisions — that is the
claim the whole project rests on. A barge-in runs step 2 and skips step 3; a
refinement runs step 3 as a no-op and adapts in step 2; only a goal switch does
the thing every surveyed framework does unconditionally.

Cancellation happens *before* state is mutated, so an in-flight call is always
identified as a reader of the slot's **old** value. Mutating first would make
the call look consistent with the new value and it would survive when it should
not — an ordering bug that is invisible in a passing demo and fatal on the
hidden set.

Nothing in the loop blocks. Tool calls are tasks; the loop returns to the queue
immediately, which is what keeps the fast path fast while the slow path runs.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable

from ..kernel.calls import CallOutcome, CallRecord
from ..kernel.dispatcher import Dispatcher, ToolExecutor
from ..kernel.policy import FloorPolicy, InterruptionKind, WorkPolicy, policy_for
from ..protocol.actions import Claim, ClaimKind
from ..protocol.events import (
    AudioClip,
    EventType,
    InterruptionSignal,
    SessionEnd,
    SessionStart,
    ToolManifestEvent,
    ToolResult,
    TranscriptChunk,
    VideoFrame,
)
from ..protocol.manifest import ToolManifest, parse_manifest
from ..protocol.state import SessionState, SlotSource
from .floor import FloorManager, describe_tool
from .lexicon import Lexicon
from .model import InterruptionModel
from .nlu import Interpretation, Interpreter, Turn
from .planner import Planner

COMMIT_VERBS = frozenset({"book", "reserve", "confirm", "buy", "purchase", "create", "raise", "open", "file"})
"""Words that turn a search into a commitment. A state-modifying tool is never
dispatched without one of these in the turn — half a sentence cannot book."""


class ParleyAgent:
    def __init__(
        self,
        session_id: str,
        *,
        clock: Any,
        trace: Any,
        executor: ToolExecutor,
        manifest: ToolManifest | None = None,
        model: InterruptionModel | None = None,
        on_action: Callable[[Any], None] | None = None,
    ) -> None:
        self.session_id = session_id
        self.clock = clock
        self.trace = trace
        self.executor = executor
        self.on_action = on_action

        self.state = SessionState(session_id=session_id)
        self.manifest = manifest or ToolManifest()
        self.lexicon = Lexicon.for_manifest(self.manifest)
        self.interpreter = Interpreter(self.lexicon, model=model)
        self.planner = Planner(self.manifest)

        self.kernel = Dispatcher(
            clock=clock, trace=trace, state=self.state,
            manifest=self.manifest, executor=executor, on_action=on_action,
        )
        self.floor = FloorManager(
            clock=clock, trace=trace, state=self.state,
            registry=self.kernel.registry, on_action=on_action,
        )

        self._turn_text: list[str] = []
        self._agent_is_speaking = False
        self._pending_interruption = False
        self._perceptions: dict[str, Any] = {}
        self._grounding: list[asyncio.Task[Any]] = []
        self._asked_for: set[str] = set()
        self._finalised = False

    # ================================================================ loop

    async def run(self, events: AsyncIterator[Any]) -> None:
        async for event in events:
            await self.handle(event)
        if not self._finalised:
            await self.finalise("stream ended")

    async def handle(self, event: Any) -> None:
        self.trace.event(getattr(event, "t", self.clock.now), event.type.value, **event.model_dump(mode="json"))

        if event.type is EventType.SESSION_START:
            self._on_session_start(event)
        elif event.type is EventType.TOOL_MANIFEST:
            self._on_manifest(event)
        elif event.type is EventType.TRANSCRIPT_CHUNK:
            await self._on_transcript(event)
        elif event.type is EventType.INTERRUPTION:
            self._on_interruption(event)
        elif event.type is EventType.AUDIO_CLIP:
            await self._on_audio(event)
        elif event.type is EventType.VIDEO_FRAME:
            await self._on_frame(event)
        elif event.type is EventType.TOOL_RESULT:
            self._on_tool_result(event)
        elif event.type is EventType.SESSION_END:
            await self.finalise(event.reason)

    # ================================================================ setup

    def _on_session_start(self, event: SessionStart) -> None:
        self.state.session_id = event.session_id
        self.session_id = event.session_id

    def _on_manifest(self, event: ToolManifestEvent) -> None:
        """Tools arrive as data, mid-session, and everything re-derives from them."""
        self.manifest = parse_manifest(event.manifest)
        self.lexicon = Lexicon.for_manifest(self.manifest)
        self.interpreter.lexicon = self.lexicon
        self.planner = Planner(self.manifest)
        self.kernel.manifest = self.manifest
        self.trace.kernel(
            self.clock.now, "manifest_loaded",
            tools=sorted(self.manifest.tools),
            mutating=[t.name for t in self.manifest.mutating],
            intents=sorted(self.manifest.intents()),
        )

    def _on_interruption(self, event: InterruptionSignal) -> None:
        """A bare VAD signal carries no meaning.

        It tells us speech started, nothing about what kind. Classifying it now
        would be guessing; we record that the next chunk arrived over our voice
        and let the words decide.
        """
        self._pending_interruption = True
        self.trace.kernel(self.clock.now, "interruption_signal", source=event.source)

    def _on_tool_result(self, event: ToolResult) -> None:
        """For harnesses that deliver results as events rather than by await."""
        resolve = getattr(self.executor, "resolve", None)
        if resolve is not None:
            resolve(event)

    # ================================================================ speech

    async def _on_transcript(self, event: TranscriptChunk) -> None:
        overlapping = self._pending_interruption or self._agent_is_speaking
        self._pending_interruption = False
        self._turn_text.append(event.text)

        # Interpret the chunk, not the accumulated turn: a repair is detectable
        # at repair onset, and waiting for end-of-turn would forfeit the latency
        # block on exactly the turns that matter most.
        turn = Turn(
            text=event.text,
            t=event.t,
            end_of_turn=event.end_of_turn,
            overlapping_agent_speech=overlapping,
        )
        interp = self.interpreter.interpret(
            turn,
            self.state,
            in_flight=len(self.kernel.registry.in_flight()),
            prefer_slots=self.manifest.params_for_intent(self.state.intent),
        )
        self.trace.kernel(event.t, "interpretation", **interp.to_payload())

        self._apply_floor(interp)
        await self._apply_work(interp)
        just_bound = self._apply_state(interp, event)

        if interp.kind is InterruptionKind.REPEAT_REQUEST:
            self.floor.repeat_last()
            if event.end_of_turn:
                self._turn_text.clear()
            return

        if interp.kind in (InterruptionKind.BACKCHANNEL, InterruptionKind.SELF_REPAIR):
            if event.end_of_turn:
                self._turn_text.clear()
            return

        spoken_before = len(self.floor.transcript)
        await self._replan(interp, event, just_bound)

        # The user finished a turn that produced no new plan — a barge-in, or a
        # request we already have in hand. Saying nothing here is what makes a
        # user repeat themselves; saying where we are up to is both responsive
        # and true. Only after end-of-turn: interrupting a half-finished
        # sentence to narrate progress would be worse than silence.
        if (
            event.end_of_turn
            and len(self.floor.transcript) == spoken_before
            and interp.policy.floor is not FloorPolicy.CONTINUE
        ):
            if self.kernel.registry.in_flight():
                self.floor.progress()
            else:
                self._ask_for_missing()

        if event.end_of_turn:
            self._turn_text.clear()

    def _apply_floor(self, interp: Interpretation) -> None:
        if interp.policy.floor is FloorPolicy.YIELD:
            self.floor.yield_floor(interp.policy)
            self._agent_is_speaking = False

    async def _apply_work(self, interp: Interpretation) -> None:
        """Cancel BEFORE mutating state.

        A call is identified as stale by having read the slot's *old* value. Patch
        the slot first and the call's recorded dependency still matches the new
        state, so it survives when it should die.
        """
        if interp.policy.work is WorkPolicy.KEEP_ALL:
            return
        await self.kernel.apply_work_policy(interp.policy, interp.changed_slots)

    def _apply_state(self, interp: Interpretation, event: TranscriptChunk) -> set[str]:
        """Patch slots, or switch goal while retaining what still applies."""
        just_bound: set[str] = set()

        if interp.policy.replans and interp.intent:
            keep = self.planner.slots_to_keep(interp.intent)
            delta = self.state.retain_for_goal_switch(interp.intent, keep)
            self.trace.kernel(
                event.t, "goal_switch",
                to=interp.intent, kept=sorted(set(self.state.slots)), dropped=delta.cleared_slots,
            )
        elif interp.intent and self.state.intent is None:
            self.state.set_intent(interp.intent)

        for match in interp.corrections + interp.additions:
            self.state.set_slot(
                match.slot, match.value,
                confidence=match.confidence,
                source=SlotSource.TEXT,
                evidence=match.surface,
                surface=match.surface,
            )
            just_bound.add(match.slot)

        # A restated value changes nothing about the state, but the user did
        # supply it this turn. Without it, "book UK404" repeated after a failed
        # attempt bound no new slot, so the mutating tool was never re-planned
        # and the retry silently never happened. The idempotency ledger is what
        # makes this safe: a genuine duplicate is suppressed before dispatch.
        just_bound |= {m.slot for m in interp.restatements}

        if interp.corrections:
            self.floor.acknowledge_correction(m.slot for m in interp.corrections)
        elif just_bound and not event.end_of_turn:
            # Mid-turn: say what we heard now rather than waiting for the turn
            # to finish. Any tool call at this point is speculative and therefore
            # unspeakable, so this is the only thing keeping the first-response
            # latency low on a multi-chunk turn.
            self.floor.acknowledge_slots(just_bound)

        return just_bound

    async def _replan(self, interp: Interpretation, event: TranscriptChunk, just_bound: set[str]) -> None:
        committed = self._is_committal(event.text)
        planned = self.planner.plan(
            self.state,
            end_of_turn=event.end_of_turn,
            committed=committed,
            just_bound=just_bound,
        )

        for call in planned:
            issued_at = self.clock.now
            record = await self.kernel.dispatch(
                call.tool, call.args, call.read_slots,
                speculative=call.speculative, intent=self.state.intent,
            )
            if call.speculative:
                # Speculation is invisible: narrating a guess would be claiming
                # we are doing something the user did not ask for.
                continue

            if record.outcome is CallOutcome.DUPLICATE_SUPPRESSED:
                spoken = self.floor.acknowledge_suppression(record, self._prior_for(record))
            elif record.dispatched_at < issued_at and record.mutating:
                # We joined a state-changing call already running rather than
                # issuing a second one. Correct, and silent — which is why the
                # user asked again. Read-only reuse stays silent: we already
                # said we were searching, and "that's already done" about a
                # search is both uninformative and faintly untrue.
                spoken = self.floor.acknowledge_suppression(record, record)
            elif record.in_flight:
                spoken = self.floor.acknowledge_dispatch(record)
            else:
                spoken = None
            self._agent_is_speaking = spoken is not None

    def _prior_for(self, suppressed: CallRecord) -> CallRecord | None:
        """The call a suppressed duplicate was blocked in favour of."""
        entry = self.kernel.ledger.get(suppressed.idempotency_key or "")
        if entry is None:
            return None
        for call_id in reversed(entry.call_ids):
            record = self.kernel.registry.get(call_id)
            if record is not None and record is not suppressed:
                return record
        return None

    def _ask_for_missing(self) -> None:
        """Nothing could be planned and nothing is running — so ask why.

        A specific question scores where silence does not, and where a generic
        failure scores worse still. Each slot is asked about once: repeating
        "Where to?" every turn is its own failure mode.
        """
        if self.state.intent is None:
            return
        missing = [
            name for name in self.planner.missing_for(self.state.intent, self.state)
            if name not in self._asked_for
        ]
        if not missing:
            return

        slot = missing[0]
        self._asked_for.add(slot)
        spec = next(
            (s for s in self.planner.plannable for p in s.params
             if p.name == slot and p.enum),
            None,
        )
        options = next((p.enum for p in spec.params if p.name == slot), []) if spec else []
        self.floor.clarify_missing(slot, options or [])

    def _is_committal(self, text: str) -> bool:
        words = {w.strip(",.!?").lower() for w in text.split()}
        return bool(words & COMMIT_VERBS)

    # ================================================================ multimodal

    async def _on_audio(self, event: AudioClip) -> None:
        from ..multimodal import ground_audio

        self._start_grounding(ground_audio, event, event.clip_id, SlotSource.AUDIO)

    async def _on_frame(self, event: VideoFrame) -> None:
        from ..multimodal import ground_frame

        self._start_grounding(ground_frame, event, event.frame_id, SlotSource.VISION)

    def _start_grounding(
        self, grounder: Callable[..., Any], event: Any, ident: str, source: SlotSource
    ) -> None:
        """Acknowledge now, decode in the background.

        "Process raw audio and frames **behind conversational acknowledgments**"
        is not a description of tone — it is a concurrency requirement. Awaiting
        the decode inline would park the whole event loop for the duration, so
        an interruption arriving mid-decode would be handled late and the
        cancellation grace period would blow out. The acknowledgment goes out
        first and the decode becomes a task, exactly like a tool call.
        """
        ack = self.floor.acknowledge_media(
            "audio" if source is SlotSource.AUDIO else "vision"
        )
        self._agent_is_speaking = ack is not None
        task = asyncio.create_task(
            self._ground(grounder, event, ident, source), name=f"ground:{ident}"
        )
        self._grounding.append(task)

    async def _ground(self, grounder: Callable[..., Any], event: Any, ident: str, source: SlotSource) -> None:
        perception = await grounder(event, clock=self.clock)
        self._perceptions[ident] = perception
        self.trace.kernel(self.clock.now, "perception", **perception.to_payload())

        if perception.ambiguous:
            self.floor.clarify(
                perception.question or "I'm not sure what I'm looking at — can you tell me?",
                slot=perception.slot,
                options=perception.candidates,
                reason="ambiguous",
            )
            return

        if perception.label is None:
            self.floor.clarify(
                "I couldn't make that out — could you describe it?",
                slot=perception.slot, reason="low_confidence",
            )
            return

        delta = self.state.set_slot(
            perception.slot, perception.label,
            confidence=perception.confidence, source=source, evidence=ident,
            surface=str(perception.label).replace("_", " "),
        )

        # A second photograph superseding the first is a slot correction whose
        # source happens to be a camera rather than a voice. The dataflow rule
        # does not care which modality bound the slot, and routing perception
        # around the cancellation path left the first lookup running against a
        # value nobody held any more.
        if delta.invalidating_slots:
            await self.kernel.apply_work_policy(
                policy_for(InterruptionKind.SLOT_CORRECTION), delta.invalidating_slots
            )
            self.floor.acknowledge_correction([perception.slot])

        if self.state.intent is None:
            inferred = self.planner.infer_intent(self.state)
            if inferred:
                self.state.set_intent(inferred)
                self.trace.kernel(self.clock.now, "intent_inferred",
                                  intent=inferred, from_slots=sorted(self.state.slots))

        planned = self.planner.plan(
            self.state, end_of_turn=True, committed=False, just_bound={perception.slot}
        )
        for call in planned:
            record = await self.kernel.dispatch(
                call.tool, call.args, call.read_slots, intent=self.state.intent
            )
            if record.in_flight:
                self.floor.acknowledge_dispatch(record)

    # ================================================================ finalise

    async def finalise(self, reason: str = "complete") -> Any:
        """Settle everything, resolve every uncertain effect, then answer.

        Order matters. A final response carries the state snapshot and the
        snapshot is compared against the environment, so every effect that might
        disagree with it has to be resolved — or disclosed — before we speak.
        """
        self._finalised = True

        # Perception first: a frame still decoding may yet bind a slot that the
        # final snapshot has to carry, and may yet dispatch a call.
        if self._grounding:
            await asyncio.gather(*self._grounding, return_exceptions=True)

        in_flight = self.kernel.registry.in_flight()
        if in_flight:
            await asyncio.gather(
                *(c.task for c in in_flight if c.task), return_exceptions=True
            )

        for record in await self.kernel.resolve_effects():
            self.floor.disclose_uncertain_effect(record)

        for record in self.kernel.registry:
            if record.outcome is CallOutcome.COMPENSATED:
                self.floor.disclose_compensation(record)

        text, claims, grounded = self._compose_final()
        return self.floor.final(text, claims=claims, grounded_on=grounded)

    def _compose_final(self) -> tuple[str, list[Claim], list[str]]:
        """Ground the answer in results that are still valid.

        Stale and cancelled calls are excluded by construction rather than by
        remembering to check — which is the difference between an agent that
        usually tells the truth and one that cannot do otherwise.
        """
        # Staleness is re-checked *now*, not trusted from settlement time. A
        # call can finish perfectly valid and be superseded a second later: the
        # manual lookup for the washing machine completed before the user
        # switched to the television, so its outcome says STILL_VALID and its
        # answer is about the wrong appliance. Grounding the final response in
        # it would be truthful about the call and misleading about the world.
        usable = []
        for record in self.kernel.registry:
            if record.outcome is not CallOutcome.COMPLETED_STILL_VALID or record.result is None:
                continue
            if self.state.is_stale(record.read_slots, record.state_revision):
                self.trace.kernel(
                    self.clock.now, "result_superseded",
                    call_id=record.call_id, tool=record.tool,
                    read_slots=sorted(record.read_slots),
                    dispatched_at_revision=record.state_revision,
                    now_revision=self.state.revision,
                )
                continue
            usable.append(record)
        # A state change that failed has to be said out loud. Not claiming
        # success is necessary but not sufficient: silence about a booking that
        # did not happen leaves the user believing it did, which is the same
        # outcome as lying about it.
        failed = [
            r for r in self.kernel.registry
            if r.outcome is CallOutcome.FAILED and r.mutating and r.error
            and not any(
                o.signature == r.signature and o.outcome.had_effect
                for o in self.kernel.registry
            )
        ]
        failure_lines = []
        seen_failures: set[tuple] = set()
        for record in failed:
            if record.signature in seen_failures:
                continue
            seen_failures.add(record.signature)
            reason = record.error.split(":", 1)[-1].strip()
            failure_lines.append(
                f"I couldn't {describe_tool(record.tool).replace('ing ', ' ')} "
                f"{_subject(record)} — {reason}."
            )

        if not usable:
            missing = self.planner.missing_for(self.state.intent, self.state)
            if failure_lines:
                return " ".join(failure_lines), [], []
            if missing:
                return (
                    f"I still need {' and '.join(m.replace('_', ' ') for m in missing)} before I can help.",
                    [],
                    [],
                )
            return "I wasn't able to complete that.", [], []

        claims: list[Claim] = []
        parts: list[str] = []
        grounded: list[str] = []

        for record in usable:
            grounded.append(record.call_id)
            if record.mutating:
                claims.append(
                    Claim(
                        kind=ClaimKind.COMPLETED,
                        subject=record.call_id,
                        value=record.tool,
                        warrant=f"settled {record.outcome.value} at {record.settled_at:.0f} ms",
                    )
                )
            sentence = _describe_result(record)
            # Two calls can legitimately produce the same sentence (a search
            # narrowed to the same count, say). Saying it twice is noise the
            # quality multiplier notices.
            if sentence not in parts:
                parts.append(sentence)

        for name, slot in self.state.slots.items():
            if slot.confidence >= 0.55:
                claims.append(
                    Claim(
                        kind=ClaimKind.SLOT_VALUE, subject=name, value=slot.value,
                        warrant=f"bound at revision {slot.revision}", confidence=slot.confidence,
                    )
                )

        return " ".join(failure_lines + parts), claims, grounded


def _subject(record: CallRecord) -> str:
    """The first argument, in words, for a one-line failure report."""
    if not record.args:
        return "that"
    return str(next(iter(sorted(record.args.values())))).replace("_", " ")


def _describe_result(record: CallRecord) -> str:
    """A short, literal sentence about one tool result. No embellishment."""
    result = record.result
    if isinstance(result, dict):
        for key in ("flights", "hotels", "bookings"):
            if key in result:
                n = result.get("count", len(result[key]))
                return f"I found {n} {key.rstrip('s')}{'s' if n != 1 else ''}."
        if "pnr" in result:
            return f"Booked — your reference is {result['pnr']}."
        if "confirmation" in result:
            return f"Booked — confirmation {result['confirmation']}."
        if "ticket_id" in result:
            return f"Raised ticket {result['ticket_id']}."
        if result.get("found") and "steps" in result:
            steps = "; ".join(result["steps"][:3])
            return f"{result.get('meaning', '')} Try this: {steps}"
    return f"{record.tool.replace('_', ' ')} came back."
