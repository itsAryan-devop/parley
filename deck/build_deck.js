/**
 * Submission deck for PARLEY — Samsung PRISM Y2026, Theme 05.
 *
 *   node deck/build_deck.js
 *
 * Covers the five things the deck is required to contain: theme ID and team,
 * the problem in our own words, solution plus architecture diagram, tools and
 * stack, and innovation / results / limitations.
 *
 * The palette is lifted verbatim from viz/timeline.html, so the slides and the
 * tool we actually built read as one system rather than two.
 */

const pptxgen = require("pptxgenjs");
const path = require("path");

const OUT = path.join(__dirname, "ThaparPatiala_TEAM_Submission_ppt.pptx");

const BG = "0E1116", PANEL = "161B22", LINE = "2B323C";
const TEXT = "E6EDF3", MUTED = "8B949E", DIM = "6E7681";
const BLUE = "58A6FF", GREEN = "3FB950", PURPLE = "A371F7";
const RED = "F85149", AMBER = "D29922";

const H = "Cambria", B = "Calibri";

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";            // 13.3 x 7.5 — set BEFORE any slide
pres.author = "ThaparPatiala_TEAM";
pres.title = "PARLEY — Interruptible Real-Time Agents";

// ---------------------------------------------------------------- helpers

function slide(dark = true) {
  const s = pres.addSlide();
  s.background = { color: dark ? BG : "FFFFFF" };
  return s;
}

/** Title + optional kicker. No underline rule: it reads as template filler. */
function heading(s, title, kicker, onDark = true) {
  if (kicker) {
    s.addText(kicker.toUpperCase(), {
      x: 0.6, y: 0.42, w: 12.1, h: 0.3, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 12, bold: true, charSpacing: 2,
      color: onDark ? BLUE : "1C7293",
    });
  }
  s.addText(title, {
    x: 0.6, y: kicker ? 0.74 : 0.55, w: 12.1, h: 0.9, isTextBox: true, margin: 0,
    fontFace: H, fontSize: 34, bold: true, color: onDark ? TEXT : "1F2328",
  });
}

/** A content card. Tinted panel + optional shadow — never an edge stripe. */
function card(s, x, y, w, h, fill) {
  s.addShape(pres.ShapeType.roundRect, {
    x, y, w, h, rectRadius: 0.06,
    fill: { color: fill || PANEL },
    line: { color: LINE, width: 1 },
    shadow: { type: "outer", blur: 10, offset: 2, angle: 90, color: "000000", opacity: 0.35 },
  });
}

/** The recurring motif: a small colour chip, matching the timeline lanes. */
function chip(s, x, y, color, size) {
  const d = size || 0.16;
  s.addShape(pres.ShapeType.roundRect, {
    x, y, w: d, h: d, rectRadius: 0.4, fill: { color }, line: { color, width: 0 },
  });
}

function body(s, text, opts) {
  s.addText(text, Object.assign({
    isTextBox: true, margin: 0, fontFace: B, fontSize: 15, color: MUTED, lineSpacing: 22,
  }, opts));
}

function stat(s, x, y, w, value, label, color) {
  s.addText(value, {
    x, y, w, h: 0.85, isTextBox: true, margin: 0, align: "center",
    fontFace: H, fontSize: 46, bold: true, color: color || TEXT,
  });
  s.addText(label, {
    x, y: y + 0.85, w, h: 0.62, isTextBox: true, margin: 0, align: "center",
    fontFace: B, fontSize: 12, color: MUTED,
  });
}

// ================================================================= 1. title

{
  const s = slide();
  s.addText("PARLEY", {
    x: 0.9, y: 1.9, w: 8, h: 1.5, isTextBox: true, margin: 0,
    fontFace: H, fontSize: 82, bold: true, color: TEXT, charSpacing: 3,
  });
  s.addText("An interruption-native coordination kernel for full-duplex voice agents", {
    x: 0.95, y: 3.35, w: 8.2, h: 0.9, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 19, color: BLUE,
  });
  s.addText(
    "Samsung PRISM Y2026 GenAI Hackathon (3rd Edition)\n" +
    "Theme 05 — Interruptible Real-Time Agents\n\n" +
    "ThaparPatiala_TEAM · Thapar Institute of Engineering & Technology, Patiala",
    { x: 0.95, y: 4.45, w: 8.2, h: 1.5, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 14, color: MUTED, lineSpacing: 21 }
  );

  // A miniature of the timeline viewer: the product, as the cover art.
  const x0 = 9.6, y0 = 1.95, w = 3.0;
  const lanes = [
    { c: BLUE,   bars: [[0.00, 0.30], [0.42, 0.22]] },
    { c: GREEN,  bars: [[0.05, 0.26], [0.50, 0.34]] },
    { c: PURPLE, bars: [[0.02, 0.38]] },
    { c: RED,    bars: [[0.40, 0.16]] },
    { c: PURPLE, bars: [[0.44, 0.50]] },
  ];
  lanes.forEach((lane, i) => {
    const y = y0 + i * 0.46;
    s.addShape(pres.ShapeType.rect, {
      x: x0, y: y + 0.13, w, h: 0.02, fill: { color: LINE }, line: { width: 0 },
    });
    lane.bars.forEach(([a, len]) => {
      s.addShape(pres.ShapeType.roundRect, {
        x: x0 + a * w, y, w: len * w, h: 0.2, rectRadius: 0.5,
        fill: { color: lane.c }, line: { width: 0 },
      });
    });
  });
  s.addText("one timeline · one trace", {
    x: x0, y: y0 + 2.5, w, h: 0.3, isTextBox: true, margin: 0, align: "center",
    fontFace: B, fontSize: 11, italic: true, color: DIM,
  });

  s.addNotes(
    "PARLEY. Theme 5. The thesis in one line: interruption handling is a dataflow " +
    "problem, not a control-flow problem. Everything else follows from taking that seriously."
  );
}

// =============================================================== 2. problem

{
  const s = slide();
  heading(s, "People interrupt. Assistants take turns.", "The problem, in our own words");

  body(s,
    "A half-duplex assistant owns the microphone in phases: listen, think, speak — each " +
    "excluding the others. People do not work like that. They interrupt. They correct " +
    "themselves mid-noun-phrase. They change their mind while the machine is three tool " +
    "calls deep into the plan they just abandoned.",
    { x: 0.6, y: 1.85, w: 6.3, h: 1.9, fontSize: 16 });

  s.addText("The obvious fix is worse than the disease.", {
    x: 0.6, y: 3.75, w: 6.3, h: 0.4, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 17, bold: true, color: AMBER,
  });
  body(s,
    "“On interrupt, throw the plan away and start over” discards work that was still " +
    "valid, re-runs calls that already had side effects, and produces an assistant that " +
    "is responsive and wrong instead of slow and right.",
    { x: 0.6, y: 4.2, w: 6.3, h: 1.4 });

  card(s, 7.35, 1.75, 5.35, 4.4);
  s.addText("What every framework we surveyed does", {
    x: 7.7, y: 2.0, w: 4.7, h: 0.4, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 14, bold: true, color: TEXT,
  });

  const quotes = [
    ["Pipecat", "cancels any pending tasks in LLM and TTS"],
    ["LiveKit Agents", "stops the TTS stream and clears the output buffer"],
    ["TASTE2", "runs a fixed four-step teardown on barge-in"],
  ];
  quotes.forEach(([who, what], i) => {
    const y = 2.55 + i * 0.85;
    chip(s, 7.72, y + 0.06, RED);
    s.addText(who, {
      x: 8.0, y, w: 4.3, h: 0.28, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 13, bold: true, color: TEXT,
    });
    s.addText(what, {
      x: 8.0, y: y + 0.28, w: 4.3, h: 0.5, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 12, italic: true, color: MUTED,
    });
  });

  s.addText(
    "All three conflate STOP SPEAKING with STOP WORKING.\nThat conflation is the bug.",
    { x: 7.7, y: 5.2, w: 4.7, h: 0.8, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 13, bold: true, color: AMBER, lineSpacing: 19 }
  );

  s.addNotes(
    "Stopping the voice is right — the user no longer wants to hear the sentence. " +
    "Stopping the work is wrong — they may still want the flight search that is 80% done."
  );
}

// =============================================================== 3. insight

{
  const s = slide();
  heading(s, "Exactly one thing changed. Kill exactly what read it.", "The insight");

  body(s,
    "“…to Delhi — no, Mumbai.” One slot changed value. The right response is not to " +
    "re-plan; it is to work out which in-flight computations read that slot, cancel " +
    "precisely those, and leave everything else running.",
    { x: 0.6, y: 1.8, w: 12.1, h: 0.95, fontSize: 16 });

  card(s, 0.6, 2.95, 12.1, 2.55);

  // The track starts clear of the label column (which ends at 2.45") and stops
  // early enough that the longest right-hand note still lands inside the slide.
  const t0 = 2.6, tw = 7.5;
  const at = f => t0 + f * tw;

  // The interruption, as a vertical marker with a caption above the lanes.
  s.addShape(pres.ShapeType.rect, {
    x: at(0.46), y: 3.45, w: 0.025, h: 1.75, fill: { color: BLUE }, line: { width: 0 },
  });
  s.addText("“no, Mumbai”", {
    x: at(0.46) - 0.75, y: 3.12, w: 1.5, h: 0.3, isTextBox: true, margin: 0,
    align: "center", fontFace: B, fontSize: 12, bold: true, color: BLUE,
  });

  const rows = [
    { label: "search_flights", a: 0.04, b: 0.46, colour: RED,
      note: "read destination  →  cancelled", dashed: false },
    { label: "search_hotels", a: 0.10, b: 0.78, colour: GREEN,
      note: "never read it  →  survives", dashed: false },
    { label: "search_flights", a: 0.48, b: 0.92, colour: PURPLE,
      note: "re-planned on the new value", dashed: true },
  ];
  rows.forEach((r, i) => {
    const y = 3.58 + i * 0.56;
    s.addText(r.label, {
      x: 0.85, y: y - 0.02, w: 1.6, h: 0.26, isTextBox: true, margin: 0,
      fontFace: "Courier New", fontSize: 10.5, color: MUTED,
    });
    s.addShape(pres.ShapeType.roundRect, {
      x: at(r.a), y, w: (r.b - r.a) * tw, h: 0.24, rectRadius: 0.5,
      fill: { color: r.colour }, line: { width: 0 },
      transparency: r.dashed ? 45 : 0,
    });
    s.addText(r.note, {
      x: at(r.b) + 0.12, y: y - 0.02, w: 2.9, h: 0.28, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 11, color: r.colour,
    });
  });

  s.addText(
    "A pipeline flush kills the hotel search too — and gains nothing for it.",
    { x: 0.85, y: 5.02, w: 11.6, h: 0.3, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 12, italic: true, color: DIM }
  );

  body(s,
    "Over-cancelling scores as badly as under-cancelling: task completion (40%) falls " +
    "while nothing is gained on interruption recovery (35%).",
    { x: 0.6, y: 5.75, w: 12.1, h: 0.6, fontSize: 14, color: TEXT });

  s.addNotes(
    "This is the whole thesis. Every in-flight call records which slots fed its " +
    "arguments, so a slot correction invalidates exactly its readers — a dict lookup, " +
    "not a judgement call."
  );
}

// ================================================================ 4. matrix

{
  const s = slide();
  heading(s, "An interruption forces two decisions, not one", "Why the taxonomy is a matrix");

  body(s,
    "Floor policy is what happens to our voice. Work policy is what happens to our " +
    "in-flight tool calls. They are independent. The naive system is the diagonal — " +
    "yield implies cancel everything — and the score lives off it.",
    { x: 0.6, y: 1.72, w: 12.1, h: 0.85, fontSize: 15 });

  const colX = [3.05, 6.3, 9.55], colW = 3.1;
  const rowY = [3.35, 4.35, 5.35], rowH = 0.88;

  ["KEEP ALL WORK", "SELECTIVE CANCEL", "CANCEL ALL WORK"].forEach((h, i) => {
    s.addText(h, {
      x: colX[i], y: 2.85, w: colW, h: 0.35, isTextBox: true, margin: 0, align: "center",
      fontFace: B, fontSize: 11.5, bold: true, charSpacing: 1, color: BLUE,
    });
  });
  ["CONTINUE\nspeaking", "ADAPT\nutterance", "YIELD\nfloor"].forEach((h, i) => {
    s.addText(h, {
      x: 0.6, y: rowY[i], w: 2.25, h: rowH, isTextBox: true, margin: 0,
      align: "right", valign: "middle",
      fontFace: B, fontSize: 12, bold: true, color: BLUE, lineSpacing: 16,
    });
  });

  const cells = [
    [0, 0, "SELF_REPAIR", "“book the… uh… the Tuesday one”", GREEN],
    [1, 0, "REFINEMENT", "“make it morning flights only”", GREEN],
    [2, 0, "BARGE_IN · REPEAT · BACKCHANNEL", "“hold on” · “say that again” · “mhm”", AMBER],
    [2, 1, "SLOT_CORRECTION", "“…to Delhi — no, Mumbai”", PURPLE],
    [2, 2, "GOAL_SWITCH", "“forget flights, find a hotel”", RED],
  ];
  cells.forEach(([r, c, name, example, colour]) => {
    card(s, colX[c], rowY[r], colW, rowH, PANEL);
    chip(s, colX[c] + 0.17, rowY[r] + 0.19, colour, 0.13);
    s.addText(name, {
      x: colX[c] + 0.38, y: rowY[r] + 0.12, w: colW - 0.55, h: 0.3, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 11, bold: true, color: TEXT,
    });
    s.addText(example, {
      x: colX[c] + 0.17, y: rowY[r] + 0.44, w: colW - 0.34, h: 0.4, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 10.5, italic: true, color: MUTED,
    });
  });

  s.addText(
    "The amber cell is where every framework we surveyed is wrong: yield the floor, keep every call. " +
    "Cancelling there throws away valid work — and re-running it afterwards is the “stale re-run” the 35% block penalises.",
    { x: 0.6, y: 6.42, w: 12.1, h: 0.6, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 12.5, color: AMBER, lineSpacing: 17 }
  );

  s.addNotes(
    "Floor verbs come from the overlapping-speech literature (arXiv 2609.13117). The " +
    "work axis is ours. Telling SELF_REPAIR from SLOT_CORRECTION uses Shriberg's " +
    "disfluency structure: they differ in what the repair does to the value."
  );
}

// ========================================================== 5. architecture

{
  const s = slide();
  heading(s, "Architecture", "Fast path, slow path, one trace");

  const lanes = [
    { y: 1.72, h: 0.52, title: "EVENTS IN", colour: BLUE,
      items: ["transcript chunks", "WAV clips", "PNG frames", "interrupt signals", "tool results", "tool manifest"] },
    { y: 2.52, h: 1.02, title: "FAST PATH — pure Python, zero inference", colour: GREEN,
      items: ["Interpreter  rules + learned classifier over one feature vector",
              "FloorManager  provable-speech gate · filler rationing"] },
    { y: 3.82, h: 1.22, title: "COORDINATION KERNEL — 75% of the score is decided here", colour: PURPLE,
      items: ["CallRegistry  slot → in-flight readers index",
              "Dispatcher  cancellation-safe dispatch · join · supersede",
              "IdempotencyLedger  claimed BEFORE dispatch · verify → compensate → or disclose"] },
    { y: 5.32, h: 0.82, title: "SLOW PATH — never blocks the fast path", colour: AMBER,
      items: ["Planner  manifest-driven · speculative before end-of-turn",
              "Perception  PNG/WAV → features → calibrated softmax + abstention"] },
    { y: 6.42, h: 0.52, title: "ACTIONS OUT", colour: BLUE,
      items: ["speak", "tool_call", "cancel", "clarify", "final response + state snapshot"] },
  ];

  lanes.forEach(lane => {
    card(s, 0.6, lane.y, 9.5, lane.h);
    chip(s, 0.82, lane.y + 0.16, lane.colour, 0.13);
    s.addText(lane.title, {
      x: 1.03, y: lane.y + 0.09, w: 8.9, h: 0.28, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 11.5, bold: true, charSpacing: 0.6, color: lane.colour,
    });
    const text = lane.h < 0.6 ? lane.items.join("   ·   ") : lane.items.join("\n");
    s.addText(text, {
      x: 1.03, y: lane.y + 0.36, w: 8.9, h: lane.h - 0.42, isTextBox: true, margin: 0,
      fontFace: lane.h < 0.6 ? B : "Courier New",
      fontSize: lane.h < 0.6 ? 11 : 10.5, color: MUTED, lineSpacing: 15,
    });
  });

  // Arrows down the left-hand gutter.
  [2.30, 3.60, 5.10, 6.20].forEach(y => {
    s.addShape(pres.ShapeType.rect, {
      x: 5.32, y, w: 0.02, h: 0.18, fill: { color: DIM }, line: { width: 0 },
    });
  });

  card(s, 10.35, 1.72, 2.35, 5.22, "12181F");
  s.addText("ONE TRACE", {
    x: 10.55, y: 1.95, w: 1.95, h: 0.3, isTextBox: true, margin: 0, align: "center",
    fontFace: B, fontSize: 11.5, bold: true, charSpacing: 1, color: TEXT,
  });
  s.addText(
    "Every event, action and kernel decision, append-only, on one virtual clock.\n\n" +
    "Scoring reads trace logs — so the trace is not telemetry. It is the scoring surface.\n\n" +
    "An action that is not logged did not happen.",
    { x: 10.55, y: 2.45, w: 1.95, h: 3.4, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 10.5, color: MUTED, lineSpacing: 15 }
  );
  s.addText("viz/timeline.html replays it", {
    x: 10.55, y: 6.35, w: 1.95, h: 0.4, isTextBox: true, margin: 0, align: "center",
    fontFace: B, fontSize: 10, italic: true, color: DIM,
  });

  s.addNotes(
    "Cancellation happens BEFORE state is mutated. Patch the slot first and the " +
    "in-flight call looks consistent with the new value, so it survives when it " +
    "should die — invisible in a passing demo, fatal on the hidden set."
  );
}

// ============================================================ 6. guarantees

{
  const s = slide();
  heading(s, "Four guarantees in the kernel", "Where 75% of every scenario is decided");

  const items = [
    [GREEN, "Nothing leaves the trace unaccounted for",
      "Dispatch is wrapped so the cancellation path writes its outcome on the way out — plus a done-callback, because a task cancelled before its first step never runs its body at all."],
    [PURPLE, "State changes are claimed before dispatch",
      "Claiming after is a race that two re-plans five milliseconds apart will lose. Failed and compensated are retryable; succeeded and in-flight are not."],
    [BLUE, "Confirming an in-flight call joins it",
      "Rather than issuing a second one. Without join, speculation manufactures exactly the duplicate state changes the safety block penalises."],
    [AMBER, "A cancel is a request, not a fact",
      "Cancelling a state-modifying call yields CANCELLED_UNCERTAIN — the cancel may have landed after the effect committed. Resolved by probing a declared verifier, compensating via a declared inverse, or saying out loud that we cannot tell."],
  ];

  items.forEach(([colour, title, text], i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = 0.6 + col * 6.25, y = 1.85 + row * 2.4;
    card(s, x, y, 5.85, 2.15);
    s.addShape(pres.ShapeType.ellipse, {
      x: x + 0.28, y: y + 0.28, w: 0.42, h: 0.42,
      fill: { color: colour }, line: { width: 0 },
    });
    s.addText(String(i + 1), {
      x: x + 0.28, y: y + 0.3, w: 0.42, h: 0.38, isTextBox: true, margin: 0,
      align: "center", fontFace: B, fontSize: 15, bold: true, color: BG,
    });
    s.addText(title, {
      x: x + 0.85, y: y + 0.28, w: 4.75, h: 0.48, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 14.5, bold: true, color: TEXT,
    });
    s.addText(text, {
      x: x + 0.3, y: y + 0.88, w: 5.28, h: 1.1, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 11.5, color: MUTED, lineSpacing: 16,
    });
  });

  s.addText(
    "The fourth is the one most designs collapse to a boolean — and it is how a state snapshot silently stops matching the world.",
    { x: 0.6, y: 6.6, w: 12.1, h: 0.4, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 12.5, italic: true, color: AMBER }
  );
}

// ============================================================ 7. multimodal

{
  const s = slide();
  heading(s, "Perception that knows when it doesn’t know", "Multimodal — the largest single lever");

  body(s,
    "Half the hidden set is audio or visual, at a 1.5× multiplier. Frames and clips are " +
    "acknowledged immediately and decoded as background tasks — “behind conversational " +
    "acknowledgments” is a concurrency requirement, not a tone.",
    { x: 0.6, y: 1.75, w: 6.2, h: 1.1, fontSize: 15 });

  s.addText("The problem nobody mentions", {
    x: 0.6, y: 3.05, w: 6.2, h: 0.35, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 15, bold: true, color: AMBER,
  });
  body(s,
    "A logistic regression is badly overconfident off-distribution. Ours labelled " +
    "blown-out photographs and frames with two LEDs lit at ~0.9 confidence — exactly " +
    "the confidently-wrong perception objective 5 penalises twice.",
    { x: 0.6, y: 3.48, w: 6.2, h: 1.1, fontSize: 13.5 });

  const fixes = [
    ["Temperature calibration, constrained to T ≥ 1",
     "Unconstrained it fitted to 0.50 — a perfectly separated validation set drives the NLL optimum to zero. Calibration may soften; never sharpen."],
    ["Mahalanobis abstention",
     "Thresholded by how far genuine in-distribution data actually reaches. A false abstention costs one question; a false confident answer costs task completion and truthfulness."],
  ];
  fixes.forEach(([t, d], i) => {
    const y = 4.72 + i * 1.08;
    chip(s, 0.62, y + 0.05, GREEN, 0.13);
    s.addText(t, {
      x: 0.85, y, w: 5.95, h: 0.28, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 12.5, bold: true, color: TEXT,
    });
    s.addText(d, {
      x: 0.85, y: y + 0.3, w: 5.95, h: 0.65, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 11, color: MUTED, lineSpacing: 15,
    });
  });

  card(s, 7.2, 1.75, 5.5, 5.0);
  s.addText("Three outcomes, not two", {
    x: 7.5, y: 2.0, w: 4.9, h: 0.35, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 15, bold: true, color: TEXT,
  });

  const outcomes = [
    [GREEN, "ANSWER", "clear winner", "“That’s the power LED — here’s what solid red means.”"],
    [AMBER, "ASK", "two labels too close to separate",
     "“I can’t tell from the picture — is it the power LED or the WAN LED?”"],
    [RED, "DECLINE", "nothing resembling the training distribution",
     "“I couldn’t make that out — could you describe it?”"],
  ];
  outcomes.forEach(([colour, name, when, line], i) => {
    const y = 2.55 + i * 1.28;
    chip(s, 7.5, y + 0.05, colour, 0.14);
    s.addText(name, {
      x: 7.75, y, w: 1.4, h: 0.28, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 13, bold: true, color: colour,
    });
    s.addText(when, {
      x: 9.0, y: y + 0.02, w: 3.4, h: 0.28, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 10.5, italic: true, color: DIM,
    });
    s.addText(line, {
      x: 7.75, y: y + 0.34, w: 4.65, h: 0.75, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 11.5, color: MUTED, lineSpacing: 16,
    });
  });

  s.addText(
    "100% of undecidable frames and 11/12 undecidable clips are refused rather than guessed.",
    { x: 7.5, y: 6.25, w: 4.9, h: 0.4, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 11.5, bold: true, color: GREEN, lineSpacing: 15 }
  );

  s.addNotes(
    "Undecidable media is generated separately and held out of training entirely. " +
    "Teaching a classifier to pick one label for a frame with two LEDs lit would train " +
    "away the behaviour that scores."
  );
}

// =============================================================== 8. results

{
  const s = slide();
  heading(s, "Results", "Scored strictly from trace logs");

  card(s, 0.6, 1.8, 12.1, 1.75);
  stat(s, 0.9, 2.05, 2.6, "22/22", "public scenarios pass\nevery declared check", GREEN);
  stat(s, 3.7, 2.05, 2.6, "109.5", "mean score\n(quality multiplier on 100)", TEXT);
  stat(s, 6.5, 2.05, 2.6, "1980", "perturbed runs holding\nevery invariant", BLUE);
  stat(s, 9.3, 2.05, 3.1, "254", "tests, including every\nscenario end-to-end", PURPLE);

  s.addText("Per component, averaged across the suite", {
    x: 0.6, y: 3.8, w: 6.0, h: 0.35, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 14, bold: true, color: TEXT,
  });

  const comps = [
    ["Task completion", "40%", 1.0],
    ["Interruption recovery", "35%", 1.0],
    ["Response latency", "15%", 1.0],
    ["Safety & protocol", "10%", 1.0],
  ];
  comps.forEach(([name, weight, score], i) => {
    const y = 4.3 + i * 0.58;
    s.addText(name, {
      x: 0.6, y, w: 2.5, h: 0.3, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 12.5, color: TEXT,
    });
    s.addText(weight, {
      x: 3.15, y, w: 0.55, h: 0.3, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 11.5, color: DIM,
    });
    s.addShape(pres.ShapeType.roundRect, {
      x: 3.85, y: y + 0.05, w: 2.0, h: 0.18, rectRadius: 0.5,
      fill: { color: PANEL }, line: { color: LINE, width: 1 },
    });
    s.addShape(pres.ShapeType.roundRect, {
      x: 3.85, y: y + 0.05, w: 2.0 * score, h: 0.18, rectRadius: 0.5,
      fill: { color: GREEN }, line: { width: 0 },
    });
    s.addText(score.toFixed(2), {
      x: 5.95, y, w: 0.6, h: 0.3, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 11.5, color: GREEN,
    });
  });

  card(s, 6.95, 3.75, 5.75, 3.0);
  s.addText("By modality", {
    x: 7.25, y: 3.98, w: 5.15, h: 0.33, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 14, bold: true, color: TEXT,
  });
  s.addText("The hidden set applies 1.5× to multimodal scenarios, and half of it is multimodal.", {
    x: 7.25, y: 4.34, w: 5.15, h: 0.5, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 11, color: MUTED, lineSpacing: 15,
  });
  [["text", 13, 110.4, BLUE], ["audio", 5, 108.0, PURPLE], ["visual", 4, 108.8, GREEN]].forEach(
    ([name, n, mean, colour], i) => {
      const y = 5.0 + i * 0.56;
      chip(s, 7.27, y + 0.06, colour, 0.13);
      s.addText(`${name}`, {
        x: 7.52, y, w: 1.3, h: 0.3, isTextBox: true, margin: 0,
        fontFace: B, fontSize: 12.5, color: TEXT,
      });
      s.addText(`${n} scenarios`, {
        x: 8.8, y: y + 0.02, w: 1.6, h: 0.3, isTextBox: true, margin: 0,
        fontFace: B, fontSize: 11, color: DIM,
      });
      s.addText(mean.toFixed(1), {
        x: 10.5, y, w: 1.0, h: 0.3, isTextBox: true, margin: 0,
        align: "right", fontFace: B, fontSize: 13, bold: true, color: colour,
      });
    }
  );
  s.addText("22 scenarios: 59% text / 23% audio / 18% visual.", {
    x: 7.25, y: 6.32, w: 5.15, h: 0.3, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 10.5, italic: true, color: DIM,
  });
}

// ========================================================== 9. adversarial

{
  const s = slide();
  heading(s, "You cannot test your way out of a hidden set", "Adversarial timing");

  body(s,
    "The hidden set is ~60 scenarios of “edge cases and adversarial timing”. Passing the " +
    "twenty-two we wrote proves little — they are the cases we thought of. So the fuzzer " +
    "jitters timestamps, scales tool latency, moves commit points, collapses events onto " +
    "identical timestamps, injects faults and truncates sessions — then asserts only " +
    "invariants, never expectations.",
    { x: 0.6, y: 1.75, w: 12.1, h: 1.25, fontSize: 15 });

  card(s, 0.6, 3.1, 5.85, 3.3);
  s.addText("It found two bugs no hand-written test could reach", {
    x: 0.88, y: 3.35, w: 5.3, h: 0.55, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 14, bold: true, color: TEXT,
  });

  const bugs = [
    ["A call vanished from the trace",
     "Two chunks landed on one timestamp, so a speculative call was superseded in the instant it was created. create_task schedules rather than runs — the body never executed, and the “every exit writes an outcome” guarantee lives inside that body."],
    ["A genuine double-booking",
     "The idempotency key included intent. Booking before the intent resolved and again afterwards gave two keys for one action — two reservations for one seat."],
  ];
  bugs.forEach(([t, d], i) => {
    const y = 4.0 + i * 1.22;
    chip(s, 0.9, y + 0.05, RED, 0.13);
    s.addText(t, {
      x: 1.13, y, w: 5.05, h: 0.28, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 12.5, bold: true, color: TEXT,
    });
    s.addText(d, {
      x: 1.13, y: y + 0.3, w: 5.05, h: 0.85, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 10.5, color: MUTED, lineSpacing: 14,
    });
  });

  card(s, 6.85, 3.1, 5.85, 3.3);
  s.addText("The invariants", {
    x: 7.13, y: 3.35, w: 5.3, h: 0.35, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 14, bold: true, color: TEXT,
  });
  const invs = [
    "no duplicate state-changing effect, ever",
    "no call left pending or missing from the trace",
    "no effect left unresolved and unmentioned",
    "nothing claimed that cannot be warranted",
    "no speculation of a state-modifying tool",
    "no run past the 120 s cap",
  ];
  invs.forEach((inv, i) => {
    const y = 3.88 + i * 0.36;
    chip(s, 7.15, y + 0.05, GREEN, 0.11);
    s.addText(inv, {
      x: 7.38, y, w: 5.1, h: 0.3, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 11.5, color: MUTED,
    });
  });
  s.addText("Every run held, across thousands of schedules.", {
    x: 7.13, y: 6.02, w: 5.3, h: 0.3, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 13, bold: true, color: GREEN,
  });

  s.addNotes(
    "Expectations are deliberately not checked under perturbation: move an " +
    "interruption 200 ms later and the call has already finished, so cancelling it " +
    "becomes impossible rather than wrong."
  );
}

// ================================================================== 10. ML

{
  const s = slide();
  heading(s, "Where machine learning earns its place — and where it doesn’t",
          "Reported as measured");

  body(s,
    "Two learned components, both trained offline, both shipped as a few kilobytes of " +
    "JSON, both inferring in numpy. scikit-learn is a development dependency and never " +
    "runs at scenario time — the 120 s cap and the ban on runtime downloads decide that.",
    { x: 0.6, y: 1.75, w: 12.1, h: 0.9, fontSize: 15 });

  card(s, 0.6, 2.85, 6.6, 2.5);
  s.addText("Interruption classifier: rules vs model", {
    x: 0.9, y: 3.05, w: 6.0, h: 0.35, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 13.5, bold: true, color: TEXT,
  });
  [["", "RULES", "MODEL"],
   ["held-out phrasings, clean", "1.000", "0.945"],
   ["held-out + ASR noise", "0.891", "0.876"],
   ["arbitrated ensemble (test)", "0.907", "0.922"]].forEach((row, i) => {
    const y = 3.5 + i * 0.44;
    const header = i === 0;
    s.addText(row[0], {
      x: 0.9, y, w: 3.4, h: 0.3, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 11.5, bold: header, color: header ? DIM : MUTED,
    });
    [1, 2].forEach(c => {
      const best = !header && ((i < 3 && c === 1) || (i === 3 && c === 2));
      s.addText(row[c], {
        x: 4.35 + (c - 1) * 1.35, y, w: 1.25, h: 0.3, isTextBox: true, margin: 0,
        align: "right", fontFace: B, fontSize: 11.5,
        bold: header || best, color: header ? DIM : (best ? GREEN : MUTED),
      });
    });
  });

  card(s, 7.6, 2.85, 5.1, 2.5);
  s.addText("The honest conclusion", {
    x: 7.88, y: 3.05, w: 4.5, h: 0.35, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 13.5, bold: true, color: AMBER,
  });
  s.addText(
    "The model does not beat the rules. The feature vector was hand-designed to be " +
    "discriminative, so a linear model over it fits a boundary the rules already encode.\n\n" +
    "It ships only because the arbitrated ensemble beats rules alone — on a split used " +
    "neither for fitting nor for choosing the arbitration policy. A +1.5 point margin is " +
    "small, and is reported as small.",
    { x: 7.88, y: 3.5, w: 4.5, h: 1.7, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 11, color: MUTED, lineSpacing: 15 }
  );

  card(s, 0.6, 5.6, 12.1, 1.3, "12181F");
  s.addText("Chasing the gap was worth more than the model", {
    x: 0.9, y: 5.78, w: 11.5, h: 0.32, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 13, bold: true, color: GREEN,
  });
  s.addText(
    "It surfaced a real extraction bug — “the Tuesday one” binding party_size = 1, a false slot in the scored snapshot — and the " +
    "finding that an inserted “uh” derailed rule ordering entirely. Stripping hesitation before matching semantic cues lifted rule " +
    "accuracy under noise from 0.809 to 0.891. With audio at 30% of the hidden set, that is the most valuable thing the detour produced.",
    { x: 0.9, y: 6.14, w: 11.5, h: 0.72, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 11, color: MUTED, lineSpacing: 15 }
  );
}

// =============================================================== 11. stack

{
  const s = slide();
  heading(s, "Tools and tech stack", "Deliberately thin");

  const groups = [
    [BLUE, "Runtime", ["Python 3.10–3.12 (pinned 3.12)", "pydantic 2 — typed wire contracts",
                       "numpy — all model inference", "Pillow — frame decoding",
                       "asyncio — subclassed event loop"]],
    [PURPLE, "Development only", ["scikit-learn — offline training", "pytest + pytest-asyncio — 233 tests",
                                  "Docker — reproducible build", "Node + pptxgenjs — this deck"]],
    [GREEN, "Built, not imported", ["virtual-clock harness + trace", "deterministic mock environment",
                                    "rubric scorer", "invariant timing fuzzer",
                                    "swimlane trace viewer"]],
  ];

  groups.forEach(([colour, title, items], i) => {
    const x = 0.6 + i * 4.15;
    card(s, x, 1.85, 3.85, 3.5);
    chip(s, x + 0.28, 2.13, colour, 0.15);
    s.addText(title, {
      x: x + 0.52, y: 2.05, w: 3.1, h: 0.32, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 14, bold: true, color: TEXT,
    });
    s.addText(items.map((t, j) => ({
      text: t, options: { bullet: true, breakLine: j < items.length - 1 },
    })), {
      x: x + 0.3, y: 2.55, w: 3.3, h: 2.6, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 11.5, color: MUTED, paraSpaceAfter: 7,
    });
  });

  card(s, 0.6, 5.6, 12.1, 1.35, "12181F");
  s.addText("No LLM in the loop, and that is a design decision", {
    x: 0.9, y: 5.8, w: 11.5, h: 0.32, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 13.5, bold: true, color: AMBER,
  });
  s.addText(
    "Task completion and interruption recovery are 75% of the score and both are properties of what got executed and what got " +
    "cancelled — not of model quality. Latency is a further 15%, and inference time is pure cost against it. A brilliant LLM that " +
    "double-books scores worse than a kernel that cannot. So the kernel is the product, and the language model is a component it is allowed to distrust.",
    { x: 0.9, y: 6.16, w: 11.5, h: 0.72, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 11, color: MUTED, lineSpacing: 15 }
  );
}

// ================================================ 12. innovation & limits

{
  const s = slide();
  heading(s, "Innovation, limitations, and what comes next", "Honest close");

  card(s, 0.6, 1.8, 5.85, 3.05);
  s.addText("What appears to be ours", {
    x: 0.88, y: 2.0, w: 5.3, h: 0.33, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 14, bold: true, color: GREEN,
  });
  [
    "Floor × work as two independent axes — the only way BACKCHANNEL and REPEAT_REQUEST are expressible at all",
    "CANCELLED_UNCERTAIN, resolved by verifier probe, compensator, or admission",
    "A provable-speech gate that puts every claim’s warrant into the trace",
    "Working multimodal grounding with calibrated abstention",
    "Invariant fuzzing as the defence against a hidden set nobody can see",
  ].forEach((t, i) => {
    const y = 2.45 + i * 0.48;
    chip(s, 0.9, y + 0.05, GREEN, 0.11);
    s.addText(t, {
      x: 1.13, y, w: 5.05, h: 0.45, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 10.5, color: MUTED, lineSpacing: 14,
    });
  });

  card(s, 6.85, 1.8, 5.85, 3.05);
  s.addText("Limitations, stated plainly", {
    x: 7.13, y: 2.0, w: 5.3, h: 0.33, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 14, bold: true, color: AMBER,
  });
  [
    "The official evaluation kit was never released publicly; ours is a spec-faithful replica, and every interface assumption needs re-checking against the real one",
    "The interruption corpus is synthetic — generated from the taxonomy, not real user speech",
    "Perception is trained on generated media; real device photographs drop into the same pipeline but are unmeasured",
    "Dependency-aware cancellation is not unique to us — other Theme 5 entries are working the same intuition",
  ].forEach((t, i) => {
    const y = 2.45 + i * 0.6;
    chip(s, 7.15, y + 0.05, AMBER, 0.11);
    s.addText(t, {
      x: 7.38, y, w: 5.05, h: 0.57, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 10.5, color: MUTED, lineSpacing: 14,
    });
  });

  card(s, 0.6, 5.1, 12.1, 1.75, "12181F");
  s.addText("Taken further as a worklet", {
    x: 0.9, y: 5.3, w: 11.5, h: 0.32, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 14, bold: true, color: BLUE,
  });
  s.addText(
    "The kernel is model-agnostic and manifest-driven, so it sits underneath an existing assistant rather than replacing it. " +
    "The immediate extensions are a real ASR front-end in place of transcript replay, on-device perception weights for the " +
    "troubleshooting case, and the trace viewer as a standing debugging tool — full-duplex agents are currently very hard to debug, " +
    "and being able to see an interruption being absorbed is useful well beyond this hackathon.",
    { x: 0.9, y: 5.66, w: 11.5, h: 1.0, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 11.5, color: MUTED, lineSpacing: 16 }
  );

  s.addNotes(
    "Close on the limitations slide deliberately. The jury asks about trade-offs, and " +
    "a team that already knows its own weak points answers better than one defending a claim."
  );
}

pres.writeFile({ fileName: OUT }).then(() => console.log("wrote " + OUT));
