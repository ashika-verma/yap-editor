#!/usr/bin/env python3
"""
Autonomous judge→repair loop.

Reads {"plan": dict, "maxIterations": int, "targetCoherence": int} from argv[1].
Runs up to maxIterations rounds of:
  1. Judge the current plan
  2. Apply repairs — two phases per iteration:
     Phase A (FP repair): restore cut segments that should be kept.
                          Guard: never restore a segment previously dropped by the loop.
     Phase B (FN repair): drop kept segments that should be cut.
                          Guard (Phase A active): never drop a restored segment while FPs still exist.
                          Guard relaxed: once all FPs are gone, allow dropping restored segments
                          to push coherence above target (prevents oscillation early on but
                          unlocks the final 5-point push).
  3. Stop if coherence >= targetCoherence and no false_positives remain,
     OR if no eligible repairs exist, OR if coherence hasn't changed in 3 straight iterations.

Outputs {"plan": dict, "iterations": int, "finalScore": {coherence, ...}} to stdout.
"""
from __future__ import annotations
import json, os, sys, time, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_judge import judge_plan

DEFAULT_MAX_ITER       = 20
DEFAULT_TARGET_COH     = 95  # coherence threshold to stop early


def _call_judge(current_plan: dict, api_key: str) -> dict:
    """Call judge with retry on Gemini 429.

    strict=False keeps the 90+ calibration anchor so Gemma scores realistically.
    require_repairs=True forces structured FP/FN output so the loop has targets to act on.
    """
    try:
        return judge_plan(current_plan, api_key, strict=False, require_repairs=True)
    except Exception as e:
        err_str = str(e)
        match = re.search(r"retry.*?(\d+)s", err_str, re.IGNORECASE)
        wait_sec = int(match.group(1)) + 5 if match else None
        if "429" in err_str:
            actual_wait = min(int(match.group(1)) + 5, 120) if match else 65
            print(f"[refine] rate-limited — waiting {actual_wait}s…", file=sys.stderr)
            time.sleep(actual_wait)
            try:
                return judge_plan(current_plan, api_key, strict=False, require_repairs=True)
            except Exception:
                pass  # daily quota exhausted — fall through to local backend
            # Fall back to local LLM if available
            import os as _os
            if _os.environ.get("LLM_BASE_URL"):
                print("[refine] Gemini quota exhausted — falling back to local LLM", file=sys.stderr)
                return judge_plan(current_plan, "", strict=False, require_repairs=True)
        raise


def refine(plan: dict, max_iterations: int, target_coherence: int, api_key: str) -> dict:
    segments = [dict(s) for s in plan.get("segments", [])]
    restored: set[int] = set()  # indices restored by this loop
    dropped:  set[int] = set()  # indices dropped by this loop
    last_score: dict   = {}
    coh_history: list[int] = []
    iterations = 0
    # Track the plan state that achieved the best coherence score
    best_coh  = -1
    best_segs = segments[:]
    best_score: dict = {}

    for i in range(max_iterations):
        iterations = i + 1
        current_plan = {**plan, "segments": segments}
        print(f"[refine] iteration {iterations}…", file=sys.stderr)

        try:
            score = _call_judge(current_plan, api_key)
        except Exception as e:
            print(f"[refine] judge failed: {e}", file=sys.stderr)
            break

        last_score = score
        coh = score.get("coherence", 0)
        fps = score.get("false_positives", [])
        fns = score.get("false_negatives", [])
        coh_history.append(coh)

        if coh > best_coh:
            best_coh  = coh
            best_segs = [dict(s) for s in segments]
            best_score = score

        print(f"[refine]   coh={coh}  fp={len(fps)}  fn={len(fns)}", file=sys.stderr)

        # Converged: at target with no missing context
        if coh >= target_coherence and not fps:
            print("[refine] converged — stopping early", file=sys.stderr)
            break

        # Stall: coherence unchanged for 3 straight iterations
        if len(coh_history) >= 3 and len(set(coh_history[-3:])) == 1:
            print("[refine] stalled — coherence flat for 3 iterations", file=sys.stderr)
            break

        made_repair = False
        all_fps_resolved = len(fps) == 0

        # Phase A: Restore false positives
        for fp in fps:
            idx = fp.get("segment_index")
            if idx is None or idx >= len(segments):
                continue
            if idx in dropped:
                continue  # never restore what we dropped
            if not segments[idx].get("keep"):
                segments[idx] = {**segments[idx], "keep": True,
                                 "decisionSource": "repair", "dropReason": ""}
                restored.add(idx)
                made_repair = True

        # Phase B: Drop false negatives
        # Guard relaxed once FPs are gone — allows the final push above target
        for fn in fns:
            idx = fn.get("segment_index")
            if idx is None or idx >= len(segments):
                continue
            if idx in restored and not all_fps_resolved:
                continue  # guard active while FPs still exist
            if idx in dropped:
                continue  # never re-drop what we already dropped
            if segments[idx].get("keep"):
                segments[idx] = {**segments[idx], "keep": False,
                                 "decisionSource": "repair", "dropReason": "judge"}
                dropped.add(idx)
                made_repair = True

        if not made_repair:
            print("[refine] no eligible repairs — stopping", file=sys.stderr)
            break

    # Return the best plan found across all iterations, not just the final state.
    # This guards against over-correction — if an early iteration scored higher,
    # we keep that plan rather than the one that stalled or regressed.
    return_segs  = best_segs  if best_coh > last_score.get("coherence", 0) else segments
    return_score = best_score if best_coh > last_score.get("coherence", 0) else last_score
    return {
        "plan": {**plan, "segments": return_segs},
        "iterations": iterations,
        "finalScore": return_score,
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
