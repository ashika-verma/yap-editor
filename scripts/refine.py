#!/usr/bin/env python3
"""
Autonomous judge→repair loop.

Reads {"plan": dict, "maxIterations": int, "targetCoherence": int} from argv[1].
Runs up to maxIterations rounds of:
  1. Judge the current plan
  2. Apply false_positive repairs (restore) + false_negative repairs (drop)
  3. Stop if coherence >= targetCoherence and no false_positives remain

Tracks restored/dropped sets to prevent oscillation — a segment restored in
an earlier iteration won't be dropped in a later one, and vice versa.

Outputs {"plan": dict, "iterations": int, "finalScore": {coherence, ...}} to stdout.
"""
from __future__ import annotations
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_judge import judge_plan

DEFAULT_MAX_ITER       = 20
DEFAULT_TARGET_COH     = 85  # coherence threshold to stop early


def refine(plan: dict, max_iterations: int, target_coherence: int, api_key: str) -> dict:
    segments = [dict(s) for s in plan.get("segments", [])]  # deep-ish copy
    restored: set[int] = set()  # indices restored by judge
    dropped:  set[int] = set()  # indices dropped by judge
    last_score: dict   = {}
    iterations = 0

    for i in range(max_iterations):
        iterations = i + 1
        current_plan = {**plan, "segments": segments}
        print(f"[refine] iteration {iterations}…", file=sys.stderr)

        try:
            score = judge_plan(current_plan, api_key)
        except Exception as e:
            print(f"[refine] judge failed: {e}", file=sys.stderr)
            break

        last_score = score
        coh = score.get("coherence", 0)
        fps = score.get("false_positives", [])
        fns = score.get("false_negatives", [])

        print(
            f"[refine]   coh={coh}  fp={len(fps)}  fn={len(fns)}",
            file=sys.stderr,
        )

        if coh >= target_coherence and not fps:
            print("[refine] converged — stopping early", file=sys.stderr)
            break

        made_repair = False

        # Restore false positives (were cut, should be kept)
        for fp in fps:
            idx = fp.get("segment_index")
            if idx is None or idx >= len(segments):
                continue
            if idx in dropped:
                continue  # oscillation guard: we previously dropped this
            if not segments[idx].get("keep"):
                segments[idx] = {
                    **segments[idx],
                    "keep": True,
                    "decisionSource": "repair",
                    "dropReason": "",
                }
                restored.add(idx)
                made_repair = True

        # Drop false negatives (were kept, should be cut)
        for fn in fns:
            idx = fn.get("segment_index")
            if idx is None or idx >= len(segments):
                continue
            if idx in restored:
                continue  # oscillation guard: we previously restored this
            if segments[idx].get("keep"):
                segments[idx] = {
                    **segments[idx],
                    "keep": False,
                    "decisionSource": "repair",
                    "dropReason": "judge",
                }
                dropped.add(idx)
                made_repair = True

        if not made_repair:
            print("[refine] no repairs to apply — stopping", file=sys.stderr)
            break

    return {
        "plan": {**plan, "segments": segments},
        "iterations": iterations,
        "finalScore": last_score,
    }


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: refine.py <payload.json>"}))
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        payload = json.load(f)

    plan             = payload.get("plan") or {}
    max_iterations   = int(payload.get("maxIterations", DEFAULT_MAX_ITER))
    target_coherence = int(payload.get("targetCoherence", DEFAULT_TARGET_COH))
    api_key          = os.environ.get("GEMINI_API_KEY", "")

    result = refine(plan, max_iterations, target_coherence, api_key)
    sys.stdout.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
