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


def _build_prompt(plan: dict) -> str:
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
    kept_lines     = [f"[{i}] {s.get('text','').strip()}" for i, s in enumerate(segments) if s.get("keep")]
    drop_lines     = [
        f"[{i}] ({s.get('dropReason','?')}) {s.get('text','').strip()}"
        for i, s in enumerate(segments) if not s.get("keep")
    ]

    return f"""You are an expert video editor evaluating an AI-generated edit plan for a {content_type} video.

Video stats: {total_dur} original → {edited_dur} edited ({pct_cut}% cut, {n_cut}/{n_total} segments removed)
AI summary of the edit: "{summary}"

═══ ORIGINAL TRANSCRIPT ({n_total} segments) ═══
{chr(10).join(original_lines)}

═══ EDITED TRANSCRIPT ({n_kept} segments kept) ═══
{chr(10).join(kept_lines)}

═══ DROP DECISIONS ({n_cut} removed) ═══
{chr(10).join(drop_lines)}

Score each dimension 0–100 using these anchors:

  90–100  Excellent — the narrative flows well and the viewer stays oriented
          throughout; cuts are detectable but non-disruptive; no lost context
  75–89   Good — 2–3 moments where the viewer briefly loses the thread or
          context feels missing, but recovers quickly
  60–74   Acceptable — several transitions cause real confusion; viewer has
          to work to stay oriented
  40–59   Mixed — multiple broken joins; viewer loses context repeatedly
  20–39   Poor — many broken joins; meaning frequently unclear
  0–19    Very poor — incoherent; viewer is lost throughout

An edit that removes clear tangents and filler while preserving the main
narrative, with at most minor roughness at a few joins, should score 90+.
Use the full range. Fine-grained scores (e.g. 72, 88, 95) are expected and preferred.

COHERENCE — Does the edited transcript flow naturally as a continuous narrative?
  Watch for: abrupt jumps, missing context, broken sentences at joins, mid-thought cuts.

PRESERVATION — Are the core ideas and the main story still intact in the edit?
  Watch for: key points removed, essential context missing, the central argument surviving.

CONCISENESS — Is the cut level appropriate for the content?
  Watch for: over-cutting (loses meaning or sounds choppy), under-cutting (obvious flab remains).

Also identify (max 3 each):
- FALSE POSITIVES: segments that were CUT but should have been KEPT
  (genuine content incorrectly removed — cite segment index, short quote, and why it matters)
- FALSE NEGATIVES: segments that were KEPT but should have been CUT
  (clear flab that survived — cite segment index, short quote, and why it adds nothing)

Note in overall_notes any systemic patterns (e.g. "consistently cuts too early before a point
lands" or "misses repetitions")."""


def judge_plan(plan: dict, api_key: str, model: str = "gemini-2.5-flash") -> dict:
    """Score an edit plan. Returns a dict matching JUDGE_SCHEMA."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import llm

    text = llm.generate(_build_prompt(plan), schema=JUDGE_SCHEMA, model=model, api_key=api_key)
    return json.loads(text)
