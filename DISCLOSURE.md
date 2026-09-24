# AI Usage Disclosure — PARLEY

**Samsung PRISM Y2026 GenAI Hackathon (3rd Edition) · Theme 05**

Running log backing the mandatory `LangAI3.0_AI_Disclosure.docx`. Section 4 of that
form asks for **the prompts used**, so they are recorded here as work happens rather
than reconstructed at submission.

| Field | Value |
|---|---|
| Team Name | `ThaparPatiala_<TEAM>` *(to confirm)* |
| Project / Product Name | PARLEY |
| Organization / Institution | Thapar Institute of Engineering & Technology, Patiala |
| Submission Date | *(to confirm — deck says 25 Sep 2026)* |

---

## 2. AI Usage Declaration

**Did your team use any AI in developing this project?** — **Yes.**

**Tool used:** Anthropic Claude (Claude Code, model `claude-opus-5`), run as an
interactive coding agent with filesystem, shell, and web-search access.

---

## 3. Purpose of AI Usage

| Category | Used | Detail |
|---|---|---|
| Idea generation / brainstorming | Yes | Interruption taxonomy, the floor×work policy matrix, choice of differentiators |
| Code generation or assistance | Yes | All Python in `parley/`, `harness/`, `tests/`, `scripts/` drafted by the assistant under human direction |
| UI / UX design | Partial | Trace-timeline viewer only. No product UI — UI polish is out of scope per the guide |
| Content creation | Yes | `docs/`, `README.md`, this file |
| Data analysis | Yes | Classifier evaluation, ablation between rule and model paths |
| Testing / debugging | Yes | Test suites authored by the assistant; several real bugs found this way (listed below) |
| Other | Yes | Literature search (arXiv, GitHub) for prior art; recorded in `docs/RESEARCH.md` |

**Human direction.** The brief was set by the team: theme, deadline, the instruction
to research prior art between build steps, to verify each step before continuing, and
to incorporate ML where it is genuinely warranted. Architecture decisions were
proposed by the assistant and are documented with their rationale in `docs/DESIGN.md`
so they can be defended in the 15 Oct Q&A.

---

## 4. Feature Origin Classification

Every row: **AI-Generated** unless stated. "Modification" records what was changed
after the first draft, including changes forced by failing tests.

---

### F1 — Protocol layer (typed events, actions, state snapshot, tool manifest)

- **Origin:** AI-Generated
- **Files:** `parley/protocol/*`
- **Prompt used:** *"see we are doing this hackathon these files contain the rules and
  theme and all the research done yet now what you need to do is start building —
  divide the build into steps, after each step check it's working then do more
  research… we need something fancy, something unique which actually works and has
  real life value and remember we need to follow all the things of theme 5"*
- **Output summary:** Pydantic models for the two queues of the interface contract,
  a `SessionState` with a monotonic revision and per-slot revisions, and a
  dialect-tolerant manifest parser.
- **Modification:** Two corrections after testing. (a) `Trace.emit` collided whenever
  a payload key was named `kind`/`name`/`t`; header parameters made positional-only.
  (b) `is_stale` conflated "slot never existed" with "slot was cleared", marking
  healthy calls stale; cleared slots now leave a revision-carrying tombstone.

### F2 — Virtual-clock harness

- **Origin:** AI-Generated
- **Files:** `harness/clock.py`, `harness/trace.py`, `harness/mockenv/*`
- **Prompt used:** as F1 (same standing instruction; the evaluation kit had not been
  released, so a spec-faithful replica was built from the Theme 5 guide's description).
- **Output summary:** Event loop subclass that overrides `loop.time()` and advances
  virtual time to the next scheduled callback only when nothing is runnable, so
  `asyncio.sleep`/`wait_for`/timeouts are virtualised without any clock-aware API.
  Mock environment with deterministic latency and fault injection.
- **Modification:** Windows' ~15.6 ms clock resolution batched all timers inside that
  window, collapsing events 5 ms apart — exactly the adversarial timing the hidden set
  uses. Clock resolution pinned to 1 ns. The mock environment's **commit point** (a
  mutating tool's side effect lands partway through its latency window) was added
  deliberately so that `COMPLETED_NOW_STALE` is reachable in tests.

### F3 — Coordination kernel

- **Origin:** AI-Generated
- **Files:** `parley/kernel/*`
- **Prompt used:** as F1.
- **Output summary:** Slot-dataflow cancellation, idempotency ledger claimed before
  dispatch, speculation with join semantics, and a four-outcome effect ledger whose
  `CANCELLED_UNCERTAIN` state is resolved by probing a manifest-declared verifier and
  compensating via a manifest-declared inverse.
- **Modification:** `CANCELLED_UNCERTAIN` was not in the first design. It was added on
  the reasoning that an agent cancelling a state-modifying call genuinely cannot know
  whether the effect committed, and recording it as clean is what desynchronises the
  snapshot from the world.

### F4 — NLU: extraction and seven-way interruption classification

- **Origin:** AI-Generated, grounded in published prior art (see `docs/RESEARCH.md`)
- **Files:** `parley/agent/lexicon.py`, `parley/agent/nlu.py`
- **Prompt used:** as F1, plus the standing instruction *"keep doing research like
  similar projects same domain… use github and research papers to widen your
  understanding"*.
- **Output summary:** Manifest-derived intent cues (set difference over tool
  vocabularies, so unseen tools need no code change) and a rule classifier over a
  20-feature vector. The `SELF_REPAIR` vs `SLOT_CORRECTION` discriminator is taken
  from Shriberg's reparandum/interregnum/repair structure.
- **Modification:** Three bugs found by tests — punctuation hid multi-word cues;
  "hold on" is a floor grab as a phrase but not as tokens; "wait" means different
  things over speech and in silence. A fourth found by held-out evaluation: the word
  "one" in "the Tuesday one" was being extracted as `party_size=1`, injecting a false
  slot into the scored snapshot.

### F5 — Learned interruption classifier

- **Origin:** **Both.** Feature design and the decision rule are human-directed and
  literature-grounded; corpus generation, training and evaluation are AI-Generated.
- **Files:** `parley/agent/model.py`, `parley/agent/corpus.py`,
  `scripts/train_classifier.py`, `parley/agent/interruption_model.json`
- **Prompt used:** *"if possible add ml things if needed or plausible"*
- **Output summary:** Multinomial logistic regression over the same feature vector the
  rules use. Trained offline with scikit-learn; fitted coefficients exported to ~6 kB
  of JSON; inference is a numpy matmul and softmax. No runtime download.
- **Honest limitations, recorded deliberately:**
  - The corpus is **synthetic**, generated from templates. It is not real user speech
    and no claim is made that it is.
  - Because the features were hand-designed to be discriminative, **a linear model
    over them cannot beat the rules that designed them.** On clean held-out phrasings
    both score ~1.00; under ASR-style noise the rules alone score **0.891** and the
    model alone **0.876**.
  - The model ships because the **arbitrated ensemble** beats rules alone —
    **0.922 vs 0.907** on a noisy test split whose seed was used neither for fitting
    nor for choosing the arbitration policy. That margin is small and is reported as
    small.
  - The model is **never** allowed to trigger a destructive branch (`GOAL_SWITCH`,
    `SLOT_CORRECTION`) on its own, because a false positive there cancels real work.

---

## 5. Ethical & Compliance Confirmation

- AI usage complies with the hackathon guidelines and policies — **Yes**
- No proprietary or copyrighted data misused — **I Agree**

Specifics: no Samsung proprietary material was ingested. Device troubleshooting
content in `harness/mockenv/world.py` is written from scratch — error codes and LED
states are facts, and the remediation text is our own wording, not manual text.
The flight and hotel catalogues are procedurally generated, not scraped. Papers
consulted are cited by title and URL in `docs/RESEARCH.md` and are paraphrased, not
reproduced.

---

## 6. Declaration & Sign-Off

| Field | Value |
|---|---|
| Name of Team Representative | *(to fill)* |
| Role | *(to fill)* |
| Signature | *(to fill)* |
| Date | *(to fill)* |
