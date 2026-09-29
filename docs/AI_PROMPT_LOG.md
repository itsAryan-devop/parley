# AI prompt log

Feeds section 4 ("prompts used") of the `LangAI3.0_AI_Disclosure.docx` form.
Entries before 29 Sep 2026 are recorded per feature in `../DISCLOSURE.md` (F1–F6).
Every Claude Code session appends one entry (see `../CLAUDE.md`). Keys are never
pasted here.

---

## 2026-09-29 — Aryan (lead) — Claude Code (cloud session)

**Prompts (verbatim, abridged):**
- "tell me wht i need ot do for the livkit thing step by step tell me and what do
  i need to send to my teamame he will bacially drop whatver you give to his claude"
- "any heavy work which would cost more creds tell me i will send that work to him"
- "go trhough all the repo all .md files to fullextent and check eveythign to
  understand whst already built so we dont wste time doing whts already done"
- "we need to follwo this [updated guide + Theme 5 guide] and keep the ai prompt
  log maitained whatevr her es mentioned"
- "i need you to do it you will do eveythign on yoru own"
- "could you still do most of the work like building and updating the rest of the
  thing ... my friedn will become controvutor"
- "i wont buy the openai api i cant go for any paid options so lets shift to free
  things ... or i have ollama in my pc"

**Output:**
- Planning: LiveKit account steps, work split, repo audit. Findings: no
  LiveKit code existed; the endpointer's strongest features were computed from
  PARLEY's own manifest; docs describe the superseded harness.
- `parley/fdb/turn.py` — endpointer exposed as a LiveKit turn detector, with
  manifest-derived features for the 12 FDB-v3 tools.
- `parley/fdb/guard.py` — tool guard (duplicate refusal, commit grace window).
- `fdb/agent_parley.py` — fork of FDB-v3 `cascaded_agent.py`; Groq free backend
  by default, OpenAI optional.
- `tests/test_fdb.py` (18 tests), `reproduce.sh`, `requirements-fdb.txt`,
  `CLAUDE.md`, this log, `.gitignore` additions.

**Human decisions:** no paid APIs (switched default backend OpenAI → Groq free
tier); a teammate commits and pushes because the Claude GitHub App is not installed.
**Not yet verified:** no end-to-end benchmark run (container had no LiveKit/Groq
network access and no keys).
