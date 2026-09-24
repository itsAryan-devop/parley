"""Floor management — the fast path, and the gate that keeps it honest.

Objective 1: "issue meaningful responses quickly **without making false
completion claims or excessive fillers**." Both halves of that are scored, and
the quality multiplier (0.80–1.20) rewards truthfulness on top, so an utterance
that overstates progress is penalised twice.

Two mechanisms.

**The provable-speech gate.** Every utterance is assembled from a template whose
variables bind only to facts the kernel can currently prove, and each assertion
carries its warrant into the trace as a `Claim`:

    slot value   provable iff the slot is bound above the speak threshold
    in progress  provable iff a CallRecord for it is still in flight
    completion   provable iff a CallRecord settled COMPLETED_STILL_VALID
    perception   provable iff a Perception cleared the grounding threshold

An unprovable claim does not produce a softer sentence — it produces a *different*
sentence, one that degrades to a progress form rather than a claim. "Booked" is
unreachable unless a booking exists.

**Rationing.** Fillers are the content-free kind and are capped; grounded
acknowledgments that name real slots are not, because they carry information.
That distinction is why `SpeechKind` exists.

Everything here is pure Python string assembly. No inference, no awaits, no
virtual time consumed — which is the whole reason the 15% latency block is
cheap for us.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ..kernel.calls import CallOutcome, CallRecord, CallRegistry
from ..kernel.policy import FloorPolicy, InterruptionPolicy
from ..protocol.actions import Claim, ClaimKind, Clarify, FinalResponse, Speak, SpeechKind
from ..protocol.state import SessionState

SPEAK_THRESHOLD = 0.55
"""Below this confidence a slot is not spoken aloud. It may still be used."""

MAX_CONSECUTIVE_FILLERS = 1
"""'Excessive fillers' is named in objective 1. One hold in a row, then silence."""

#: How a slot reads in a sentence. Unknown slots fall back to "name value",
#: which is clumsy but never wrong -- and unseen tools bring unknown slots.
_PHRASING: dict[str, str] = {
    "destination": "to {v}",
    "origin": "from {v}",
    "city": "in {v}",
    "date": "on {v}",
    "time_of_day": "in the {v}",
    "party_size": "for {v}",
    # Bare, because the tool name already supplies the noun: describe_tool
    # turns book_flight into "booking flight", and "flight {v}" made that
    # "Booking flight flight AI101".
    "flight_no": "{v}",
    "hotel_id": "{v}",
    "kayak_size": "a {v}",
    "max_price": "under {v}",
    "cabin": "in {v}",
    # Bare: describe_tool already contributes the noun, so "sound {v}" made
    # diagnose_sound read "diagnosing sound sound grinding".
    "label": "{v}",
    "sound": "{v}",
    "symptom": "{v}",
}

_GERUND: dict[str, str] = {
    "search": "searching", "find": "finding", "book": "booking",
    "create": "creating", "cancel": "cancelling", "lookup": "looking up",
    "look": "looking up", "get": "checking", "check": "checking",
    "identify": "looking at", "list": "listing", "reserve": "reserving",
}


def _gerund(verb: str) -> str:
    if verb in _GERUND:
        return _GERUND[verb]
    if verb.endswith("e") and not verb.endswith("ee"):
        return verb[:-1] + "ing"
    return verb + "ing"


def describe_tool(tool: str) -> str:
    """"search_flights" -> "searching flights". Manifest-driven, not hardcoded."""
    parts = tool.replace("-", "_").split("_")
    if not parts:
        return tool
    head = _gerund(parts[0].lower())
    tail = " ".join(parts[1:])
    return f"{head} {tail}".strip()


def phrase_slot(name: str, value: Any) -> str:
    return _PHRASING.get(name, "{n} {v}").format(n=name.replace("_", " "), v=value)


def _sentence_case(text: str) -> str:
    """Capitalise the first letter only.

    `str.capitalize()` lower-cases everything after it, which turned
    "to Mumbai" into "To mumbai" — a proper noun mangled in the first thing the
    user hears.
    """
    return text[:1].upper() + text[1:] if text else text


@dataclass
class Utterance:
    """A sentence plus the warrants for everything it asserts."""

    text: str
    kind: SpeechKind
    claims: list[Claim] = field(default_factory=list)


class FloorManager:
    def __init__(
        self,
        *,
        clock: Any,
        trace: Any,
        state: SessionState,
        registry: CallRegistry,
        on_action: Callable[[Any], None] | None = None,
    ) -> None:
        self.clock = clock
        self.trace = trace
        self.state = state
        self.registry = registry
        self._on_action = on_action

        self.transcript: list[Speak] = []
        self.speaking: Speak | None = None
        self._consecutive_fillers = 0
        self._spoken_calls: set[str] = set()

    # ------------------------------------------------------------ the gate

    def _unprovable(self, claim: Claim) -> str | None:
        """Why this claim cannot be made, or None if it can."""
        if claim.kind is ClaimKind.SLOT_VALUE:
            slot = self.state.slots.get(claim.subject)
            if slot is None:
                return f"slot {claim.subject!r} is not bound"
            if slot.value != claim.value:
                return f"slot {claim.subject!r} holds {slot.value!r}, not {claim.value!r}"
            if slot.confidence < SPEAK_THRESHOLD:
                return f"slot {claim.subject!r} confidence {slot.confidence:.2f} below threshold"
            return None

        if claim.kind is ClaimKind.IN_PROGRESS:
            record = self.registry.get(claim.subject)
            if record is None:
                return f"no call {claim.subject!r}"
            if not record.in_flight:
                return f"call {claim.subject!r} already settled as {record.outcome.value}"
            return None

        if claim.kind is ClaimKind.COMPLETED:
            record = self.registry.get(claim.subject)
            if record is None:
                return f"no call {claim.subject!r}"
            if record.outcome is not CallOutcome.COMPLETED_STILL_VALID:
                return f"call {claim.subject!r} settled as {record.outcome.value}, not a valid completion"
            return None

        if claim.kind is ClaimKind.PERCEPTION:
            if claim.confidence < SPEAK_THRESHOLD:
                return f"perception confidence {claim.confidence:.2f} below threshold"
            return None

        return f"unknown claim kind {claim.kind!r}"

    def _emit(self, utterance: Utterance) -> Speak | None:
        """Speak, unless a claim cannot be warranted or the filler budget is spent."""
        blocked = [(c, why) for c in utterance.claims if (why := self._unprovable(c))]
        if blocked:
            # A programmer error, not a user-facing one. Refuse and log rather
            # than utter something the kernel cannot back.
            self.trace.kernel(
                self.clock.now, "speech_blocked",
                text=utterance.text,
                kind=utterance.kind.value,
                reasons=[why for _, why in blocked],
            )
            return None

        if utterance.kind is SpeechKind.FILLER:
            if self._consecutive_fillers >= MAX_CONSECUTIVE_FILLERS:
                self.trace.kernel(
                    self.clock.now, "filler_suppressed",
                    consecutive=self._consecutive_fillers,
                )
                return None
            self._consecutive_fillers += 1
        else:
            self._consecutive_fillers = 0

        action = Speak(
            t=self.clock.now,
            text=utterance.text,
            kind=utterance.kind,
            claims=utterance.claims,
            interruptible=utterance.kind is not SpeechKind.REPAIR,
        )
        self.transcript.append(action)
        self.speaking = action
        self.trace.action(action.t, "speak", **action.model_dump(mode="json"))
        if self._on_action:
            self._on_action(action)
        return action

    # ------------------------------------------------------ what we can say

    def _slot_claims(self, names: Iterable[str]) -> tuple[list[str], list[Claim]]:
        """Phrases and warrants for every slot we are allowed to say out loud."""
        phrases, claims = [], []
        for name in names:
            slot = self.state.slots.get(name)
            if slot is None or slot.confidence < SPEAK_THRESHOLD:
                continue
            # Spoken form, not the canonical one: the user said "Mumbai", so we
            # say "Mumbai" even though the tool argument is "BOM".
            phrases.append(phrase_slot(name, slot.spoken))
            claims.append(
                Claim(
                    kind=ClaimKind.SLOT_VALUE,
                    subject=name,
                    value=slot.value,
                    warrant=f"bound at revision {slot.revision} from {slot.source.value}",
                    confidence=slot.confidence,
                )
            )
        return phrases, claims

    def acknowledge_dispatch(self, record: CallRecord) -> Speak | None:
        """"Searching flights to Mumbai on Tuesday."

        Names the slots that actually fed the call, so the user can hear a wrong
        slot immediately rather than after the tool returns. A generic "one
        moment" would be cheaper to write and worth less on every axis.
        """
        if record.call_id in self._spoken_calls:
            return None
        self._spoken_calls.add(record.call_id)

        phrases, claims = self._slot_claims(sorted(record.read_slots))
        body = describe_tool(record.tool)
        text = f"{body} {' '.join(phrases)}.".strip() if phrases else f"{body}."
        claims.append(
            Claim(
                kind=ClaimKind.IN_PROGRESS,
                subject=record.call_id,
                value=record.tool,
                warrant=f"dispatched at {record.dispatched_at:.0f} ms, still in flight",
            )
        )
        return self._emit(Utterance(text=_sentence_case(text), kind=SpeechKind.ACK, claims=claims))

    def acknowledge_slots(self, slots: Iterable[str]) -> Speak | None:
        """"Mumbai on Tuesday — got it."

        Emitted the moment a slot binds, mid-utterance, before any tool call is
        confirmed. Speculation is deliberately silent, so without this the agent
        stays quiet from the first word of a turn until end-of-turn — which on a
        two-chunk turn is hundreds of milliseconds of nothing, measured directly
        by the latency block.

        It claims only what it has: the slot values. It does not say we are
        searching, because at this point we may only be guessing.
        """
        phrases, claims = self._slot_claims(sorted(slots))
        if not phrases:
            return None
        return self._emit(
            Utterance(
                text=f"{_sentence_case(' '.join(phrases))} — got it.",
                kind=SpeechKind.ACK,
                claims=claims,
            )
        )

    def acknowledge_correction(self, slots: Iterable[str]) -> Speak | None:
        """"Mumbai instead — updating that."  Confirms the patch, claims nothing else."""
        phrases, claims = self._slot_claims(sorted(slots))
        if not phrases:
            return None
        text = f"{_sentence_case(' and '.join(phrases))} instead — updating that."
        return self._emit(Utterance(text=text, kind=SpeechKind.REPAIR, claims=claims))

    def _describe_args(self, record: CallRecord) -> str:
        """Name a call's subject in the words the user used.

        Falls back to the raw argument only when no slot holds it — otherwise
        the agent reads back "PNQ" at someone who said "Pune".
        """
        if not record.args:
            return record.tool.replace("_", " ")
        name, value = next(iter(sorted(record.args.items())))
        slot = self.state.slots.get(name)
        if slot is not None and slot.value == value:
            spoken = slot.spoken
        else:
            # The slot has moved on, so there is no surface form to recover.
            # Machine labels are snake_case and must not be read out that way.
            spoken = str(value).replace("_", " ")
        return phrase_slot(name, spoken)

    def acknowledge_media(self, modality: str) -> Speak | None:
        """"Let me take a look at that." — said before any decoding starts.

        This is what "process raw audio and frames **behind conversational
        acknowledgments**" means in practice. It is an ACK rather than a FILLER
        because it is substantive: it commits to an action we are actually
        taking and tells the user their photo arrived. Emitting a content-free
        "one moment" here instead cost the entire latency block on every
        multimodal scenario, because a filler is not a substantive response.

        It asserts nothing about the world, so it carries no claims — a
        statement of intent is not a completion claim.
        """
        text = "Let me listen to that." if modality == "audio" else "Let me take a look at that."
        return self._emit(Utterance(text=text, kind=SpeechKind.ACK))

    def acknowledge_suppression(self, record: CallRecord, prior: CallRecord | None) -> Speak | None:
        """"That one's already booked." — said when a duplicate is blocked.

        Suppressing the duplicate is correct and invisible, and invisibility is
        the problem: the user asked twice because we said nothing the first
        time, and silence makes them ask a third time. Observed directly in the
        double-booking scenario, where the agent correctly refused to book twice
        and then said nothing at all to two further requests.

        What it says depends on what the kernel can prove: an in-flight prior
        warrants "already going through", a settled one warrants "already done".
        Neither is available unless the corresponding CallRecord says so.
        """
        what = self._describe_args(record)
        action = describe_tool(record.tool).split()[0]

        if prior is not None and prior.in_flight:
            return self._emit(
                Utterance(
                    text=f"Already {action} {what} — hang on.",
                    kind=SpeechKind.PROGRESS,
                    claims=[
                        Claim(
                            kind=ClaimKind.IN_PROGRESS, subject=prior.call_id, value=prior.tool,
                            warrant=f"in flight since {prior.dispatched_at:.0f} ms",
                        )
                    ],
                )
            )

        if prior is not None and prior.outcome is CallOutcome.COMPLETED_STILL_VALID:
            return self._emit(
                Utterance(
                    text=f"That's already done — {what} is confirmed.",
                    kind=SpeechKind.REPAIR,
                    claims=[
                        Claim(
                            kind=ClaimKind.COMPLETED, subject=prior.call_id, value=prior.tool,
                            warrant=f"settled {prior.outcome.value} at {prior.settled_at:.0f} ms",
                        )
                    ],
                )
            )

        # We blocked it but cannot prove what happened to the original. Say the
        # only true thing: we are not doing it twice.
        return self._emit(
            Utterance(text=f"I've not repeated that — {what} was already requested.",
                      kind=SpeechKind.REPAIR)
        )

    def progress(self) -> Speak | None:
        """Narrate live work. Degrades to nothing rather than to a claim."""
        live = [c for c in self.registry.in_flight() if not c.speculative]
        if not live:
            return self._emit(Utterance(text="One moment.", kind=SpeechKind.FILLER))

        record = live[0]
        claims = [
            Claim(
                kind=ClaimKind.IN_PROGRESS,
                subject=record.call_id,
                value=record.tool,
                warrant=f"in flight since {record.dispatched_at:.0f} ms",
            )
        ]
        text = f"Still {describe_tool(record.tool)} — nearly there."
        return self._emit(Utterance(text=text, kind=SpeechKind.PROGRESS, claims=claims))

    def yield_floor(self, policy: InterruptionPolicy) -> Speak | None:
        """Stop talking. Deliberately silent: the user has the floor now.

        Recorded in the trace because 'prompt yielding' is only visible there,
        and because the *absence* of speech is as much a floor-management
        decision as speech is.
        """
        if policy.floor is not FloorPolicy.YIELD:
            return None
        self.trace.kernel(
            self.clock.now, "floor_yielded",
            was_speaking=self.speaking.text if self.speaking else None,
            policy=policy.to_payload(),
        )
        self.speaking = None
        return None

    def repeat_last(self) -> Speak | None:
        """Answer a repeat request from the transcript. Never re-runs a tool."""
        prior = [s for s in self.transcript if s.kind in (SpeechKind.ACK, SpeechKind.PROGRESS)]
        if not prior:
            return None
        last = prior[-1]
        self.trace.kernel(self.clock.now, "repeat_from_transcript", source_t=last.t)
        # Re-assert the original claims: they are re-verified by the gate, so a
        # repeat cannot resurrect a fact that has since stopped being true.
        return self._emit(
            Utterance(text=f"I said: {last.text}", kind=SpeechKind.ACK, claims=list(last.claims))
        )

    def disclose_uncertain_effect(self, record: CallRecord) -> Speak | None:
        """Say out loud that a state change may have happened and we cannot tell.

        Reached when a mutating call was cancelled and the manifest declares no
        verifier. Silence here is the failure mode that makes the snapshot lie.
        """
        what = self._describe_args(record)
        text = (
            f"One thing — I'd already started {describe_tool(record.tool)} {what} "
            "when you changed that, and I can't confirm whether it went through."
        )
        self.trace.kernel(
            self.clock.now, "disclosed_uncertain_effect",
            call_id=record.call_id, tool=record.tool, outcome=record.outcome.value,
        )
        return self._emit(Utterance(text=text, kind=SpeechKind.REPAIR))

    def disclose_compensation(self, record: CallRecord) -> Speak | None:
        """"I had already booked that — I've undone it." Truthful, and specific."""
        what = self._describe_args(record)
        text = (
            f"I'd already gone ahead with {what} before you changed your mind, "
            "so I've reversed it."
        )
        self.trace.kernel(
            self.clock.now, "disclosed_compensation",
            call_id=record.call_id, tool=record.tool,
        )
        return self._emit(Utterance(text=text, kind=SpeechKind.REPAIR))

    def clarify(
        self, question: str, *, slot: str | None = None, options: Iterable[str] = (), reason: str = "ambiguous"
    ) -> Clarify:
        """A specific question. Objective 5 wants ambiguous perceptions clarified,
        and a specific question scores where a generic failure does not."""
        action = Clarify(
            t=self.clock.now, question=question, slot=slot,
            options=list(options), reason=reason,
        )
        self.trace.action(action.t, "clarify", **action.model_dump(mode="json"))
        if self._on_action:
            self._on_action(action)
        return action

    def final(self, text: str, *, claims: Iterable[Claim] = (), grounded_on: Iterable[str] = ()) -> FinalResponse:
        """The final response, carrying the state snapshot.

        Unprovable claims are dropped rather than spoken, and the drop is traced.
        A final response is the one utterance that must never overstate.
        """
        kept, dropped = [], []
        for claim in claims:
            why = self._unprovable(claim)
            (dropped if why else kept).append((claim, why))

        if dropped:
            self.trace.kernel(
                self.clock.now, "final_claims_dropped",
                reasons=[why for _, why in dropped],
            )

        action = FinalResponse(
            t=self.clock.now,
            text=text,
            state=self.state.snapshot(),
            claims=[c for c, _ in kept],
            grounded_on=list(grounded_on),
        )
        self.trace.action(action.t, "final_response", **action.model_dump(mode="json"))
        if self._on_action:
            self._on_action(action)
        return action
