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
| **Deadline** | ⚠️ **unresolved** | Deck says **25 Sep 11:59 PM**; team reports 30 Sep. Get it in writing from `prism@samsung.com`. **Plan to the 25th**; treat any extension as buffer, never budget. |
| **Team representative** | ⚠️ blank | Needed for the disclosure sign-off. |
| **Demo video host** | ⚠️ not recorded | YouTube (unlisted) or Drive. Link must be inside the tagged commit. |

---

## 2. Substitute the team name

It appears in five places. A single `ThaparPatiala_<TEAM>` left in the tagged
commit looks careless to a jury reading the README first.

```bash
grep -rn "ThaparPatiala_<TEAM>\|ThaparPatiala_TEAM" --exclude-dir=.git --exclude-dir=.venv .
```

Then rename the deck file itself:

```bash
git mv deck/ThaparPatiala_TEAM_Submission_ppt.pptx deck/<CollegeName>_<TeamName>_Submission_ppt.pptx
```

The deck's author/title metadata is set in `deck/build_deck.js` — change it
there and re-run `node deck/build_deck.js` rather than editing the .pptx.

---

## 3. Verify the build from clean

**The one thing not yet verified on this machine.** Docker Desktop's Linux
engine would not start here, so `docker build` has never actually run.
`scripts/check_dockerfile.py` validates COPY paths, the Python version band,
dependency coverage, committed weights, and executes both inline build guards —
but that is not the same as a build.

**Run this on a machine with a working daemon before tagging:**

```bash
git clone <repo> /tmp/parley-clean && cd /tmp/parley-clean
docker build -t parley .            # must succeed, and print "warm-up OK"
docker run --rm parley              # must print 18/18 scenarios, no failures
docker run --rm parley pytest       # must print 233 passed
```

If the build fails, fix it in the Dockerfile and re-run — do not ship a README
whose first command doesn't work.

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
2. **Docker never actually built** (§3). The README's first command is
   `docker build`. A judge running it and hitting an error reads as untested.
3. **Video over 5:00**, or not publicly accessible. Check in incognito.
4. **Team name format.** `CollegeName_TeamName`, exactly, and the deck filename
   must match.
5. **Deadline.** Planning to the 30th when it closes on the 25th loses
   everything, and the discrepancy is still unresolved.
