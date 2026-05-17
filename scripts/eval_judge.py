#!/usr/bin/env python3
"""
LLM-as-judge for Yap Editor edit plans.

Takes an edit plan (segments with keep/drop decisions) and scores it on
three dimensions using Gemini. Called by eval.py.
"""
from __future__ import annotations
import json

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "coherence":            {"type": "integer", "minimum": 0, "maximum": 100},
        "preservation":         {"type": "integer", "minimum": 0, "maximum": 100},
        "conciseness":          {"type": "integer", "minimum": 0, "maximum": 100},
        "coherence_reason":     {"type": "string"},
        "preservation_reason":  {"type": "string"},
        "conciseness_reason":   {"type": "string"},
        "false_positives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "segment_index": {"type": "integer"},
                    "text":          {"type": "string"},
                    "reason":        {"type": "string"},
                },
                "required": ["segment_index", "text", "reason"],
            },
        },
        "false_negatives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "segment_index": {"type": "integer"},
                    "text":          {"type": "string"},
                    "reason":        {"type": "string"},
                },
                "required": ["segment_index", "text", "reason"],
            },
        },
        "overall_notes": {"type": "string"},
    },
    "required": [
        "coherence", "preservation", "conciseness",
        "coherence_reason", "preservation_reason", "conciseness_reason",
        "false_positives", "false_negatives", "overall_notes",
    ],
}


def _build_prompt(plan: dict, strict: bool = False, require_repairs: bool = True) -> str:
    segments  = plan.get("segments", [])
    n_total   = len(segments)
    n_kept    = sum(1 for s in segments if s.get("keep"))
    n_cut     = n_total - n_kept
    pct_cut   = round(n_cut / max(n_total, 1) * 100)
    summary   = plan.get("summary", "(none)")
    total_dur = plan.get("totalDuration", "?")
    edited_dur = plan.get("editedDuration", "?")
    content_type = plan.get("directorConfig", {}).get("content_type", "unknown")

    original_lines = [f"[{i}] {s.get('text','').strip()}" for i, s in enumerate(segments)]
    kept_indices   = {i for i, s in enumerate(segments) if s.get("keep")}
    kept_lines     = [f"[{i}] {s.get('text','').strip()}" for i, s in enumerate(segments) if s.get("keep")]
    drop_lines     = [
        f"[{i}] ({s.get('dropReason','?')}) {s.get('text','').strip()}"
        for i, s in enumerate(segments) if not s.get("keep")
    ]
    kept_idx_hint  = f"Kept segment indices (only these can be false negatives): {sorted(kept_indices)}"

    return f"""You are an expert video editor evaluating an AI-generated edit plan for a {content_type} video.
{kept_idx_hint}

Video stats: {total_dur} original → {edited_dur} edited ({pct_cut}% cut, {n_cut}/{n_total} segments removed)
AI summary of the edit: "{summary}"

═══ ORIGINAL TRANSCRIPT ({n_total} segments) ═══
{chr(10).join(original_lines)}

═══ EDITED TRANSCRIPT ({n_kept} segments kept) ═══
{chr(10).join(kept_lines)}

═══ DROP DECISIONS ({n_cut} removed) ═══
{chr(10).join(drop_lines)}

Score each dimension 0–100. Use the FULL RANGE — scores of 95+ and scores of 40 or below are both valid and expected when the edit warrants it.

Calibration examples for COHERENCE:

  95–100  Every join feels intentional. Speaker's train of thought is never
          interrupted mid-point. All context needed to understand each kept segment
          is available from earlier kept segments. Zero abrupt jumps.
          Example: cuts only remove off-topic asides; the argument or story arc
          is complete and logical in the final edit.

  85–92   One or two joins feel slightly abrupt — a word or phrase from the end of
          one thought is missing, or a new topic starts without 1–2 words of
          transition. Viewer recovers within a sentence. The overall story is intact.
          Example: speaker's intro sentence for a new point was cut, so the point
          lands slightly suddenly, but the viewer still understands it.

  70–84   Multiple (3–5) joins have missing context. Viewer has to infer connections
          or re-read. The core message survives but feels patchy.

  50–69   Frequent broken joins. Viewer frequently loses the thread between segments.

  0–49    Incoherent. Major portions of context are missing. Viewer cannot follow.

Fine-grained scores (e.g. 72, 88, 95) are expected and preferred.
{"" if strict else ""}

COHERENCE — Does the edited transcript flow naturally as a continuous narrative?
  Watch for: abrupt jumps, missing context, broken sentences at joins, mid-thought cuts.

PRESERVATION — Are the core ideas and the main story still intact in the edit?
  Watch for: key points removed, essential context missing, the central argument surviving.

CONCISENESS — Is the cut level appropriate for the content?
  Watch for: over-cutting (loses meaning or sounds choppy), under-cutting (obvious flab remains).

{"REQUIRED — cite up to 3 of each. If coherence < 90, you MUST find at least one:" if require_repairs else "Also identify (max 3 each):"}
- FALSE POSITIVES: specific segments that were CUT but should have been KEPT.
  These are joins where context is missing because a segment was removed. Give the segment
  index from the original transcript, a short quote, and exactly why restoring it would
  fix a specific join or missing context.
- FALSE NEGATIVES: specific segments that were KEPT but should have been CUT.
  These are segments that add no information, repeat something already said, or interrupt
  flow. Give the segment index, a short quote, and exactly why cutting it improves the edit.
{"If you noted an abrupt jump or missing context in overall_notes, trace it to a specific dropped segment index and list it as a false positive." if require_repairs else ""}

Note in overall_notes any systemic patterns (e.g. "consistently cuts too early before a point
lands" or "misses repetitions")."""


def judge_plan(
    plan: dict,
    api_key: str,
    model: str = "gemini-2.5-flash",
    strict: bool = False,
    require_repairs: bool = True,
) -> dict:
    """Score an edit plan. Returns a dict matching JUDGE_SCHEMA."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import llm

    text = llm.generate(
        _build_prompt(plan, strict=strict, require_repairs=require_repairs),
        schema=JUDGE_SCHEMA, model=model, api_key=api_key,
    )
    return json.loads(text)
