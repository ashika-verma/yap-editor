#!/usr/bin/env python3
"""
scripts/tighten.py — Holistic word-level edit pass (Tasks 1–3B).

Entry point: tighten(whisper_segments, api_key) -> (segments, summary, low_confidence)

Architecture:
  full prose → LLM deletion-only edit → difflib alignment → word-level cuts per segment
  Iterative: edit → judge → feedback → re-edit; always return best-scoring round.
"""
from __future__ import annotations
import difflib
import json
import re
import sys

# ── Constants ──────────────────────────────────────────────────────────────────

TARGET_COHERENCE     = 90
FLOOR_COHERENCE      = 70    # if best coherence < this, ship unedited + low_confidence flag
MIN_WORDS_TO_TIGHTEN = 30    # trivial inputs: skip LLM entirely
MAX_ROUNDS_CLOUD     = 3
MAX_ROUNDS_LOCAL     = 2     # local cap is speed (4B is slow), not context
LONG_TOKEN_THRESHOLD = 15000 # past ~1hr of speech, scale rounds down

EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "edited_transcript": {"type": "string"},
        "summary":           {"type": "string"},
        "rationale":         {"type": "string"},
    },
    "required": ["edited_transcript"],
}

_norm = lambda t: re.sub(r"[^a-z0-9']", "", t.lower())


def _seg_start(seg: dict) -> float:
    """Get segment start in seconds from either raw Whisper ('start') or shaped ('startSec') format."""
    for k in ("startSec", "start_sec"):
        if seg.get(k) is not None:
            return float(seg[k])
    v = seg.get("start")
    if isinstance(v, (int, float)):
        return float(v)
    return 0.0


def _seg_end(seg: dict) -> float:
    """Get segment end in seconds from either raw Whisper ('end') or shaped ('endSec') format."""
    for k in ("endSec", "end_sec"):
        if seg.get(k) is not None:
            return float(seg[k])
    v = seg.get("end")
    if isinstance(v, (int, float)):
        return float(v)
    return 0.0


# ── Task 1: Prompt builder ─────────────────────────────────────────────────────

def _build_prompt(flat_words: list[dict], extra_instructions: str = "") -> str:
    prose = " ".join(w["word"] for w in flat_words)
    extra_block = f"\n{extra_instructions.strip()}\n" if extra_instructions.strip() else ""
    return (
        "You are a video editor tightening a spoken-word transcript. Below is the FULL transcript.\n\n"
        "Return the transcript edited to be tighter and cleaner, by DELETING words only. You may\n"
        "NOT add words, rewrite, reorder, paraphrase, or fix grammar. Every word in your output must\n"
        "appear in the original, in the same order — you are only allowed to remove words.\n\n"
        'Remove: filler ("um", "uh", "you know", "like", "I mean"), verbal stumbles and false\n'
        "starts, redundant repetition, tangents, and rambling that adds nothing. Preserve the\n"
        "complete narrative and the speaker's voice. When unsure, keep it. Do not over-cut — the\n"
        "result must read naturally.\n"
        f"{extra_block}\n"
        f"TRANSCRIPT:\n{prose}\n\n"
        'Return JSON: {"edited_transcript": "<edited text>", "summary": "<2-sentence summary>", '
        '"rationale": "<one line: what you cut and why>"}.'
    )


# ── Task 2: Diff-based alignment ───────────────────────────────────────────────

def align_deletions(flat_words: list[dict], edited_text: str) -> tuple[list[int], int]:
    """Return (deleted_global_indices, inserted_count).

    deleted = original words removed by the edit (safe to cut).
    inserted = tokens the model ADDED or CHANGED — these are NOT applied; we only ever under-cut.
    """
    orig = [_norm(w["word"]) for w in flat_words]
    edit = [_norm(t) for t in edited_text.split() if _norm(t)]
    sm   = difflib.SequenceMatcher(a=orig, b=edit, autojunk=False)
    deleted, inserted = [], 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "delete":
            deleted.extend(range(i1, i2))
        elif tag == "insert":
            inserted += (j2 - j1)
        elif tag == "replace":
            # model changed words: keep originals, count added tokens as violation
            inserted += (j2 - j1)
        # "equal" → kept, nothing to do
    return deleted, inserted


# ── Helpers ────────────────────────────────────────────────────────────────────

def _flatten(whisper_segments: list[dict]) -> list[dict]:
    """Build flat word list with segment provenance (§3.1)."""
    flat = []
    for seg_idx, seg in enumerate(whisper_segments):
        for word_idx, w in enumerate(seg.get("words", [])):
            flat.append({
                "word":         w["word"],
                "start":        w["start"],
                "end":          w["end"],
                "segIdx":       seg_idx,
                "wordIdxInSeg": word_idx,
            })
    return flat


def _approx_tokens(flat_words: list[dict]) -> int:
    return sum(len(w["word"]) for w in flat_words) // 4


def _fmt_time(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


def _all_keep(whisper_segments: list[dict]) -> list[dict]:
    """Return §3.3 segments with keep=True and no cuts."""
    out = []
    for seg in whisper_segments:
        start_sec = _seg_start(seg)
        end_sec   = _seg_end(seg)
        out.append({
            "startSec":       start_sec,
            "endSec":         end_sec,
            "start":          _fmt_time(start_sec),
            "end":            _fmt_time(end_sec),
            "text":           seg.get("text", ""),
            "keep":           True,
            "words":          seg.get("words", []),
            "wordCuts":       [],
            "decisionSource": "pipeline",
            "dropReason":     "",
            "jobRisk":        "",
            "motionScore":    seg.get("motionScore", 0),
            "audioRms":       seg.get("audioRms", 0),
            "energyScore":    seg.get("energyScore", 0),
            "visualTag":      seg.get("visualTag", None),
        })
    return out


def _get_edit(flat: list[dict], api_key: str | None, extra: str = "") -> tuple[str, str, str]:
    """Call LLM for edited transcript. Returns (edited_text, summary, rationale). Never raises."""
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import llm

    orig_word_count = len(flat)
    prompt = _build_prompt(flat, extra)
    try:
        raw    = llm.generate(prompt, schema=EDIT_SCHEMA, api_key=api_key,
                               prefer_cloud=True, temperature=0)
        parsed = json.loads(raw)
        edited = parsed.get("edited_transcript", "")

        # Sanity check: if LLM returned near-empty output, treat as failure
        edit_word_count = len(edited.split()) if edited.strip() else 0
        if edit_word_count < 0.3 * orig_word_count:
            print(
                f"[tighten] edit suspiciously short ({edit_word_count} words vs {orig_word_count} orig) "
                "— treating as failed edit",
                file=sys.stderr,
            )
            return " ".join(w["word"] for w in flat), "", ""

        print(
            f"[tighten] edit: {orig_word_count} → {edit_word_count} words "
            f"(−{orig_word_count - edit_word_count}, {(orig_word_count - edit_word_count) / orig_word_count * 100:.0f}% cut)",
            file=sys.stderr,
        )
        return (edited, parsed.get("summary", ""), parsed.get("rationale", ""))
    except Exception as e:
        print(f"[tighten] LLM edit failed: {e}", file=sys.stderr)
        return " ".join(w["word"] for w in flat), "", ""  # no deletions


def _apply_deletions(
    whisper_segments: list[dict],
    flat: list[dict],
    deleted_set: set[int],
) -> list[dict]:
    """Build §3.3 segments with word-level WordCuts from deleted global indices."""
    cuts_by_seg: dict[int, list[int]] = {}
    for global_idx in deleted_set:
        fw = flat[global_idx]
        cuts_by_seg.setdefault(fw["segIdx"], []).append(fw["wordIdxInSeg"])

    out = []
    for seg_idx, seg in enumerate(whisper_segments):
        start_sec = _seg_start(seg)
        end_sec   = _seg_end(seg)
        words     = seg.get("words", [])
        del_idxs  = sorted(cuts_by_seg.get(seg_idx, []))
        all_deleted = len(words) > 0 and len(del_idxs) == len(words)

        word_cuts: list[dict] = []
        if del_idxs and not all_deleted:
            # Group consecutive word indices into runs → one WordCut per run
            runs: list[tuple[int, int]] = []
            rs = re_ = del_idxs[0]
            for wi in del_idxs[1:]:
                if wi == re_ + 1:
                    re_ = wi
                else:
                    runs.append((rs, re_))
                    rs = re_ = wi
            runs.append((rs, re_))

            for rs, re_ in runs:
                word_cuts.append({
                    "id":       f"{start_sec:.3f}:{rs}",
                    "startSec": words[rs]["start"],
                    "endSec":   words[re_]["end"],
                    "word":     " ".join(words[i]["word"] for i in range(rs, re_ + 1)),
                    "source":   "trim",
                })

        out.append({
            "startSec":       start_sec,
            "endSec":         end_sec,
            "start":          _fmt_time(start_sec),
            "end":            _fmt_time(end_sec),
            "text":           seg.get("text", ""),
            "keep":           not all_deleted,
            "words":          words,
            "wordCuts":       word_cuts,
            "decisionSource": "pipeline",
            "dropReason":     "removed" if all_deleted else "",
            "jobRisk":        "",
            "motionScore":    seg.get("motionScore", 0),
            "audioRms":       seg.get("audioRms", 0),
            "energyScore":    seg.get("energyScore", 0),
            "visualTag":      seg.get("visualTag", None),
        })
    return out


# ── Task 3: One pass ───────────────────────────────────────────────────────────

def tighten_once(
    whisper_segments: list[dict],
    api_key: str | None,
    extra_instructions: str = "",
) -> tuple[list[dict], str, str]:
    """One edit + diff pass. Returns (segments, summary, rationale)."""
    flat = _flatten(whisper_segments)
    edited, summary, rationale = _get_edit(flat, api_key, extra_instructions)
    deleted, inserted = align_deletions(flat, edited)
    if inserted / max(len(flat), 1) > 0.05:
        print(f"[tighten] model added/changed {inserted} tokens; honoring deletes only",
              file=sys.stderr)
    print(f"[tighten] align: {len(deleted)} words deleted, {inserted} inserted/changed",
          file=sys.stderr)
    segs = _apply_deletions(whisper_segments, flat, set(deleted))
    dropped_segs = sum(1 for s in segs if not s.get("keep"))
    print(f"[tighten] result: {dropped_segs}/{len(segs)} segments dropped",
          file=sys.stderr)
    return segs, summary, rationale


# ── Task 3B: Judge + feedback ──────────────────────────────────────────────────

def _judge(segments: list[dict], api_key: str | None) -> dict:
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from eval_judge import judge_plan
    plan = {
        "version": 1,
        "segments": segments,
        "summary": "",
        "settings": {"fillerSensitivity": "balanced"},
    }
    return judge_plan(plan, api_key, strict=True, require_repairs=True)


def _feedback_to_instructions(verdict: dict) -> str:
    lines = ["REVISION NOTES (fix these from your previous edit):"]
    for fp in verdict.get("false_positives", []):
        lines.append(f'- RESTORE (you removed this but it\'s needed): "{fp["text"]}" — {fp["reason"]}')
    for fn in verdict.get("false_negatives", []):
        lines.append(f'- REMOVE (you left this in but it should go): "{fn["text"]}" — {fn["reason"]}')
    lines.append("Keep all other edits the same unless they conflict with these notes.")
    return "\n".join(lines)


# ── Task 3B: Public entrypoint ─────────────────────────────────────────────────

def tighten(
    whisper_segments: list[dict],
    api_key: str | None,
) -> tuple[list[dict], str, bool, str]:
    """Returns (segments, summary, low_confidence, rationale)."""
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import llm

    flat = _flatten(whisper_segments)

    print(
        f"[tighten] {len(whisper_segments)} segments, {len(flat)} words, "
        f"~{_approx_tokens(flat)} tokens; api_key={'yes' if api_key else 'no'}",
        file=sys.stderr,
    )

    if len(flat) < MIN_WORDS_TO_TIGHTEN:
        print(f"[tighten] too short ({len(flat)} < {MIN_WORDS_TO_TIGHTEN}) — skipping", file=sys.stderr)
        return _all_keep(whisper_segments), "", False, ""

    max_rounds = MAX_ROUNDS_CLOUD if api_key else MAX_ROUNDS_LOCAL
    if _approx_tokens(flat) > LONG_TOKEN_THRESHOLD:
        max_rounds = min(max_rounds, 2)

    extra = ""
    # best = (segments, summary, score, coherence, rationale)
    best: tuple[list[dict], str, float, int, str] | None = None

    for r in range(max_rounds):
        print(f"[tighten] round {r + 1}/{max_rounds}...", file=sys.stderr)
        segments, summary, rationale = tighten_once(whisper_segments, api_key, extra)

        try:
            verdict = _judge(segments, api_key)
        except Exception as e:
            print(f"[tighten] judge failed on round {r + 1}: {e}", file=sys.stderr)
            if best is None:
                # Judge unavailable; assume edit is acceptable (75 > FLOOR) rather
                # than triggering the floor check with 0 and discarding the edit.
                best = (segments, summary, 0.0, 75, rationale)
            break

        coherence    = verdict.get("coherence", 0)
        preservation = verdict.get("preservation", 0)
        conciseness  = verdict.get("conciseness", 0)
        score = 0.5 * coherence + 0.3 * preservation + 0.2 * conciseness
        print(
            f"[tighten] round {r + 1}: coh={coherence} pre={preservation} "
            f"con={conciseness} score={score:.1f}",
            file=sys.stderr,
        )

        if best is None or score > best[2]:
            best = (segments, summary, score, coherence, rationale)

        # Pass: coherence target met and no repair items
        if (coherence >= TARGET_COHERENCE
                and not verdict.get("false_positives")
                and not verdict.get("false_negatives")):
            print(f"[tighten] passed on round {r + 1}", file=sys.stderr)
            break

        # No improvement → anti-oscillation stop
        if best[0] is not segments and score <= best[2]:
            print(f"[tighten] no improvement — stopping at round {r + 1}", file=sys.stderr)
            break

        extra = _feedback_to_instructions(verdict)

    assert best is not None
    best_segs, best_summary, _, best_coherence, best_rationale = best

    if best_coherence < FLOOR_COHERENCE:
        print(
            f"[tighten] best coherence {best_coherence} < floor {FLOOR_COHERENCE} "
            "→ shipping unedited (low confidence)",
            file=sys.stderr,
        )
        return _all_keep(whisper_segments), best_summary, True, ""

    return best_segs, best_summary, False, best_rationale


# ── Self-tests (run with: python scripts/tighten.py) ──────────────────────────

def _run_self_tests() -> None:
    PASS = "\033[32mPASS\033[0m"
    FAIL = "\033[31mFAIL\033[0m"
    errors = 0

    def check(name: str, cond: bool) -> None:
        nonlocal errors
        if cond:
            print(f"  {PASS} {name}")
        else:
            print(f"  {FAIL} {name}")
            errors += 1

    def make_words(sentence: str) -> list[dict]:
        t = 0.0
        result = []
        for w in sentence.split():
            result.append({"word": w, "start": t, "end": t + 0.5})
            t += 0.6
        return result

    def flat_from_segs(seg_word_lists: list[list[dict]]) -> list[dict]:
        flat = []
        for seg_idx, words in enumerate(seg_word_lists):
            for wi, w in enumerate(words):
                flat.append({**w, "segIdx": seg_idx, "wordIdxInSeg": wi})
        return flat

    print("\n── align_deletions tests ──")

    # Duplicate token: only one "really" deleted
    words = make_words("I really really wanted it")
    flat  = flat_from_segs([words])
    deleted, inserted = align_deletions(flat, "I really wanted it")
    check("duplicate: exactly 1 deleted", len(deleted) == 1)
    check("duplicate: 0 inserted", inserted == 0)

    # Plain filler
    words = make_words("But you know I moved")
    flat  = flat_from_segs([words])
    deleted, inserted = align_deletions(flat, "But I moved")
    check("filler: 2 deleted ('you know')", len(deleted) == 2)
    check("filler: 0 inserted", inserted == 0)

    # Paraphrase / violation: model added words
    words = make_words("But I relocated")
    flat  = flat_from_segs([words])
    deleted, inserted = align_deletions(flat, "But then I moved away")
    check("paraphrase: inserted > 0", inserted > 0)
    check("paraphrase: 'relocated' (idx 2) not cut", 2 not in deleted)

    # No-op
    words = make_words("Hello world")
    flat  = flat_from_segs([words])
    deleted, inserted = align_deletions(flat, "Hello world")
    check("no-op: 0 deleted", deleted == [])
    check("no-op: 0 inserted", inserted == 0)

    print("\n── _apply_deletions tests ──")

    seg_a = [{"word": "I",     "start": 0.0, "end": 0.5},
             {"word": "said",  "start": 0.6, "end": 1.0},
             {"word": "hello", "start": 1.1, "end": 1.5}]
    seg_b = [{"word": "and",     "start": 2.0, "end": 2.3},
             {"word": "goodbye", "start": 2.4, "end": 2.8}]
    segs = [
        {"startSec": 0.0, "endSec": 1.5, "text": "I said hello", "words": seg_a},
        {"startSec": 2.0, "endSec": 2.8, "text": "and goodbye",  "words": seg_b},
    ]
    flat = _flatten(segs)
    # Delete "hello" (global idx 2) and "and" (global idx 3)
    result = _apply_deletions(segs, flat, {2, 3})
    check("cross-seg: seg A has wordCut for 'hello'",
          any(wc["word"] == "hello" for wc in result[0].get("wordCuts", [])))
    check("cross-seg: seg B has wordCut for 'and'",
          any(wc["word"] == "and"   for wc in result[1].get("wordCuts", [])))
    check("cross-seg: both segs keep=True", result[0]["keep"] and result[1]["keep"])

    # Fully deleted segment
    seg_only = [{"startSec": 0.0, "endSec": 1.0, "text": "um uh", "words": [
        {"word": "um", "start": 0.0, "end": 0.3},
        {"word": "uh", "start": 0.4, "end": 0.7},
    ]}]
    flat2   = _flatten(seg_only)
    result2 = _apply_deletions(seg_only, flat2, {0, 1})
    check("full-delete: keep=False", result2[0]["keep"] is False)
    check("full-delete: dropReason='removed'", result2[0]["dropReason"] == "removed")
    check("output count matches input", len(result2) == 1)

    print(f"\n{'All tests passed!' if errors == 0 else f'{errors} test(s) FAILED'}\n")
    sys.exit(0 if errors == 0 else 1)


if __name__ == "__main__":
    _run_self_tests()
