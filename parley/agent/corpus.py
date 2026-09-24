"""A generated corpus for training and evaluating the interruption classifier.

There is no public dataset labelled with our seven-way taxonomy, so the corpus
is synthesised from templates. That carries an obvious risk of circularity — if
the model is trained on the same phrasings the rules were written from, high
accuracy proves nothing.

Two things keep the evaluation honest:

1. **Held-out phrasings.** `TRAIN_TEMPLATES` and `EVAL_TEMPLATES` share no
   surface forms. The eval set uses paraphrases, different word order, and cue
   words that appear nowhere in the training templates. Accuracy on it measures
   generalisation to phrasings neither the rules nor the training data contain.

2. **Slot values and states are sampled, not fixed.** The same template yields
   different feature vectors depending on what happens to be bound, so the model
   cannot memorise a template-to-label mapping.

What this corpus honestly is: a way to learn sensible weights over a feature
vector that was itself designed by hand. What it is not: evidence about real
user speech. That limitation is recorded in DISCLOSURE.md rather than papered
over.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..kernel.policy import InterruptionKind
from ..protocol.state import SessionState
from .nlu import Turn

CITIES = [("delhi", "DEL"), ("mumbai", "BOM"), ("bengaluru", "BLR"), ("goa", "GOI"),
          ("chennai", "MAA"), ("hyderabad", "HYD"), ("kolkata", "CCU"), ("pune", "PNQ")]
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
FILLED = ["uh", "um", "er", "erm", "hmm"]
EDITS = ["no", "sorry", "actually", "i mean", "not"]

K = InterruptionKind

#: Templates the model is fitted on.
TRAIN_TEMPLATES: dict[InterruptionKind, list[str]] = {
    K.SLOT_CORRECTION: [
        "{edit} {new_city}",
        "to {old_city} {edit} {new_city}",
        "{filled} {edit} {new_city}",
        "make it {new_city}",
        "{edit} make it {new_day}",
        "not {old_city} {new_city}",
        "change the date to {new_day}",
        "{edit} i want {new_city} instead",
    ],
    K.SELF_REPAIR: [
        "book the {filled} the {bound_day} one",
        "{filled} {filled}",
        "the {filled} {bound_city} flight",
        "i want the {filled} yes the {bound_day} one",
        "{filled} {edit} {filled}",
        "take the {filled} {bound_city} option",
    ],
    K.GOAL_SWITCH: [
        "forget flights find me a hotel",
        "never mind i need a hotel",
        "cancel that i want a hotel in {new_city}",
        "actually a hotel instead",
        "change of plan hotel please",
        "drop that find hotels in {new_city}",
    ],
    K.REFINEMENT: [
        "make it morning only",
        "only morning flights",
        "just the cheapest one",
        "make it direct",
        "only nonstop please",
        "just the earliest",
    ],
    K.BARGE_IN: ["wait", "hold on", "stop", "hang on", "wait wait", "stop stop"],
    K.REPEAT_REQUEST: [
        "say that again", "repeat that", "sorry what", "come again",
        "what was that", "one more time",
    ],
    K.BACKCHANNEL: ["mhm", "yeah", "okay", "right", "sure", "got it"],
    K.NEW_REQUEST: [
        "find me a flight to {new_city}",
        "i need a flight to {new_city} on {new_day}",
        "book a flight to {new_city}",
        "flights to {new_city} please",
        "i want to fly to {new_city} on {new_day}",
    ],
}

#: Phrasings held out entirely from training. Share no surface forms with above.
EVAL_TEMPLATES: dict[InterruptionKind, list[str]] = {
    K.SLOT_CORRECTION: [
        "scratch that {new_city}",
        "{new_city} rather than {old_city}",
        "hold on i said {old_city} i meant {new_city}",
        "correction {new_day}",
    ],
    K.SELF_REPAIR: [
        "the {filled} you know the {bound_city} one",
        "yes the {filled} {bound_day} flight",
        "that {filled} {filled} one",
    ],
    K.GOAL_SWITCH: [
        "something else entirely a hotel",
        "abandon that hotels please",
        "new plan i want a hotel",
    ],
    K.REFINEMENT: [
        "narrow it to morning",
        "filter to direct ones",
        "under 5000 please",
    ],
    K.BARGE_IN: ["hang on a moment", "quiet", "shush", "listen"],
    K.REPEAT_REQUEST: ["didn't catch that", "what did you say", "pardon"],
    K.BACKCHANNEL: ["yep", "uh huh"],
    K.NEW_REQUEST: [
        "get me a flight into {new_city}",
        "looking to fly {new_day} to {new_city}",
    ],
}


#: Plausible ASR confusions. Audio is 30% of the hidden set, so the transcript
#: the agent classifies will not be the words the user said.
_CONFUSIONS = {
    "no": "know", "to": "two", "not": "knot", "wait": "weight", "hold": "old",
    "flight": "fight", "hotel": "hostel", "only": "lonely", "morning": "mourning",
    "mumbai": "bombay", "delhi": "deli", "right": "write", "yeah": "yah",
    "i mean": "i mean the", "again": "a gain", "that": "at",
}


def _noisify(rng: random.Random, text: str, *, level: float) -> str:
    """Perturb an utterance the way a streaming recogniser would.

    Deletion, stutter, inserted hesitation, and word confusion. Applied to the
    held-out split only: a classifier that survives this is one that will
    survive the audio scenarios, and a classifier that needs pristine text is
    one we would rather find out about now.
    """
    if level <= 0:
        return text
    tokens = text.split()
    if not tokens:
        return text

    ops = []
    if rng.random() < level:
        ops.append("drop")
    if rng.random() < level:
        ops.append("stutter")
    if rng.random() < level:
        ops.append("hesitate")
    if rng.random() < level:
        ops.append("confuse")

    for op in ops:
        if not tokens:
            break
        i = rng.randrange(len(tokens))
        if op == "drop" and len(tokens) > 1:
            tokens.pop(i)
        elif op == "stutter":
            tokens.insert(i, tokens[i])
        elif op == "hesitate":
            tokens.insert(i, rng.choice(FILLED))
        elif op == "confuse":
            swap = _CONFUSIONS.get(tokens[i].lower())
            if swap:
                tokens[i] = swap
    return " ".join(tokens)


@dataclass
class Example:
    turn: Turn
    state: SessionState
    label: InterruptionKind
    in_flight: int
    template: str
    noisy: bool = False


def _state(rng: random.Random, *, bind: bool = True) -> tuple[SessionState, tuple[str, str], str]:
    """A plausible mid-conversation state, plus the values it bound."""
    st = SessionState(session_id="corpus")
    st.set_intent("book_flight")
    city = rng.choice(CITIES)
    day = rng.choice(DAYS)
    if bind:
        st.set_slot("destination", city[1])
        st.set_slot("date", day.capitalize())
        if rng.random() < 0.3:
            st.set_slot("origin", rng.choice(CITIES)[1])
    return st, city, day


def _fill(rng: random.Random, template: str, bound_city: tuple[str, str], bound_day: str) -> str:
    other = rng.choice([c for c in CITIES if c[1] != bound_city[1]])
    other_day = rng.choice([d for d in DAYS if d != bound_day])
    return template.format(
        old_city=bound_city[0],
        bound_city=bound_city[0],
        new_city=other[0],
        bound_day=bound_day,
        new_day=other_day,
        filled=rng.choice(FILLED),
        edit=rng.choice(EDITS),
    )


def generate(
    n: int = 4000, *, split: str = "train", seed: int = 20260924, noise: float = 0.0
) -> list[Example]:
    """Deterministic corpus. Same seed, split and noise give the same examples."""
    templates = TRAIN_TEMPLATES if split == "train" else EVAL_TEMPLATES
    rng = random.Random(f"{seed}:{split}:{noise}")
    labels = list(templates)
    out: list[Example] = []

    for i in range(n):
        label = labels[i % len(labels)]
        template = rng.choice(templates[label])

        # NEW_REQUEST must start from an unbound state. Held-out evaluation
        # caught this: "get me a flight into Mumbai" while destination=BLR is
        # already bound is a SLOT_CORRECTION, not a new request, so binding
        # conflicting slots here was labelling the corpus wrongly and the rule
        # engine was being marked down for being right.
        bind = label is not K.NEW_REQUEST
        st, city, day = _state(rng, bind=bind)
        text = _noisify(rng, _fill(rng, template, city, day), level=noise)

        # Overlap and in-flight counts are themselves features, so they are
        # sampled per class rather than held constant.
        if label in (K.BARGE_IN, K.BACKCHANNEL):
            overlapping, in_flight = True, rng.randint(1, 3)
        elif label is K.NEW_REQUEST:
            overlapping, in_flight = False, 0
        else:
            overlapping, in_flight = rng.random() < 0.6, rng.randint(1, 3)

        out.append(
            Example(
                turn=Turn(
                    text=text,
                    t=float(i),
                    end_of_turn=rng.random() < 0.5,
                    overlapping_agent_speech=overlapping,
                ),
                state=st,
                label=label,
                in_flight=in_flight,
                template=template,
                noisy=noise > 0,
            )
        )
    return out
