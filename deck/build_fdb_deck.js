/**
 * FDB-v3 submission deck for PARLEY — Samsung PRISM Y2026, Theme 05. <= 8 slides.
 *
 *   node deck/build_fdb_deck.js        (needs pptxgenjs: npm install pptxgenjs)
 *
 * Every number on these slides is copied from docs/FDB_RESULTS.md. Edit the
 * RESULTS table below when that file changes; nothing here is computed.
 * Palette and fonts match the earlier deck (deck/build_deck.js).
 */

const pptxgen = require("pptxgenjs");
const path = require("path");

const OUT = path.join(__dirname, "ThaparPatiala_TEAM_FDBv3_ppt.pptx");

const BG = "0E1116", PANEL = "161B22", LINE = "2B323C";
const TEXT = "E6EDF3", MUTED = "8B949E";
const BLUE = "58A6FF", GREEN = "3FB950", PURPLE = "A371F7";
const RED = "F85149", AMBER = "D29922", INK = "1F2328", SOFT = "F3F4F6";
const H = "Cambria", B = "Calibri";

// Copied from docs/FDB_RESULTS.md. [label, strict pass %, detail]
const RESULTS = [
  ["5 clips · Orpheus TTS", 80, "4/5 · 10.1 s"],
  ["5 clips · local Piper TTS", 80, "4/5 · 5.7 s"],
  ["5 clips · ablation (ours off)", 100, "5/5 · 5.0 s"],
  ["10 spread · before fixes", 30, "3/10 · 6.1 s"],
  ["10 spread · with fixes", 40, "4/10 · 6.8 s"],
  ["10 spread · gpt-oss-20b", 60, "6/10 · 6.3 s"],
  ["10 spread · 20b + STT-aware guard", 50, "5/10 · 5.6 s (1 clip lost to benchmark client crash)"],
];

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
pres.author = "ThaparPatiala_TEAM";
pres.title = "PARLEY — listens through self-corrections before it acts";

function slide(dark) {
  const s = pres.addSlide();
  s.background = { color: dark ? BG : "FFFFFF" };
  return s;
}

function heading(s, title, kicker, dark) {
  if (kicker) {
    s.addText(kicker.toUpperCase(), {
      x: 0.6, y: 0.45, w: 12, h: 0.35, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 12, bold: true, charSpacing: 2, color: dark ? BLUE : "0969DA",
    });
  }
  s.addText(title, {
    x: 0.6, y: 0.8, w: 12.1, h: 0.9, isTextBox: true, margin: 0,
    fontFace: H, fontSize: 30, bold: true, color: dark ? TEXT : INK,
  });
}

function body(s, text, opts) {
  s.addText(text, Object.assign({
    isTextBox: true, margin: 0, fontFace: B, fontSize: 15, color: INK, valign: "top",
  }, opts));
}

function card(s, x, y, w, h, fill) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x, y, w, h, rectRadius: 0.12, fill: { color: fill }, line: { color: fill },
  });
}

// 1 — title --------------------------------------------------------------
{
  const s = slide(true);
  s.addText("Samsung PRISM Y2026 GenAI Hackathon · Theme 05 — Interruptible Real-Time Agents", {
    x: 0.8, y: 0.8, w: 11.8, h: 0.4, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 14, color: MUTED,
  });
  s.addText("PARLEY", {
    x: 0.8, y: 2.0, w: 11.8, h: 1.3, isTextBox: true, margin: 0,
    fontFace: H, fontSize: 72, bold: true, color: TEXT,
  });
  s.addText("A LiveKit voice agent that listens through self-corrections before it acts.", {
    x: 0.8, y: 3.35, w: 11.5, h: 0.8, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 24, color: BLUE,
  });
  s.addText("“…move my mortgage autopay to checking — wait, no — make it savings.”", {
    x: 0.8, y: 4.6, w: 11.5, h: 0.6, isTextBox: true, margin: 0,
    fontFace: H, fontSize: 20, italic: true, color: MUTED,
  });
  s.addText("Team ThaparPatiala_<TEAM> · Thapar Institute of Engineering & Technology, Patiala", {
    x: 0.8, y: 6.5, w: 11.8, h: 0.4, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 13, color: MUTED,
  });
  s.addNotes("Scored on Full-Duplex-Bench v3 (arXiv 2604.04847): 100 real human recordings, 12 tools, 4 domains.");
}

// 2 — problem ----------------------------------------------------------
{
  const s = slide(false);
  heading(s, "One changed word, two tool calls, a failed task", "The problem", false);
  body(s, [
    { text: "People don't speak in finished commands. They correct themselves, pause to find an order number, and spell IDs one character at a time.", options: { breakLine: true } },
    { text: " ", options: { breakLine: true } },
    { text: "A voice agent that calls a tool the moment it hears a plausible value acts on the abandoned one, then fires a second call for the correction. On FDB-v3 that extra call fails the scenario.", options: {} },
  ], { x: 0.6, y: 2.0, w: 6.4, h: 3.5, fontSize: 17 });
  const stats = [
    [">40%", "of self-correction scenarios failed even by GPT-Realtime, the best system in the FDB-v3 paper"],
    ["0.45", "Pass@1 of the paper's stock cascaded agent (Whisper → GPT-4o → TTS), with the gpt-4o judge"],
  ];
  stats.forEach(([big, small], i) => {
    const y = 1.95 + i * 2.35;
    card(s, 7.5, y, 5.2, 2.05, SOFT);
    s.addText(big, { x: 7.8, y: y + 0.2, w: 4.8, h: 0.9, isTextBox: true, margin: 0,
      fontFace: H, fontSize: 48, bold: true, color: RED });
    s.addText(small, { x: 7.8, y: y + 1.1, w: 4.7, h: 0.85, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 13, color: INK });
  });
  s.addText("Source: Full-Duplex-Bench-v3, arXiv 2604.04847 (Apr 2026).", {
    x: 0.6, y: 6.8, w: 12, h: 0.3, isTextBox: true, margin: 0, fontFace: B, fontSize: 11, color: MUTED,
  });
}

// 3 — architecture -------------------------------------------------------
{
  const s = slide(true);
  heading(s, "Cancel before effect", "Architecture", true);
  const boxes = [
    ["User audio", "LiveKit room", 0.5, MUTED],
    ["Silero VAD\n+ Whisper STT", "Groq", 2.55, MUTED],
    ["PARLEY turn\ndetector", "keeps listening", 4.6, GREEN],
    ["gpt-oss-120b", "Groq, tool calls", 6.65, MUTED],
    ["ID canonicaliser\n+ ToolGuard", "hold · dedupe", 8.7, GREEN],
    ["FDB-v3 APIs →\nPiper TTS", "local voice", 10.75, MUTED],
  ];
  boxes.forEach(([t, sub, x, c]) => {
    card(s, x, 2.3, 1.85, 1.55, PANEL);
    s.addText(t, { x: x + 0.1, y: 2.4, w: 1.65, h: 0.9, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 14, bold: true, color: c === GREEN ? GREEN : TEXT, align: "center", valign: "middle" });
    s.addText(sub, { x: x + 0.1, y: 3.3, w: 1.65, h: 0.4, isTextBox: true, margin: 0,
      fontFace: B, fontSize: 11, color: MUTED, align: "center" });
    if (x < 10) {
      s.addShape(pres.shapes.LINE, { x: x + 1.87, y: 3.07, w: 0.16, h: 0, line: { color: MUTED, width: 2, endArrowType: "triangle" } });
    }
  });
  const notes = [
    ["Turn detector", "Our learned endpointer holds the turn while the words end on “no”, “I mean”, a dangling word or a trailing “…”.", GREEN],
    ["ToolGuard", "Each call waits 0.3 s and runs only if the user is silent and nothing they said is still in STT — else “not executed”. Identical (tool, args) runs once.", GREEN],
    ["Fork of the stock agent", "Same 12 tools, schemas and logs as FDB-v3’s cascaded agent, so the benchmark scores it like the baseline.", BLUE],
  ];
  notes.forEach(([t, d, c], i) => {
    const x = 0.5 + i * 4.2;
    s.addText(t, { x, y: 4.45, w: 3.9, h: 0.4, isTextBox: true, margin: 0, fontFace: B, fontSize: 16, bold: true, color: c });
    s.addText(d, { x, y: 4.9, w: 3.9, h: 1.5, isTextBox: true, margin: 0, fontFace: B, fontSize: 13, color: TEXT, valign: "top" });
  });
}

// 4 — what's new -------------------------------------------------------------
{
  const s = slide(false);
  heading(s, "What PARLEY adds, and the failure each one fixes", "What's new", false);
  const items = [
    ["Guard waits for silence", "A call is deferred while the user is talking, or while their last words are still being transcribed.", "Self-correction clip: two calls (checking, savings) → one call on savings"],
    ["Turn detector reads Whisper right", "“I’m” and a pause-capitalised word are not proper nouns; a trailing “…” is not a finished value.", "Trailing-off turn: 0.986 “done” → 0.011"],
    ["Spoken IDs canonicalised", "“P-5-2” → “P52”, only in ID arguments whose pieces are ≤ 3 characters. The prompt version was ignored by the LLM.", "Spelled-ID clip now passes exact match"],
    ["Local TTS, patient retries", "Piper speaks on CPU (Groq's free TTS allows 100 requests a day). LLM retries 6 × 3 s instead of giving up after ~7 s.", "Mean latency 10.1 s → 5.7 s on the same clips"],
  ];
  items.forEach(([t, d, e], i) => {
    const x = 0.6 + (i % 2) * 6.2, y = 1.95 + Math.floor(i / 2) * 2.45;
    card(s, x, y, 5.9, 2.2, SOFT);
    s.addText(t, { x: x + 0.3, y: y + 0.2, w: 5.3, h: 0.45, isTextBox: true, margin: 0, fontFace: B, fontSize: 17, bold: true, color: INK });
    s.addText(d, { x: x + 0.3, y: y + 0.7, w: 5.3, h: 0.85, isTextBox: true, margin: 0, fontFace: B, fontSize: 13, color: INK, valign: "top" });
    s.addText(e, { x: x + 0.3, y: y + 1.6, w: 5.3, h: 0.45, isTextBox: true, margin: 0, fontFace: B, fontSize: 13, bold: true, color: "1A7F37" });
  });
}

// 5 — results ---------------------------------------------------------
{
  const s = slide(false);
  heading(s, "Results: small samples, reported as measured", "FDB-v3 results", false);
  s.addChart(pres.charts.BAR, [{
    name: "Strict pass %", labels: RESULTS.map(r => r[0]), values: RESULTS.map(r => r[1]),
  }], {
    x: 0.5, y: 1.85, w: 7.4, h: 4.6, barDir: "bar",
    chartColors: ["0969DA"], showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: '0"%"',
    dataLabelFontSize: 12, catAxisLabelFontSize: 12, valAxisHidden: true, valAxisMaxVal: 110,
    valGridLine: { style: "none" }, catGridLine: { style: "none" }, showLegend: false,
    showTitle: true, title: "Strict pass rate (exact match)", titleFontSize: 14,
    catAxisOrientation: "maxMin",
  });
  const caveats = [
    "Exact-match scoring only: no OpenAI key, so FDB-v3's gpt-4o judge did not run.",
    "5–10 clips per run. The same clip has flipped between pass and fail on replays. The 20b rows exist because 120b's daily quota was spent.",
    "Measured without a GPU, on a local LiveKit server; latency is from that box.",
    "No 100-clip run yet: Groq's free tier caps each tool-calling model at 200K tokens/day, and a clip costs ~4K.",
  ];
  s.addText("Read before comparing", { x: 8.3, y: 1.9, w: 4.5, h: 0.4, isTextBox: true, margin: 0, fontFace: B, fontSize: 16, bold: true, color: RED });
  s.addText(caveats.map((c, i) => ({ text: c, options: { bullet: true, breakLine: i < caveats.length - 1, paraSpaceAfter: 8 } })), {
    x: 8.3, y: 2.4, w: 4.5, h: 4.2, isTextBox: true, margin: 0, fontFace: B, fontSize: 13, color: INK, valign: "top",
  });
  s.addNotes(RESULTS.map(r => `${r[0]}: ${r[2]}`).join("\n") + "\nFull log: docs/FDB_RESULTS.md");
}

// 6 — honest findings -------------------------------------------------------
{
  const s = slide(true);
  heading(s, "What the ablation actually says", "Honest findings", true);
  const cols = [
    ["On 5 easy clips: no benefit", "With our turn detector and guard switched off, the same 5 clips scored 5/5 vs our 4/5, and 0.6 s faster. The differing clip flips on replays, so it is noise. None of those clips has a self-correction.", AMBER],
    ["Where it does work", "On the spread set, the guard deferred 4–5 premature calls per run. The self-correction clip went from calling the abandoned value to one call on the corrected one.", GREEN],
    ["What no agent change fixes", "When the spoken city or destination never reaches the transcript, the agent correctly asks for it. Exact match also rejects synonyms such as filter names that a judge might accept.", BLUE],
  ];
  cols.forEach(([t, d, c], i) => {
    const x = 0.6 + i * 4.15;
    card(s, x, 2.0, 3.85, 3.5, PANEL);
    s.addText(t, { x: x + 0.3, y: 2.25, w: 3.3, h: 0.8, isTextBox: true, margin: 0, fontFace: B, fontSize: 17, bold: true, color: c, valign: "top" });
    s.addText(d, { x: x + 0.3, y: 3.1, w: 3.3, h: 3.0, isTextBox: true, margin: 0, fontFace: B, fontSize: 14, color: TEXT, valign: "top" });
  });
}

// 7 — extension -------------------------------------------------------------
{
  const s = slide(false);
  heading(s, "Extension: troubleshooting through the camera", "20% use case", false);
  body(s, [
    { text: "A separate live LiveKit agent (fdb/agent_extension.py): the user points their phone camera at a TV, router or washer and asks what's wrong. One tool reads the latest frame: error code, LED colour, or on-screen text.", options: { breakLine: true } },
    { text: " ", options: { breakLine: true } },
    { text: "Three outcomes only: a diagnosis with the manual's steps, a question naming both candidates, or a request for a better shot. It asks rather than guesses.", options: { breakLine: true } },
    { text: " ", options: { breakLine: true } },
    { text: "Real phone frames, held out: 5/9 diagnosed (every real 'No Signal' TV), 4 abstained, 0 wrong. The colour model was trained on synthetic frames only; routers and washers on camera are not yet proven. Not part of the FDB-v3 scores.", options: { bold: true } },
  ], { x: 0.6, y: 2.0, w: 6.6, h: 4.3, fontSize: 15 });
  card(s, 7.7, 2.0, 5.0, 4.3, SOFT);
  s.addText([
    { text: "Demo (video)", options: { bold: true, fontSize: 18, breakLine: true } },
    { text: " ", options: { breakLine: true } },
    { text: "1. Camera away from the device → it asks to see it, no guess", options: { breakLine: true } },
    { text: "2. A real TV showing 'No Signal' → diagnosed from the screen text, fix read out", options: { breakLine: true } },
    { text: "3. Groq's free voice quota runs out → offline Piper voice takes over, no drop", options: {} },
  ], { x: 8.0, y: 2.25, w: 4.4, h: 3.8, isTextBox: true, margin: 0, fontFace: B, fontSize: 14, color: INK, valign: "top" });
}

// 8 — what's next --------------------------------------------------------------
{
  const s = slide(true);
  heading(s, "What's next", "Roadmap", true);
  const steps = [
    ["A full 100-clip run", "Needs more LLM quota than the free tier gives in a day, or the LLM served locally on the evaluation GPU (e.g. gpt-oss-20b under vLLM)."],
    ["Score with the gpt-4o judge", "Our numbers are exact-match; the paper's are judged. A like-for-like comparison needs one judged run."],
    ["Audio-aware turn-taking", "Our endpointer reads words only. An open audio model such as Pipecat’s Smart Turn v3 (BSD-2, ~12 ms on CPU) could hear a trailing-off tone the transcript hides."],
    ["Ablation on self-correction clips", "Measure the guard and turn detector on the 21 self-correction scenarios they were built for, not on easy clips."],
  ];
  steps.forEach(([t, d], i) => {
    const y = 1.95 + i * 1.2;
    s.addShape(pres.shapes.OVAL, { x: 0.6, y: y + 0.05, w: 0.5, h: 0.5, fill: { color: BLUE }, line: { color: BLUE } });
    s.addText(String(i + 1), { x: 0.6, y: y + 0.05, w: 0.5, h: 0.5, isTextBox: true, margin: 0, fontFace: B, fontSize: 16, bold: true, color: BG, align: "center", valign: "middle" });
    s.addText(t, { x: 1.35, y, w: 11.2, h: 0.4, isTextBox: true, margin: 0, fontFace: B, fontSize: 17, bold: true, color: TEXT });
    s.addText(d, { x: 1.35, y: y + 0.42, w: 11.2, h: 0.6, isTextBox: true, margin: 0, fontFace: B, fontSize: 13, color: MUTED, valign: "top" });
  });
}

pres.writeFile({ fileName: OUT }).then(f => console.log("wrote", f));
