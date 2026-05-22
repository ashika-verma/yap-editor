#!/usr/bin/env python3
"""
Apply surgeon.py's segment boundary snapping to an existing fixture.

Tests _find_word_tail / _find_speech_onset without re-running the full pipeline.
Outputs a <fixture>_patched.json and prints its path to stdout.

Usage:
    .venv/bin/python3 scripts/patch_fixture.py fixtures/foo.json --video video.mp4
    .venv/bin/python3 scripts/patch_fixture.py fixtures/foo.json --video video.mp4 --out /tmp/test.json
"""
from __future__ import annotations
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from surgeon import load_audio_from_video, _find_word_tail, _find_speech_onset, BUDGET_SEC

GAP_MIN = 0.05


def patch_boundaries(plan: dict, audio, sr: int) -> tuple[dict, int, int]:
    segs = list(plan.get("segments", []))
    result = [dict(s) for s in segs]
    end_n = start_n = 0

    for i, seg in enumerate(result):
        if not seg.get("keep"):
            continue

        prev_kept = next((result[j] for j in range(i - 1, -1, -1) if result[j].get("keep")), None)
        next_kept = next((result[j] for j in range(i + 1, len(result)) if result[j].get("keep")), None)

        gap_before = (seg["startSec"] - prev_kept["endSec"]) if prev_kept else float("inf")
        gap_after  = (next_kept["startSec"] - seg["endSec"]) if next_kept else float("inf")

        if gap_after >= GAP_MIN:
            cap     = (next_kept["startSec"] - 0.003) if next_kept else float("inf")
            new_end = _find_word_tail(audio, sr, seg["endSec"], max_extend=0.25)
            new_end = min(new_end, cap)
            if abs(new_end - seg["endSec"]) > 0.001:
                print(
                    f"  endSec  seg {i:>3}: {seg['endSec']:.3f} → {new_end:.3f} "
                    f"(+{(new_end - seg['endSec'])*1000:.0f} ms)",
                    file=sys.stderr,
                )
                result[i]["endSec"] = round(new_end, 4)
                end_n += 1

        if gap_before >= GAP_MIN:
            floor     = (prev_kept["endSec"] + 0.003) if prev_kept else 0.0
            new_start = _find_speech_onset(audio, sr, seg["startSec"], BUDGET_SEC)
            new_start = max(new_start, floor)
            if abs(new_start - seg["startSec"]) > 0.001:
                print(
                    f"  startSec seg {i:>3}: {seg['startSec']:.3f} → {new_start:.3f} "
                    f"({'−' if new_start < seg['startSec'] else '+'}{abs(new_start - seg['startSec'])*1000:.0f} ms)",
                    file=sys.stderr,
                )
                result[i]["startSec"] = round(new_start, 4)
                start_n += 1

    return {**plan, "segments": result}, end_n, start_n


def main() -> None:
    ap = argparse.ArgumentParser(description="Patch segment boundaries in a fixture")
    ap.add_argument("fixture",        help="Fixture JSON (from orchestrator --save-fixture)")
    ap.add_argument("--video",        required=True, help="Original video file")
    ap.add_argument("--out",          help="Output path (default: <fixture>_patched.json)")
    args = ap.parse_args()

    with open(args.fixture) as f:
        data = json.load(f)
    plan       = data.get("plan", data)
    is_wrapped = "plan" in data

    print(f"Loading audio from {args.video} …", file=sys.stderr)
    audio, sr = load_audio_from_video(args.video)
    print(f"Audio: {len(audio)/sr:.1f}s @ {sr}Hz", file=sys.stderr)

    patched_plan, end_n, start_n = patch_boundaries(plan, audio, sr)

    kept = sum(1 for s in plan.get("segments", []) if s.get("keep"))
    print(
        f"\nPatched {end_n} endSec + {start_n} startSec boundaries "
        f"({kept} kept segs total)",
        file=sys.stderr,
    )

    out_path = args.out or args.fixture.replace(".json", "_patched.json")
    output   = {"plan": patched_plan, "metadata": data.get("metadata", {})} if is_wrapped else patched_plan
    with open(out_path, "w") as f:
        json.dump(output, f)
    print(f"Saved → {out_path}", file=sys.stderr)
    print(out_path)  # stdout so caller can pipe it


if __name__ == "__main__":
    main()
