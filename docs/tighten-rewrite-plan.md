# Tighten Rewrite — Implementation Plan (handoff spec, v2)

> Audience: an engineer or smaller coding model executing tasks one at a time.
> Each task is self-contained: files, exact contract, steps, and how to verify.
> Do the tasks **in order**. Do not skip an acceptance check.

---

## 0. Why we're doing this

The current pipeline decides keep/drop **per segment, locally**, then repairs incoherence
afterward (Continuity Guard, opener protection, fragment cleanup). Coherence is a *global*
property, so local decisions + heuristic repair produce choppy edits.

**New approach:** feed the **entire transcript as continuous prose** to a long-context LLM
and get back the **edited transcript** (words removed only). We recover exactly which words
were cut with a **diff**, then map those to timestamps. Edits are **word-level, extractive,
deletion-only, in original order**.

**Phase 1 (this plan): "tighten."** **Phase 2 (sketched): "clips."** Build Phase 2 only
after Phase 1 passes the eval gate.

---

## 1. Core principles (do not violate)

1. **The decision unit is the WORD, not any chunk.** The LLM edits continuous prose and
   may delete words anywhere. Cuts land wherever words are removed — never forced to a
   chunk boundary. (This is the key fix from v1: Whisper acoustic segments are an arbitrary
   unit that split mid-sentence, so they must NOT drive keep/drop.)
2. **Whisper segments are display-only.** They group words for the editor UI and carry the
   word-level cuts; they do not make keep/drop decisions. A segment becomes `keep=false`
   only if *every* one of its words was deleted.
3. **Deletion-only, diff-verified.** The LLM removes words; it never adds/rewords/reorders.
   We recover the edit with a real sequence diff. Any tokens the model *added* or *changed*
   are ignored (we keep the original there) — violations can only ever cause us to
   **under-cut**, never to cut the wrong words. Never trust the model; trust the diff.
4. **Text is decided by the LLM; timestamps are decided by code.**
5. **Gemini first, Gemma backup — both whole-transcript.** Prefer Gemini (1M ctx). If no
   key / Gemini fails, fall back to local Gemma, loaded with **131k context** in LM Studio —
   enough to hold a full transcript in one pass too. So **no chunking**: the Gemma path uses
   the same single full-transcript prompt as Gemini, just via the local route. If neither
   LLM is available, rule-based fallback. (Caveat: capacity ≠ quality — a 4B model may give
   weaker edits; the diff guardrail keeps it *safe*, never wrong-cut.)
6. **Iterate with a judge, keep the best.** Tighten runs in rounds: edit → judge → feed
   complaints back → re-edit. The judge uses the **same routing as the editor** —
   `gemini-2.5-flash` if a key is available, local Gemma as fallback, always. (Same-model
   judging is accepted; we don't have a stronger independent model, and the best-of guard
   below prevents the loop from drifting.) Always return the best-scoring round; stop on
   pass / no-improvement / cap.
7. **Eval gates the cutover against a baseline.** Capture the current pipeline's scores
   FIRST (Task 0) and don't ship unless coherence/preservation beat baseline. Optionally
   keep a small hand-marked must-keep/must-cut set as an extra sanity check, but the
   baseline comparison is the gate.
8. **Determinism for editing.** The edit + critic calls run at **temperature 0** so runs are
   reproducible and testable.

---

## 2. Architecture: before → after

**Before** (`orchestrator.py main`):
```
transcribe → segment.py → Director → Narrative Architect → build_segments
→ _repair_broken_joins → filler → surgeon → Continuity Guard → Rhythmist → linter
```

**After:**
```
transcribe → tighten ⟳ critic (iterate, keep best) → filler → surgeon → Rhythmist → linter
                │
                └─ round: edit full prose → diff → word cuts → critic → feedback → re-edit …
```

**Deleted (Phase 1):** `run_director`, `run_narrative_architect`, `build_segments`,
`_repair_broken_joins`, `_protect_opener`, `_run_quota_enforcement`, the Semantic Segmenter
step, and `apply_continuity_guard` from `main`.
> Keep `continuity.sanitize_word_cuts` (used by `replan.py`, Task 6). Keep `segment.py` on
> disk (unused); optional cleanup later.

**Kept unchanged:** `transcribe.py` (+ loop collapse), `surgeon.refine_word_cuts`,
`filler.apply_filler_cuts`, `linter.lint`, `rhythmist.apply_rhythm`, export route,
transcript-editor UI, eval/judge system.

**New file:** `scripts/tighten.py` (Tasks 1–3B).

### 2.1 Silence & dead-air (NOT the LLM's job)

The LLM only edits text; it cannot perceive silence, so it must never be asked to. Silence
removal stays a **deterministic acoustic pass** that runs *after* tighten, unchanged:

- **Within a kept segment** — `surgeon.refine_word_cuts(..., add_silence_cuts=True)` (still
  called in Task 7) detects RMS-silent regions and emits `source:"silence"` word-cuts. This
  is where the `min_sil`/aggressiveness scaling and the edge-silence sanitize exemption live.
- **Between segments** — the export route's `PRE_ROLL`/`POST_ROLL` gap logic drops gaps
  beyond ~0.33s left behind after content is cut.
- **Inside deleted content** — gone for free (the whole span is removed by the word cut).
- The export **no-word-span filter** drops any residual silent sliver so it never renders as
  a phantom clip.

Responsibility split, stated plainly: **LLM = which words (content) to remove; code =
silence, boundary snapping, gaps.** Do not move silence logic into the LLM.

---

## 3. Data contracts (authoritative)

### 3.1 Flat word with provenance (built once at the top of `tighten`)
```python
{"word": "Cambridge,", "start": 392.10, "end": 392.64, "segIdx": 41, "wordIdxInSeg": 3}
```
Built by flattening `whisper_segments[*].words`, recording each word's owning segment index
and its index within that segment. Order = spoken order. This list is the alignment domain.

### 3.2 LLM output: the edited transcript (Task 3)
Plain text — the original transcript with words removed (deletion-only) — plus a 2-sentence
`summary` and a one-line `rationale` (the "why I cut what I cut" shown in the UI):
```python
{"edited_transcript": "I used to think my goals were my own. But as I moved around the country I realized ...",
 "summary": "Moving from a small Texas town to MIT reshaped how the speaker sees identity.",
 "rationale": "Removed filler and two repeated false starts; kept the full MIT arc."}
```
Schema: `{"type":"object","properties":{"edited_transcript":{"type":"string"},"summary":{"type":"string"},"rationale":{"type":"string"}},"required":["edited_transcript"]}`.
No per-span reasons (localization stays unambiguous); fully-deleted segments get a generic
`dropReason:"trimmed"`.

### 3.3 Output segment (matches existing shape — frontend/export unchanged)
```python
{
  "startSec": 392.10, "endSec": 395.02,
  "start": "6:32", "end": "6:35",
  "text": "When I was 18 I moved to Cambridge",
  "keep": True,                 # False ⇔ every word in this segment was deleted
  "words": [ {"word","start","end"}, ... ],   # original Whisper words
  "wordCuts": [ ... ],          # §3.4, the deletions that fall in this segment
  "decisionSource": "pipeline",
  "dropReason": "",             # "removed" when keep=False via tighten; else ""
  "jobRisk": "",
  "motionScore": 0, "audioRms": 0, "energyScore": 0, "visualTag": None,
}
```

### 3.4 WordCut (deletion span — existing type)
```python
{
  "id": "392.100:3",   # f"{segment.startSec:.3f}:{firstDeletedWordIdxInSeg}"
  "startSec": 393.01, "endSec": 393.55,
  "word": "you know",  # display only
  "source": "trim",    # NEW source — Tasks 5, 6
}
```

---

## 4. Tasks

### Task 0 — Baseline + independent eval harness (DO THIS FIRST)

**Goal:** be able to *prove* the rewrite is better, not just different.

**Steps:**
1. Run the **current** pipeline on 3–5 representative fixtures; save plans and record
   `coherence / preservation / conciseness` from `scripts/eval.py`. This is the **baseline**
   (write the numbers into `docs/tighten-baseline.md`).
2. (Optional but recommended) Hand-mark a tiny sanity set: for 1–2 fixtures, list a few
   spans that **must be kept** and a few that **should be cut**. Store as
   `fixtures/ground_truth/<id>.json`. Task 8 spot-checks the new edit against these.

Models: editor and judge both use the standard routing (Gemini `gemini-2.5-flash` if a key
is set, local Gemma fallback). No separate critic model.

**Acceptance:** `docs/tighten-baseline.md` exists with per-fixture scores.

---

### Task 1 — `scripts/tighten.py`: prompt builder

**Function:** `def _build_prompt(flat_words, extra_instructions="") -> str:`

Render the transcript as **continuous prose** (join `w["word"]` with spaces). Do **not**
number or chunk it — we want the model editing natural text, not chunks.

**Prompt (verbatim):**
```
You are a video editor tightening a spoken-word transcript. Below is the FULL transcript.

Return the transcript edited to be tighter and cleaner, by DELETING words only. You may
NOT add words, rewrite, reorder, paraphrase, or fix grammar. Every word in your output must
appear in the original, in the same order — you are only allowed to remove words.

Remove: filler ("um", "uh", "you know", "like", "I mean"), verbal stumbles and false
starts, redundant repetition, tangents, and rambling that adds nothing. Preserve the
complete narrative and the speaker's voice. When unsure, keep it. Do not over-cut — the
result must read naturally.

{extra_instructions}     # REVISION NOTES block on refine rounds; empty on round 1

TRANSCRIPT:
{prose}

Return JSON: {"edited_transcript": "<edited text>", "summary": "<2-sentence summary>", "rationale": "<one line: what you cut and why>"}.
```
> Tightness is a fixed default baked into this prompt for v1 (no user knob). A light/medium/
> heavy control is a v2 addition.

**Schema:** see §3.2.

**Acceptance:** prompt contains the full prose and (when given) the revision notes.

---

### Task 2 — `scripts/tighten.py`: diff-based alignment (the rigorous core)

**This replaces v1's greedy matcher.** Greedy two-pointer mis-handles duplicate tokens
("really really" → wrong "really" cut). Use `difflib.SequenceMatcher`, which aligns by
longest matching blocks and is positionally coherent.

```python
import difflib, re
_norm = lambda t: re.sub(r"[^a-z0-9']", "", t.lower())

def align_deletions(flat_words, edited_text):
    """Return (deleted_global_indices: list[int], inserted_count: int).
    deleted = original words removed by the edit; inserted = tokens the model
    ADDED or CHANGED (deletion-only violations) — these are NOT applied."""
    orig = [_norm(w["word"]) for w in flat_words]
    edit = [_norm(t) for t in edited_text.split() if _norm(t)]
    sm = difflib.SequenceMatcher(a=orig, b=edit, autojunk=False)
    deleted, inserted = [], 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "delete":
            deleted.extend(range(i1, i2))
        elif tag == "insert":
            inserted += (j2 - j1)
        elif tag == "replace":
            # model changed words: do NOT cut the originals (keep them); count
            # the new tokens as a violation. Worst case we under-cut — never wrong-cut.
            inserted += (j2 - j1)
        # "equal" → kept
    return deleted, inserted
```

**Guardrails (caller):**
- If `inserted / max(len(orig),1) > 0.05` → the model misbehaved badly; **log a warning**
  and either retry once or accept (we only ever under-cut, so it's safe — content stays).
- Deletions are safe by construction: only `delete` opcodes cut anything.

**Acceptance (inline self-tests, `python scripts/tighten.py` prints PASS/FAIL):**
- Duplicates: orig `"I really really wanted it"`, edited `"I really wanted it"` → exactly
  ONE of the two "really" indices deleted (adjacent), not both, not the wrong token.
- Plain filler: orig `"But you know I moved"`, edited `"But I moved"` → "you","know" deleted.
- Paraphrase: orig `"But I relocated"`, edited `"But then I moved away"` → `inserted > 0`,
  and the original "relocated"/"I" are NOT spuriously cut (replace → kept).
- No-op: edited == original → `deleted == []`, `inserted == 0`.

---

### Task 3 — `scripts/tighten.py`: one pass (LLM → diff → segments) + Gemini/Gemma

```python
def tighten_once(whisper_segments, api_key, extra_instructions="") -> tuple[list[dict], str]:
    flat = _flatten(whisper_segments)                 # §3.1
    edited, summary = _get_edit(flat, api_key, extra_instructions)
    deleted, inserted = align_deletions(flat, edited) # Task 2
    if inserted / max(len(flat),1) > 0.05:
        print(f"[tighten] model added/changed {inserted} tokens; honoring deletes only", file=sys.stderr)
    segments = _apply_deletions(whisper_segments, flat, set(deleted))
    return segments, summary
```

**`_flatten(whisper_segments) -> list[flat_word]`** — §3.1, recording `segIdx`/`wordIdxInSeg`.

**`_get_edit(flat, api_key, extra) -> (edited_text, summary)`:** one full-transcript call;
the prompt and parsing are identical for both backends — only the route differs.
- **Gemini first** (if `api_key`): `llm.generate(_build_prompt(prose, extra),
  schema=EDIT_SCHEMA, api_key=api_key, prefer_cloud=True, temperature=0)`.
- **Gemma fallback** (if `llm.is_local()`): same call **without** `prefer_cloud` → routes to
  local Gemma. Gemma's 131k context holds the whole transcript, so **no chunking** — same
  single prompt. **Set a generous output cap** (see note) since the edited transcript can be
  nearly as long as the input.
- On total failure: return `(original_prose, "")` (⇒ no deletions ⇒ everything kept).
- Parse the JSON → `(edited_transcript, summary)` in both cases.

> **Output token cap:** the model echoes the (edited) transcript, so the response can be
> thousands of tokens. Ensure `_gemini`/`_openai_compat` don't cap `max_tokens` too low —
> set `max_tokens` high (e.g. `len(prose_tokens) + 512`) or unlimited for the edit call,
> or the transcript will be truncated mid-document and the diff will mark the tail deleted.

**`_apply_deletions(whisper_segments, flat, deleted_set) -> list[dict]`:**
1. For each global deleted index, look up `segIdx` + `wordIdxInSeg`.
2. Per segment, group deleted `wordIdxInSeg` into **consecutive runs** → one WordCut each
   (§3.4), `startSec/endSec` from the run's first/last word.
3. If **all** of a segment's words are deleted → emit it with `keep=False`,
   `dropReason="removed"`, `wordCuts=[]`.
4. Otherwise `keep=True` with the run WordCuts. Segments with no deletions → `keep=True`,
   `wordCuts=[]`. Emit §3.3 shape for every segment.

**Acceptance:** with a stub `_get_edit`, deleting a span that crosses two Whisper segments
produces WordCuts in *both* segments; deleting all words of a segment yields `keep=False`;
output has one segment per input segment.

---

### Task 3B — `scripts/tighten.py`: iterative critic loop (public entrypoint)

```python
TARGET_COHERENCE = 90
FLOOR_COHERENCE  = 70             # below max(baseline, this) → ship unedited (low confidence)
MIN_WORDS_TO_TIGHTEN = 30         # short-input guard: nothing to tighten
MAX_ROUNDS_CLOUD, MAX_ROUNDS_LOCAL = 3, 2   # local cap is about speed (4B is slow), not context
LONG_TOKEN_THRESHOLD = 15000      # past ~1hr of speech, scale rounds down

def tighten(whisper_segments, api_key) -> tuple[list[dict], str, bool]:
    """Returns (segments, summary, low_confidence)."""
    flat = _flatten(whisper_segments)
    # Short-input guard: skip the LLM entirely, keep everything.
    if len(flat) < MIN_WORDS_TO_TIGHTEN:
        return _all_keep(whisper_segments), "", False
    max_rounds = MAX_ROUNDS_CLOUD if api_key else MAX_ROUNDS_LOCAL
    if _approx_tokens(flat) > LONG_TOKEN_THRESHOLD:
        max_rounds = min(max_rounds, 2)               # length-based round scaling
    extra, best = "", None            # best = (segments, summary, score, coherence)
    for r in range(max_rounds):
        segments, summary = tighten_once(whisper_segments, api_key, extra)
        verdict = _judge(segments, api_key)
        score = 0.5*verdict["coherence"] + 0.3*verdict["preservation"] + 0.2*verdict["conciseness"]
        print(f"[tighten] round {r+1}: coh={verdict['coherence']} score={score:.1f}", file=sys.stderr)
        if best is None or score > best[2]:
            best = (segments, summary, score, verdict["coherence"])
        if verdict["coherence"] >= TARGET_COHERENCE and not verdict["false_positives"] and not verdict["false_negatives"]:
            break                                     # passed
        if best[0] is not segments and score <= best[2]:
            break                                     # no improvement → stop (anti-oscillation)
        extra = _feedback_to_instructions(verdict)
    # Failure floor: if even the best round is poor, ship the UNEDITED transcript + flag.
    if best[3] < FLOOR_COHERENCE:
        print(f"[tighten] best coherence {best[3]} < floor → shipping unedited (low confidence)", file=sys.stderr)
        return _all_keep(whisper_segments), best[1], True
    return best[0], best[1], False
```
> `_all_keep(whisper_segments)` emits §3.3 segments with `keep=True`, no cuts. The
> `low_confidence` flag flows to the output so the UI/summary can say "auto-edit
> low-confidence — review manually". The baseline value for the floor comes from Task 0
> (use `max(baseline_coherence, FLOOR_COHERENCE)` if you want it baseline-relative).

**`_judge(segments, api_key)`** — wrap `judge_plan`; same routing as the editor (Gemini if
key, Gemma fallback — `judge_plan` already reads the environment, no model override needed):
```python
from eval_judge import judge_plan
plan = {"version":1, "segments":segments, "summary":"", "settings":{"fillerSensitivity":"balanced"}}
return judge_plan(plan, api_key, strict=True, require_repairs=True)
```
> Returns `coherence/preservation/conciseness` (0–100), `false_positives` (over-cuts →
> restore) and `false_negatives` (under-cuts → cut), items `{segment_index,text,reason}`.

**`_feedback_to_instructions(verdict) -> str`** — REVISION NOTES block:
```
REVISION NOTES (fix these from your previous edit):
- RESTORE (you removed this but it's needed): "<text>" — <reason>
- REMOVE (you left this in but it should go): "<text>" — <reason>
Keep all other edits the same unless they conflict with these notes.
```

**Anti-regression (critical):** always return the best-scoring round; stop on pass, on
failure-to-improve, or at the cap. Never return a worse later round.

**Acceptance (stub `_judge`):** pass-on-round-1 → 1 round; improve-then-regress → returns
the middle (best) round; no key → ≤ `MAX_ROUNDS_LOCAL` rounds.

---

### Task 4 — `scripts/llm.py`: `prefer_cloud` + `temperature` + `model` plumbing

- Add `prefer_cloud=False` and `temperature=None` to `generate`:
  ```python
  def generate(prompt, schema=None, model=None, api_key=None, prefer_cloud=False, temperature=None):
      if prefer_cloud and api_key:
          return _gemini(prompt, schema, model, api_key, temperature)
      if is_local():
          return _openai_compat(prompt, schema, model, temperature)
      return _gemini(prompt, schema, model, api_key, temperature)
  ```
- Thread `temperature` into `_gemini` and `_openai_compat` (default to their current values
  when `None`; editing/critic callers pass `0`).
- Existing callers (no new args) behave exactly as before. `_openai_compat` already ignores
  Gemini-style model names (prior fix) → the Gemma path uses `LLM_MODEL` correctly.

**Acceptance:** `generate(..., prefer_cloud=True)` with key → Gemini; `generate(...)` →
local when `LLM_BASE_URL` set; temperature 0 reaches the API payloads.

---

### Task 5 — Frontend updates
- **`"trim"` source:** `lib/editPlan.ts` `WordCutSource` += `"trim"`; `app/api/export/route.ts`
  `WordCut.source` += `"trim"`. `components/TranscriptEditor.tsx`: render `source==="trim"`
  struck-through red (`#ef4444` / `rgba(239,68,68,0.5)`) — distinct from blue `manual`,
  amber `filler`.
- **Narrative card:** show `summary` + `rationale`. The anchor-story/tangent chips are gone
  (`narrativeAnalysis` is `{}`); the existing card already renders those conditionally, so an
  empty object just hides them — add the `rationale` line beneath the summary.
- **Low-confidence banner:** if `plan.lowConfidence`, show "Auto-edit was low-confidence —
  transcript left intact, please review/cut manually."
- **Auto-refine button:** wire it to the **mechanical** apply of judge repairs (restore
  `false_positives`, drop `false_negatives`), NOT a new LLM edit pass. The Judge button still
  just lists suggestions. (Confirm current Auto-refine handler doesn't re-invoke tighten.)
- **Progress:** surface the orchestrator's per-round stderr lines ("Tighten — round 2…") in
  the existing transcription progress UI (synchronous execution; no new job infra).
- **Acceptance:** `npx tsc --noEmit` clean; trims red-struck; rationale shows; low-confidence
  banner appears when flagged.

---

### Task 6 — Preserve `"trim"` cuts through finalize/replan
- `scripts/filler.py` `apply_filler_cuts`: preserve cuts whose source ∈ `{"manual","trim"}`.
- **Test:**
  ```bash
  .venv/bin/python3 -c "import sys;sys.path.insert(0,'scripts');from filler import apply_filler_cuts;\
  seg={'startSec':1.0,'endSec':3.0,'words':[],'wordCuts':[{'id':'1.000:2','startSec':1.5,'endSec':1.8,'word':'um','source':'trim'}]};\
  print('preserved:', any(c['source']=='trim' for c in apply_filler_cuts([seg],'balanced',preserve_manual=True)[0]['wordCuts']))"
  ```

---

### Task 7 — Rewire `orchestrator.py main()` (switch; keep old code dead)

**Two-commit cutover:** this task only *switches* to tighten and leaves the old functions in
place, unused. Deleting them happens in **Task 8** *after* the eval gate passes — so a failed
gate is a one-line revert, not a re-deletion.

Replace the Segmenter → Director → Narrative Architect → build_segments → _repair_broken_joins
→ Continuity Guard block (~lines 1133–1182 and the `directorConfig`/`continuity_issues`
references at 1205–1217) with:
```python
print("▶ Tighten: holistic word-level edit + critic loop...", file=sys.stderr)
from tighten import tighten
if api_key or _llm.is_local():
    segments, summary, low_confidence = tighten(whisper_result["segments"], api_key)
else:
    segments = _rule_based_fallback_segments(whisper_result["segments"]); summary = ""; low_confidence = False
segments = apply_filler_cuts(segments, filler_sensitivity, preserve_manual=True)
# Surgeon: aggressiveness=0.65, add_silence_cuts=True.  Rhythmist: defaults.  Linter: unchanged.
```
- Add `_rule_based_fallback_segments(whisper_segments)` → full §3.3 segments (reuse old drop
  heuristics).
- Output dict: set `directorConfig: {}`, `narrativeAnalysis: {}`, `summary`, `rationale`
  (thread it out of `tighten` too if you want it in the card), `lowConfidence: low_confidence`,
  `linterPassed`, `linterIssues = lint_result["issues"]`.
- The old `run_director` / `run_narrative_architect` / `build_segments` /
  `_repair_broken_joins` / `_protect_opener` / `_run_quota_enforcement` / `_run_segmenter` /
  `apply_continuity_guard` are now **unreferenced but NOT deleted yet** (Task 8).
- **Acceptance:** `orchestrator.py <video> --save-fixture` → valid plan with the new fields;
  the only call path is via `tighten`.

---

### Task 8 — Eval gate, then delete the old code
1. New fixtures with the new pipeline (`--save-fixture`).
2. Run `scripts/eval.py` (same judge routing as everything else).
3. **Gate (both required):** coherence ≥ baseline **AND** preservation ≥ baseline.
   Conciseness is informational only. If you made the optional `ground_truth` set, also
   spot-check its must-keep spans survive. If the gate fails, tune the Task 1 prompt; **do
   not delete the old code.**
4. **Only after the gate passes:** delete the now-dead `run_director`,
   `run_narrative_architect`, `build_segments`, `_repair_broken_joins`, `_protect_opener`,
   `_run_quota_enforcement`, the `_run_segmenter` import/call, and
   `from continuity import apply_continuity_guard` (keep `continuity.py` for `replan.py`).
   Verify: `grep -nE "run_director|run_narrative|build_segments|_protect_opener|_run_quota|_repair_broken_joins|apply_continuity_guard" scripts/orchestrator.py` → no matches.

---

## 5. Verification checklist
- [ ] `docs/tighten-baseline.md` recorded before any rewrite (Task 0).
- [ ] `npx tsc --noEmit` clean.
- [ ] `python scripts/tighten.py` self-tests pass — **including the duplicate-token diff tests**.
- [ ] Loop logs per-round coherence; returns best round; stops on pass/no-improvement/cap.
- [ ] A deletion crossing two Whisper segments produces cuts in both; a fully-deleted
      segment is `keep=False`.
- [ ] Edits are reproducible run-to-run (temperature 0).
- [ ] Short input (<30 words) skips the LLM and keeps everything.
- [ ] Failure floor: a deliberately-bad edit (best coherence < floor) ships unedited with
      `lowConfidence=True` and the UI banner shows.
- [ ] UI: trims struck-through (red); rationale shows; toggling a segment + export respects
      the edit; Auto-refine applies judge repairs without re-running tighten.
- [ ] Export: no zero-word clips (existing filter still applies).
- [ ] Eval gate passes: coherence & preservation ≥ baseline (+ optional ground-truth
      spot-check); old code deleted only after the gate passes (Task 8).

---

## 6. Phase 2 — Clips (sketch only; build after Phase 1 passes §5)
Second pass over the tightened transcript → N self-contained highlight ranges (strong hook,
complete thought, ~20–60s). Reuse alignment → timestamps → export; each clip renders to its
own MP4 (consider 9:16 crop reusing thumbnail/zoom code). New "Clips" UI tab.

---

## 7. Rollback
Replace-in-place. Single revert point: the `orchestrator.py main()` rewrite (Task 7).
`tighten.py` is additive/harmless if unused. One commit per task for independent reverts.
```
