# Submission checklist

> **"Teams NOT following the submission guideline would lead to direct
> disqualification."** — that warning appears twice in the deck. Everything on
> this page is a way to lose without writing bad code.

Work top to bottom. **Tag last.**

---

## 1. Decide these four things first

Everything downstream depends on them, and three are currently placeholders.

| | Status | Notes |
|---|---|---|
| **Team name** `CollegeName_TeamName` | ⚠️ `ThaparPatiala_<TEAM>` throughout | Exact nomenclature. Substitute everywhere — see §2. |
| **Deadline** | ✅ **30 Sep**, confirmed by the team | The deck PDF we were given reads 25 Sep 11:59 PM; the team has confirmed 30 Sep twice and that is what we are working to. Noted only so nobody re-derives the discrepancy from the deck and panics. |
| **Team representative** | ⚠️ blank | Needed for the disclosure sign-off. |
| **Demo video host** | ⚠️ not recorded | YouTube (unlisted) or Drive. Link must be inside the tagged commit. |

---

## 2. Substitute the team name

**One command.** It appears in seven places across five files, plus the deck's
filename and its embedded author metadata. A single `ThaparPatiala_<TEAM>` left in
the tagged commit looks careless to a jury reading the README first, and doing
this by hand at 11 p.m. is exactly how one gets missed.

```bash
python scripts/set_team_name.py --check
```

Reports where the placeholder still is and changes nothing. Then, with the real
name:

```bash
python scripts/set_team_name.py ThaparPatiala_Parley
node deck/build_deck.js
python scripts/set_team_name.py --check
```

The script validates the `CollegeName_TeamName` shape before touching anything —
the deck calls that exact nomenclature a disqualification risk — writes UTF-8
without a BOM, and uses `git mv` for the deck so history follows the file. It
does not commit and does not tag; those stay manual.

Two files keep the placeholder on purpose, because they are the instructions:
this checklist and `scripts/set_team_name.py` itself. `--check` labels them so
they are not mistaken for misses.

---

## 3. Verify the build from clean

**Done — this is no longer an open risk.** Docker Desktop's Linux engine would
not start here for most of the project; the cause turned out to be orphaned
AF_UNIX socket files Windows could not delete, not anything about the image. See
`docs/BUILD_LOG.md` for the diagnosis. With the engine up, all three advertised
commands were run and pass:

```bash
docker build -t parley .                       # exit 0, "weights present", "warm-up OK"
docker run --rm parley                         # 30/30 scenarios, mean 109.2
docker run --rm parley pytest                  # 348 tests, 21 skipped, 0 failed
docker run --rm parley python scripts/fuzz.py --trials 10   # 300/300 invariants held
```

`docker images` reports **375 MB**. The 21 skips are the optional `voice` and
`vision` extras, which the image deliberately does not install — the scored
engine needs neither, and `tests/test_asr.py`, `tests/test_ocr.py` and one
multimodal test skip via `importorskip` / `ocr.available()` rather than failing.

> **A number we got wrong first.** An earlier revision of this file and the
> README claimed 153 MB. That was read from `docker images` while the image was
> still unpacking and never re-checked; the settled figure was 641 MB. Re-reading
> it also exposed something worth fixing: scipy and scikit-learn were 188 MB of
> that — 29% of the image — to carry a library no scenario and no test imports.
> It fits the classifiers offline and the weights ship as JSON. Dropping it from
> the runtime install took the image to 375 MB with 30/30 and the full suite
> unchanged. Quote the `docker images` figure; `docker inspect .Size` reports 88 MB
> for the same image because of BuildKit's attestation manifests, and the two are
> not comparable.

**Still worth re-running on a clean clone before tagging**, because the build
above ran against a working tree rather than a fresh checkout:

```bash
git clone <repo> /tmp/parley-clean && cd /tmp/parley-clean
docker build -t parley . && docker run --rm parley
```

Two real defects came out of finally running this, both invisible to
`scripts/check_dockerfile.py`:

- `docker run --rm parley pytest` died at *collection* — `demo/` was not copied
  into the image and `tests/test_live.py` imports it. Then, once copied, it still
  failed: the bare `pytest` console script does not put the working directory on
  `sys.path` the way `python -m pytest` does, so `demo` was unimportable.
  `tests/conftest.py` now inserts the repository root explicitly.
- Host `__pycache__` was being baked into the image, so a traceback inside a
  Linux container pointed at `D:\samsungprism\...`. Fixed by adding
  `.dockerignore`, which also took the build context from 8.63 MB to 16.7 kB.

Local check without a daemon, still useful and still passing:

```bash
python scripts/check_dockerfile.py
```

Local check without a daemon:

```bash
python scripts/check_dockerfile.py
```

---

## 4. Regenerate everything, from nothing

Proves the repo is self-contained and that no artefact is stale.

```bash
python scripts/make_media.py            # frames + clips (dataset is gitignored)
python scripts/train_perception.py      # vision + audio weights
python scripts/train_classifier.py      # interruption weights
python scripts/make_scenarios.py        # scenarios/*.json
python scripts/run_scenarios.py --json runs/scores.json
python scripts/fuzz.py --trials 300 --jitter 1400
python -m pytest
node deck/build_deck.js
```

Expected: **18/18 clean**, mean ≈ 107.8, **233 tests**, fuzzer reports no
invariant broken. If a number moved, find out why before tagging — the README
and the deck both quote these figures and they must not be fiction.

---

## 5. Record the demo video

Script and timings: [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md).

- [ ] **≤ 5:00.** Hard cap. Check the final file duration, not the timeline.
- [ ] Covers theme ID, problem, solution, architecture, stack, innovation,
      results, limitations.
- [ ] Uploaded and **checked in an incognito window** — "anyone with the link".
- [ ] Link written into `README.md` **before** tagging.

---

## 6. Complete the AI disclosure

[`DISCLOSURE.md`](DISCLOSURE.md) is filled in per feature, with the prompts
used, as section 4 of the form demands. Still to do:

- [ ] Team name, project name, submission date
- [ ] Representative's name, role, signature, date
- [ ] Transcribe into `LangAI3.0_AI_Disclosure.docx` and submit alongside

---

## 7. Final repo state

- [ ] `README.md` reproduces from clean (§3 proves it)
- [ ] Demo video link present
- [ ] Deck present, correctly named, **inside the repo**
- [ ] `DISCLOSURE.md` complete
- [ ] No placeholder team name anywhere (§2)
- [ ] Repo is **public**, or explicitly shared
- [ ] `git status` clean

---

## 8. Tag — and then stop

> "Create a release tag named `PRISM_GENAI_HACKATHON_Y2026` on your final
> commit. **The tagged commit is what gets judged.**"
>
> "Make sure everything referenced in your submission — PPT, demo video,
> documentation, etc. — is present in the tagged commit."

```bash
git status                      # must be clean
git tag -a PRISM_GENAI_HACKATHON_Y2026 -m "Samsung PRISM Y2026 GenAI Hackathon — Theme 05 — PARLEY"
git push origin main --tags
```

Verify the tag contains what you think it does:

```bash
git show --stat PRISM_GENAI_HACKATHON_Y2026 | head -20
git ls-tree -r --name-only PRISM_GENAI_HACKATHON_Y2026 | grep -iE "pptx|README|DISCLOSURE|Dockerfile"
```

**Push nothing after tagging.** If something must change, fix it, commit, delete
the tag, re-tag the new commit, and force-push the tag.

---

## 9. Submit the form

One submission per team, through the Google Form, before the deadline.

- Repo URL · demo video link · deck file · AI disclosure

---

## Traps, ranked by how easily they'd catch us

1. **Pushing after tagging.** The tagged commit is judged; a later fix is
   invisible. Tag last and verify.
2. ~~**Docker never actually built**~~ — was a self-inflicted trap. Nothing in
   the rules asks for a container; the submission is repo, video, deck,
   disclosure. It only counted as a risk because the README led with
   `docker build`, so a judge whose daemon was broken would have hit an error on
   our first line. The README now leads with plain Python and keeps Docker as a
   collapsed, optional section. The image still builds and is still verified —
   it is just no longer standing between a judge and the code.
3. **Video over 5:00**, or not publicly accessible. Check in incognito.
4. **Team name format.** `CollegeName_TeamName`, exactly, and the deck filename
   must match.
5. **Deadline: 30 Sep**, per the team. Submit with a day in hand — the form,
   the video upload and the tag all take longer than they look, and none of them
   can be done twice.
