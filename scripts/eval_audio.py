#!/usr/bin/env python3
"""
Deterministic audio quality scorer for Yap Editor edit plans.

Mirrors the TypeScript computeKeepSpans logic (both old buggy and new fixed)
to measure:
  PRE-FIX  — how many ms of speech the old code would have clipped
  POST-FIX — output quality after the acoustic-boundary fix:
               span count, fade coverage, orphaned micro-spans

No LLM required. Called by eval.py or standalone.
"""
from __future__ import annotations
import json, sys, glob
from pathlib import Path
from dataclasses import dataclass, field

# Mirror constants from app/api/export/route.ts
WORD_CUT_PAD_PRE   = 0.003
WORD_CUT_PAD_POST  = 0.030
MIN_SPAN           = 0.05
AUDIO_FADE_INNER   = 0.025  # ms fade at word-cut seams


@dataclass
class SegmentAudio:
    seg_idx: int
    # Pre-fix: would have clipped these amounts (ms)
    pre_clip_ms: float = 0.0    # ms clipped from preceding word
    post_clip_ms: float = 0.0   # ms clipped from following word
    # Post-fix: span quality
    spans_produced: int = 0
    orphaned_ms: float = 0.0    # speech lost to MIN_SPAN filter
    underfaded_spans: int = 0   # spans too short for their fades


@dataclass
class AudioReport:
    fixture_path: str
    segments: list[SegmentAudio] = field(default_factory=list)
    total_word_cuts: int = 0

    # ── Pre-fix metrics ────────────────────────────────────────────────
    @property
    def pre_clip_violations(self) -> int:
        return sum(1 for s in self.segments if s.pre_clip_ms > 0 or s.post_clip_ms > 0)

    @property
    def pre_max_clip_ms(self) -> float:
        return max((max(s.pre_clip_ms, s.post_clip_ms) for s in self.segments), default=0.0)

    @property
    def pre_avg_clip_ms(self) -> float:
        clips = [max(s.pre_clip_ms, s.post_clip_ms) for s in self.segments
                 if s.pre_clip_ms > 0 or s.post_clip_ms > 0]
        return sum(clips) / len(clips) if clips else 0.0

    # ── Post-fix metrics ───────────────────────────────────────────────
    @property
    def post_orphaned_ms(self) -> float:
        return sum(s.orphaned_ms for s in self.segments)

    @property
    def post_underfaded(self) -> int:
        return sum(s.underfaded_spans for s in self.segments)

    @property
    def post_fix_score(self) -> int:
        """
        0–100. Score of what the FIXED code will actually produce.
        Deductions: orphaned speech (inaudible micro-gaps) and underfaded spans (click risk).
        With good data this should be 90+.
        """
        if self.total_word_cuts == 0:
            return 100
        orphan_penalty = min(self.post_orphaned_ms / (self.total_word_cuts * 50.0), 1.0)
        fade_penalty   = min(self.post_underfaded   / max(self.total_word_cuts, 1), 0.5)
        raw = 1.0 - (0.6 * orphan_penalty + 0.4 * fade_penalty)
        return max(0, min(100, round(raw * 100)))


def _score_segment(
    seg_idx: int,
    range_start: float,
    range_end: float,
    word_cuts: list[dict],
) -> SegmentAudio:
    result = SegmentAudio(seg_idx=seg_idx)

    zones_fixed: list[tuple[float, float]] = []

    for wc in word_cuts:
        acoustic_start = wc["startSec"]
        acoustic_end   = wc["endSec"]
        render_start   = wc.get("renderStartSec", acoustic_start)
        render_end     = wc.get("renderEndSec",   acoustic_end)

        s_old = render_start - WORD_CUT_PAD_PRE
        e_old = render_end   + WORD_CUT_PAD_POST

        # Pre-fix clip amounts
        if s_old < acoustic_start:
            result.pre_clip_ms = max(result.pre_clip_ms, (acoustic_start - s_old) * 1000)
        if e_old > acoustic_end:
            result.post_clip_ms = max(result.post_clip_ms, (e_old - acoustic_end) * 1000)

        # Fixed zone — capped by acoustic boundaries
        s_new = max(s_old, acoustic_start, range_start)
        e_new = min(e_old, acoustic_end,   range_end)
        if e_new > s_new:
            zones_fixed.append((s_new, e_new))

    zones_fixed.sort()

    # Simulate span production with fixed zones
    cursor = range_start
    for (zs, ze) in zones_fixed:
        gap = zs - cursor
        if gap >= MIN_SPAN:
            result.spans_produced += 1
            dur_ms = gap * 1000
            fade_budget_ms = AUDIO_FADE_INNER * 2 * 1000
            if dur_ms < fade_budget_ms:
                result.underfaded_spans += 1
        elif gap > 0:
            result.orphaned_ms += gap * 1000
        cursor = max(cursor, ze)

    tail = range_end - cursor
    if tail >= MIN_SPAN:
        result.spans_produced += 1
        if tail * 1000 < AUDIO_FADE_INNER * 2 * 1000:
            result.underfaded_spans += 1
    elif tail > 0:
        result.orphaned_ms += tail * 1000

    return result


def score_fixture(fixture_path: str) -> AudioReport:
    with open(fixture_path) as f:
        data = json.load(f)
    plan = data.get("plan", data)
    segments = plan.get("segments", [])

    report = AudioReport(fixture_path=fixture_path)
    for seg_idx, seg in enumerate(segments):
        if not seg.get("keep"):
            continue
        word_cuts = seg.get("wordCuts") or []
        if not word_cuts:
            continue
        report.total_word_cuts += len(word_cuts)
        sa = _score_segment(seg_idx, seg["startSec"], seg["endSec"], word_cuts)
        report.segments.append(sa)

    return report


def main(argv: list[str]) -> None:
    if not argv:
        fixture_dir = Path(__file__).parent.parent / "fixtures"
        paths = sorted(glob.glob(str(fixture_dir / "*.json")))
    else:
        paths = argv

    if not paths:
        print("No fixtures found.")
        sys.exit(1)

    print(f"\n{'─'*80}")
    print(f"  {'Fixture':<32}  {'Post-fix':>8}  {'WC':>4}  {'Max clip (pre)':>14}  {'Orphaned':>8}")
    print(f"{'─'*80}")

    all_scores: list[int] = []
    for path in paths:
        try:
            r = score_fixture(path)
        except Exception as e:
            print(f"  ERROR {Path(path).name}: {e}")
            continue
        name = Path(path).name[:32]
        max_clip = f"{r.pre_max_clip_ms:.0f}ms" if r.pre_max_clip_ms > 0 else "  0ms"
        orphaned = f"{r.post_orphaned_ms:.0f}ms" if r.post_orphaned_ms > 0 else "  0ms"
        all_scores.append(r.post_fix_score)
        print(f"  {name:<32}  {r.post_fix_score:>3}/100   {r.total_word_cuts:>4}  {max_clip:>14}  {orphaned:>8}")

    print(f"{'─'*80}")
    if all_scores:
        avg = sum(all_scores) / len(all_scores)
        print(f"  {'Average':<32}  {avg:>3.0f}/100\n")
        print(f"  pre-fix column = speech that WOULD have been clipped (now fixed)")
        print(f"  orphaned = speech lost to {MIN_SPAN*1000:.0f}ms min-span filter\n")


if __name__ == "__main__":
    main(sys.argv[1:])
