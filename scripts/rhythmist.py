#!/usr/bin/env python3
"""
The Rhythmist: adds J-cuts, L-cuts, audio ducking, and zoom hints.

Imported by orchestrator.py; not meant to be run standalone.

J-cut  — audio of the INCOMING segment starts before the video cut.
          Creates anticipation at rising-energy transitions.
L-cut  — audio of the OUTGOING segment lingers into the next visual.
          Creates continuity at falling-energy transitions.
Ducking — attenuate audio on high-motion / low-speech segments (B-roll feel).
Zoom   — subtle punch-in hint on high-energy moments (applied in export).
"""
from __future__ import annotations

J_CUT_SEC  = 0.35   # default J-cut lead (audio starts this many seconds early)
L_CUT_SEC  = 0.30   # default L-cut tail (audio lingers this many seconds longer)
DUCK_LEVEL = 0.45   # volume multiplier for ducked segments


def _avg_energy(buckets: list[dict], start: float, end: float) -> float:
    """Average weighted energy score over a time range."""
    if not buckets:
        return 0.0
    segs = [b for b in buckets if start <= b["t"] < end]
    if not segs:
        # Fall back to nearest bucket
        idx = min(max(int(start), 0), len(buckets) - 1)
        b   = buckets[idx]
        segs = [b]
    return sum(
        0.50 * b.get("motion_score", 0)
        + 0.40 * b.get("audio_rms", 0)
        + 0.10 * b.get("pitch_delta", 0)
        for b in segs
    ) / len(segs)


def _zoom_hint(buckets: list[dict], start: float, end: float) -> dict | None:
    """Return a subtle punch-in zoom hint if peak energy > 0.6, else None."""
    segs = [b for b in buckets if start <= b["t"] < end]
    if not segs:
        return None
    peak = max(
        0.50 * b.get("motion_score", 0) + 0.40 * b.get("audio_rms", 0)
        for b in segs
    )
    if peak < 0.60:
        return None
    return {"startScale": 1.0, "endScale": 1.06, "x": 0.5, "y": 0.45}  # subtle punch-in


def apply_rhythm(
    segments: list[dict],
    buckets: list[dict],
    j_cut_threshold: float = 0.30,
    l_cut_threshold: float = 0.30,
    ducking_enabled: bool = True,
) -> list[dict]:
    """
    Annotate each segment with:
      transition  : {type: "cut"|"j-cut"|"l-cut", offsetSec: float}
      lCutTailSec : float  (seconds of audio tail from the L-cut decision on prev segment)
      duckLevel   : float  (1.0 = full volume; < 1.0 = ducked)
      zoomHint    : {startScale, endScale, x, y} | None

    Returns a new list; input segments are not mutated.
    """
    result = [dict(s) for s in segments]
    kept_indices = [i for i, s in enumerate(result) if s.get("keep", True)]

    for pos, idx in enumerate(kept_indices):
        seg = result[idx]

        # ── Duck audio on high-motion / low-speech (B-roll) ──────────────────
        motion = seg.get("motionScore", 0)
        rms    = seg.get("audioRms", 0)
        if ducking_enabled and motion > 0.60 and rms < 0.35:
            seg["duckLevel"] = DUCK_LEVEL
        else:
            seg["duckLevel"] = 1.0

        # ── Zoom hint ─────────────────────────────────────────────────────────
        seg["zoomHint"] = _zoom_hint(buckets, seg["startSec"], seg["endSec"])

        # ── Transition type (from previous kept segment) ──────────────────────
        if pos == 0:
            seg["transition"]   = {"type": "cut", "offsetSec": 0.0}
            seg["lCutTailSec"]  = 0.0
            continue

        prev_idx = kept_indices[pos - 1]
        prev_seg = result[prev_idx]

        energy_prev = _avg_energy(buckets, prev_seg["startSec"], prev_seg["endSec"])
        energy_curr = _avg_energy(buckets, seg["startSec"], seg["endSec"])
        delta = energy_curr - energy_prev

        if delta > j_cut_threshold:
            # Rising energy → J-cut: audio of current segment starts early
            seg["transition"]  = {"type": "j-cut", "offsetSec": J_CUT_SEC}
            seg["lCutTailSec"] = 0.0
        elif delta < -l_cut_threshold:
            # Falling energy → L-cut: previous segment's audio lingers
            prev_seg["lCutTailSec"] = L_CUT_SEC
            seg["transition"]        = {"type": "l-cut", "offsetSec": L_CUT_SEC}
            seg["lCutTailSec"]       = 0.0
        else:
            seg["transition"]  = {"type": "cut", "offsetSec": 0.0}
            seg["lCutTailSec"] = 0.0

    return result
