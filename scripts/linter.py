#!/usr/bin/env python3
"""
Integrity Linter: deterministic QA pass over the final edit plan.

Imported by orchestrator.py; not meant to be run standalone.

Checks:
  1. Minimum segment duration (> 150 ms)
  2. Temporal overlap between consecutive kept segments
  3. Audio RMS clipping risk (> 0.95)
  4. J-cut offset exceeding segment duration
  5. Gap warnings (> 500 ms gap between kept segments — not an error, just advisory)

Returns: {"passed": bool, "issues": [...], "segments": [...]}

"passed" is False only on hard errors (overlap, too_short, invalid_j_cut).
Gap and clipping issues are advisory ("warning" severity).
"""
from __future__ import annotations

MIN_DUR      = 0.15   # seconds — discard/warn on shorter kept segments
MAX_GAP_WARN = 0.50   # seconds — gap larger than this gets a warning (not error)
CLIP_RMS     = 0.95   # normalized RMS above this risks clipping


def lint(segments: list[dict], duration: float) -> dict:
    issues: list[dict] = []
    kept = [s for s in segments if s.get("keep", True)]

    for pos, seg in enumerate(kept):
        seg_dur = seg["endSec"] - seg["startSec"]
        global_idx = segments.index(seg)

        # ── 1. Too short ──────────────────────────────────────────────────────
        if seg_dur < MIN_DUR:
            issues.append({
                "severity": "error",
                "type": "too_short",
                "segIdx": global_idx,
                "detail": f"{seg_dur:.3f}s — may produce a click or flash frame",
            })

        # ── 2. Overlap with next kept segment ─────────────────────────────────
        if pos + 1 < len(kept):
            nxt = kept[pos + 1]
            gap = nxt["startSec"] - seg["endSec"]
            if gap < 0:
                issues.append({
                    "severity": "error",
                    "type": "overlap",
                    "segIdx": global_idx,
                    "detail": f"overlaps next segment by {-gap:.3f}s",
                })
            elif gap > MAX_GAP_WARN:
                issues.append({
                    "severity": "warning",
                    "type": "gap",
                    "segIdx": global_idx,
                    "detail": f"gap of {gap:.2f}s to next segment",
                })

        # ── 3. Audio clipping risk ────────────────────────────────────────────
        if seg.get("audioRms", 0) > CLIP_RMS:
            issues.append({
                "severity": "warning",
                "type": "clipping",
                "segIdx": global_idx,
                "detail": f"audio RMS {seg['audioRms']:.2f} — possible digital clipping",
            })

        # ── 4. J-cut offset sanity ────────────────────────────────────────────
        t = seg.get("transition", {})
        if t.get("type") == "j-cut" and t.get("offsetSec", 0) > seg_dur:
            issues.append({
                "severity": "error",
                "type": "invalid_j_cut",
                "segIdx": global_idx,
                "detail": (
                    f"J-cut offset {t['offsetSec']:.2f}s exceeds segment "
                    f"duration {seg_dur:.2f}s — clamped to 0"
                ),
            })
            # Auto-fix: downgrade to hard cut
            seg["transition"] = {"type": "cut", "offsetSec": 0.0}

    errors = [i for i in issues if i["severity"] == "error"]
    return {
        "passed": len(errors) == 0,
        "issues": issues,
        "segments": segments,
    }
