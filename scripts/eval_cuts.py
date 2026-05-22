#!/usr/bin/env python3
"""
Acoustic cut quality eval.

Measures speech energy at every cut boundary in an edit plan.
High energy right at a cut point → the word was still ringing when the
scissors fell (clipping). Low energy → clean pause before the cut.

Four boundary types are checked:
  seg_end   — energy in 30 ms AFTER endSec (continuation check: speech still going in dropped content?)
  seg_start — energy in 8 ms AFTER startSec (abrupt onset: word starts before we resume?)
  wc_pre    — energy in 8 ms BEFORE the word-cut start (tail check: prior word still ringing?)
  wc_post   — energy in 8 ms AFTER the word-cut end (abrupt onset at resume point)

<silence> word cuts are skipped: they remove pauses between words,
not words themselves, so speech-before-silence is expected and normal.

Usage:
    python3 scripts/eval_cuts.py fixtures/foo.json --video video.mp4
    python3 scripts/eval_cuts.py fixtures/foo.json --video video.mp4 --threshold 3.0 --show-clean
"""
from __future__ import annotations
import argparse, json, os, sys, tempfile, subprocess
from dataclasses import dataclass, field

import numpy as np

# Must match app/api/export/route.ts
WORD_CUT_PAD_PRE  = 0.003
WORD_CUT_PAD_POST = 0.030
AUDIO_FADE        = 0.025

MEASURE_WINDOW      = 0.008  # 8 ms for wc_pre/wc_post — short enough to miss the prior word
MEASURE_WINDOW_CONT = 0.030  # 30 ms for seg_end continuation check — skips over PAD_POST dead air
NOISE_FRAME         = 0.025  # 25 ms frames for noise floor estimation
SUSPICIOUS          = 3.0    # score = boundary_rms / noise_floor ≥ this → suspicious


# ── Audio helpers ──────────────────────────────────────────────────────────────

def extract_audio(video_path: str) -> tuple[np.ndarray, int]:
    """Extract mono 16 kHz audio from video into a temporary WAV, load with librosa."""
    import librosa
    tmp = tempfile.mktemp(suffix=".wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-ac", "1", "-ar", "16000", tmp],
            check=True, capture_output=True,
        )
        y, sr = librosa.load(tmp, sr=16000, mono=True)
        return y, int(sr)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def estimate_noise_floor(y: np.ndarray, sr: int) -> float:
    """15th-percentile RMS across 25 ms frames — captures near-silence level."""
    frame_len = max(1, int(NOISE_FRAME * sr))
    n_frames  = len(y) // frame_len
    if n_frames == 0:
        return 1e-6
    frames = y[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms    = np.sqrt(np.mean(frames ** 2, axis=1))
    return float(np.percentile(rms, 15)) + 1e-8


def rms_near(y: np.ndarray, sr: int, t: float, before: bool, window: float = MEASURE_WINDOW) -> float:
    """RMS in a window immediately before (before=True) or after t."""
    n      = max(1, int(window * sr))
    center = int(t * sr)
    if before:
        seg = y[max(0, center - n) : center]
    else:
        seg = y[center : min(len(y), center + n)]
    return float(np.sqrt(np.mean(seg ** 2))) if len(seg) > 0 else 0.0


# ── Boundary model ─────────────────────────────────────────────────────────────

@dataclass
class Boundary:
    kind:    str    # seg_end | seg_start | wc_pre | wc_post
    time:    float  # seconds in original audio
    seg_idx: int
    context: str
    rms:     float = field(default=0.0, init=False)
    score:   float = field(default=0.0, init=False)

    @property
    def is_end_of_kept(self) -> bool:
        # seg_end measures AFTER the cut (continuation check), not before.
        # wc_pre measures BEFORE the cut (natural word tail check).
        return self.kind in ("wc_pre",)

    @property
    def measure_before(self) -> bool:
        """True → measure window before time; False → measure window after time."""
        return self.kind not in ("seg_end", "seg_start", "wc_post")

    @property
    def label(self) -> str:
        return {"seg_end": "seg end+ ", "seg_start": "seg start",
                "wc_pre":  "wc pre   ", "wc_post":  "wc post  "}[self.kind]


GAP_MIN = 0.05  # seconds — gaps smaller than this mean segments are adjacent (no real cut)


def collect_boundaries(plan: dict) -> list[Boundary]:
    segs = plan.get("segments", [])
    out: list[Boundary] = []

    for si, seg in enumerate(segs):
        if not seg.get("keep"):
            continue

        text    = seg.get("text", "").strip()
        t_end   = seg.get("endSec", 0.0)
        t_start = seg.get("startSec", 0.0)

        # Find the previous and next kept segment
        prev_kept = next((segs[j] for j in range(si - 1, -1, -1) if segs[j].get("keep")), None)
        next_kept = next((segs[j] for j in range(si + 1, len(segs)) if segs[j].get("keep")), None)

        gap_before = (t_start - prev_kept["endSec"]) if prev_kept else float("inf")
        gap_after  = (next_kept["startSec"] - t_end) if next_kept else float("inf")

        # seg_end: only meaningful when there's a real gap after this segment.
        # A 0-gap means the next kept segment immediately follows — speech flows
        # through, no cut happens, high energy here is expected and not clipping.
        if gap_after >= GAP_MIN:
            out.append(Boundary(
                kind="seg_end", time=t_end, seg_idx=si,
                context=f"…{text[-70:]}" if len(text) > 70 else text,
            ))

        # seg_start: only meaningful when there's a real gap before this segment.
        if gap_before >= GAP_MIN:
            out.append(Boundary(
                kind="seg_start", time=t_start, seg_idx=si,
                context=text[:70] + ("…" if len(text) > 70 else ""),
            ))

        for wc in seg.get("wordCuts") or []:
            wc_start = wc.get("startSec", 0.0)
            wc_end   = wc.get("endSec",   0.0)
            word     = wc.get("word", "?")
            src      = wc.get("source", "?")

            # Skip silence cuts: removing a pause means speech-before-silence is
            # expected. Checking energy there would always flag as suspicious.
            if word == "<silence>" or src == "silence":
                continue

            # PRE: energy at the very tail of the kept audio before this word cut.
            # We check (wc_start - PAD_PRE) — the actual cut point after padding.
            # High energy here → prior word still ringing when we cut.
            out.append(Boundary(
                kind="wc_pre",
                time=max(0.0, wc_start - WORD_CUT_PAD_PRE),
                seg_idx=si,
                context=f"[{src}] …| {word} |",
            ))

            # POST: energy at the start of the kept audio after this word cut.
            # We check (wc_end + PAD_POST) — the actual resume point after padding.
            # High energy here → next word already underway before we resume.
            out.append(Boundary(
                kind="wc_post",
                time=wc_end + WORD_CUT_PAD_POST,
                seg_idx=si,
                context=f"[{src}] | {word} |…",
            ))

    return out


def score_all(boundaries: list[Boundary], y: np.ndarray, sr: int, floor: float) -> None:
    for b in boundaries:
        # seg_end uses wider window — measures speech continuation in dropped content
        window = MEASURE_WINDOW_CONT if b.kind == "seg_end" else MEASURE_WINDOW
        b.rms   = rms_near(y, sr, b.time, before=b.measure_before, window=window)
        b.score = b.rms / floor


# ── Formatting ─────────────────────────────────────────────────────────────────

def fmt_time(t: float) -> str:
    m = int(t // 60)
    return f"{m}:{t % 60:05.2f}"


def print_row(b: Boundary) -> None:
    flag = "🔴" if b.score >= SUSPICIOUS * 2 else "🟡"
    print(f"  {flag} [{b.label}] seg {b.seg_idx:>3}  {fmt_time(b.time)}  score={b.score:5.1f}x  {b.context[:70]}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Acoustic cut quality eval")
    ap.add_argument("fixture",        help="Fixture JSON from orchestrator --save-fixture")
    ap.add_argument("--video",        required=True, help="Original video file")
    ap.add_argument("--threshold",    type=float, default=SUSPICIOUS,
                    help=f"Score threshold for suspicious (default {SUSPICIOUS})")
    ap.add_argument("--show-clean",   action="store_true", help="Also print clean boundaries")
    ap.add_argument("--end-only",     action="store_true",
                    help="Only check end-of-kept boundaries (seg_end + wc_pre)")
    args = ap.parse_args()

    with open(args.fixture) as f:
        data = json.load(f)
    plan = data.get("plan", data)
    segs = plan.get("segments", [])

    print(f"Extracting audio from {args.video} …", file=sys.stderr)
    y, sr = extract_audio(args.video)
    floor = estimate_noise_floor(y, sr)
    print(f"Noise floor: {floor:.5f} RMS ({len(y)/sr:.1f}s audio @ {sr}Hz)", file=sys.stderr)

    all_bounds = collect_boundaries(plan)
    if args.end_only:
        all_bounds = [b for b in all_bounds if b.kind in ("seg_end", "wc_pre")]
    score_all(all_bounds, y, sr, floor)

    all_bounds.sort(key=lambda b: b.score, reverse=True)
    suspicious = [b for b in all_bounds if b.score >= args.threshold]
    clean      = [b for b in all_bounds if b.score <  args.threshold]

    kept_n   = sum(1 for s in segs if s.get("keep"))
    wc_count = sum(len(s.get("wordCuts") or []) for s in segs if s.get("keep"))

    bar  = "═" * 72
    dash = "─" * 72
    print(f"\n{bar}")
    print(f"  eval_cuts — acoustic cut quality")
    print(f"  {os.path.basename(args.video)}  ·  {kept_n} kept segs  ·  {wc_count} word cuts  ·  {len(all_bounds)} boundaries")
    print(f"  noise floor {floor:.5f} RMS  ·  threshold {args.threshold}×")
    print(bar)

    # Suspicious
    if suspicious:
        n    = len(suspicious)
        pct  = n / max(len(all_bounds), 1) * 100
        print(f"\n⚠  SUSPICIOUS — {n} of {len(all_bounds)} ({pct:.0f}%)\n")
        for b in suspicious:
            print_row(b)
    else:
        print(f"\n✓  All {len(all_bounds)} boundaries clean (score < {args.threshold})")

    # Optional clean listing
    if args.show_clean and clean:
        print(f"\n✓  CLEAN — {len(clean)} boundaries\n")
        for b in clean[:30]:
            print(f"  ✓ [{b.label}] seg {b.seg_idx:>3}  {fmt_time(b.time)}  score={b.score:4.1f}x  {b.context[:60]}")
        if len(clean) > 30:
            print(f"  … and {len(clean) - 30} more")

    # Stats
    scores       = [b.score for b in all_bounds]
    cont_scores  = [b.score for b in all_bounds if b.kind == "seg_end"]   # continuation (after cut)
    wc_pre_scores= [b.score for b in all_bounds if b.kind == "wc_pre"]    # word-cut tail
    onset_scores = [b.score for b in all_bounds if b.kind in ("seg_start", "wc_post")]

    print(f"\n{dash}")
    print(f"SUMMARY")
    print(f"  boundaries checked : {len(all_bounds)}")
    print(f"  suspicious         : {len(suspicious)} ({len(suspicious)/max(len(all_bounds),1)*100:.0f}%)")
    print(f"  median score       : {np.median(scores):.1f}x   max: {max(scores):.1f}x")
    if cont_scores:
        print(f"  seg_end (after)   : {np.mean(cont_scores):.1f}x avg   ← speech continues past cut?")
    if wc_pre_scores:
        print(f"  wc_pre (before)   : {np.mean(wc_pre_scores):.1f}x avg  ← word still ringing at word-cut?")
    if onset_scores and not args.end_only:
        print(f"  onset avg         : {np.mean(onset_scores):.1f}x   (seg_start + wc_post) ← abrupt onset risk")

    # Actionable recommendations
    worst_seg_end = next((b for b in all_bounds if b.kind == "seg_end"), None)
    worst_wc_pre  = next((b for b in all_bounds if b.kind == "wc_pre"),  None)

    print(f"\nRECOMMENDATIONS")
    made = False
    if worst_seg_end and worst_seg_end.score >= args.threshold:
        made = True
        print(f"  → seg_end clipping (worst {worst_seg_end.score:.1f}x at {fmt_time(worst_seg_end.time)})")
        print(f"    Speech continues past endSec into the dropped content.")
        print(f"    Fix: surgeon.py _find_word_tail extends endSec through the word tail.")
        print(f"    If still clipping, lower TAIL_DB in surgeon.py (currently -46 dBFS).")
    if worst_wc_pre and worst_wc_pre.score >= args.threshold:
        made = True
        print(f"  → wc_pre clipping (worst {worst_wc_pre.score:.1f}x at {fmt_time(worst_wc_pre.time)})")
        print(f"    Kept audio is still ringing when a word cut starts.")
        print(f"    Fix: increase WORD_CUT_PAD_PRE in app/api/export/route.ts (currently {WORD_CUT_PAD_PRE*1000:.0f} ms).")
    if not made:
        print(f"  ✓ Nothing stands out. Run with --threshold {args.threshold/2:.1f} for stricter analysis.")

    print()


if __name__ == "__main__":
    main()
