#!/usr/bin/env python3
"""
Fixture-based eval runner for Yap Editor.

Usage:
  python scripts/eval.py                       # eval all fixtures/
  python scripts/eval.py fixtures/foo.json     # eval a specific fixture
  python scripts/eval.py --model gemini-2.5-pro  # use a different judge model
"""
from __future__ import annotations
import sys, json, os, glob
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_judge import judge_plan

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
RESULTS_DIR  = Path(__file__).parent.parent / "eval_results"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _score_bar(n: int) -> str:
    return "█" * n + "░" * (5 - n)

def _avg(scores: list[int]) -> float:
    return round(sum(scores) / len(scores), 1) if scores else 0.0

def _cut_pct(plan: dict) -> int:
    segs = plan.get("segments", [])
    n_cut = sum(1 for s in segs if not s.get("keep"))
    return round(n_cut / max(len(segs), 1) * 100)


# ── Load fixtures ─────────────────────────────────────────────────────────────

def load_fixtures(targets: list[str]) -> list[tuple[str, dict]]:
    paths = targets if targets else sorted(glob.glob(str(FIXTURES_DIR / "*.json")))
    out = []
    for p in paths:
        try:
            raw = json.load(open(p))
            # Support both bare plan and {metadata, plan} fixture format
            plan = raw.get("plan", raw)
            out.append((p, plan))
        except Exception as e:
            print(f"  SKIP {p}: {e}", file=sys.stderr)
    return out


# ── Run evals ────────────────────────────────────────────────────────────────

def run_eval(fixtures: list[tuple[str, dict]], api_key: str, model: str) -> list[dict]:
    results = []

    for path, plan in fixtures:
        name = Path(path).stem
        segs = plan.get("segments", [])
        n_kept = sum(1 for s in segs if s.get("keep"))
        cut = _cut_pct(plan)

        print(f"\n── {name}")
        print(f"   {len(segs)} segments · {n_kept} kept · {cut}% cut · "
              f"{plan.get('totalDuration','?')} → {plan.get('editedDuration','?')}")

        try:
            score = judge_plan(plan, api_key, model=model)
        except Exception as e:
            print(f"   Judge failed: {e}")
            continue

        coh = score["coherence"]
        pre = score["preservation"]
        con = score["conciseness"]
        avg = _avg([coh, pre, con])

        print(f"   Coherence    {_score_bar(coh)} {coh}/5  {score['coherence_reason']}")
        print(f"   Preservation {_score_bar(pre)} {pre}/5  {score['preservation_reason']}")
        print(f"   Conciseness  {_score_bar(con)} {con}/5  {score['conciseness_reason']}")
        print(f"   Average: {avg}/5")

        fps = score.get("false_positives", [])
        fns = score.get("false_negatives", [])

        if fps:
            print(f"   False positives — good content that was cut ({len(fps)}):")
            for fp in fps:
                snippet = fp["text"][:70].strip()
                print(f"     [{fp['segment_index']}] \"{snippet}\" → {fp['reason']}")

        if fns:
            print(f"   False negatives — flab that survived ({len(fns)}):")
            for fn in fns:
                snippet = fn["text"][:70].strip()
                print(f"     [{fn['segment_index']}] \"{snippet}\" → {fn['reason']}")

        notes = score.get("overall_notes", "").strip()
        if notes:
            print(f"   Notes: {notes}")

        results.append({
            "fixture":         name,
            "cut_pct":         cut,
            "coherence":       coh,
            "preservation":    pre,
            "conciseness":     con,
            "avg":             avg,
            "false_positives": fps,
            "false_negatives": fns,
            "overall_notes":   notes,
        })

    return results


# ── Summary + save ────────────────────────────────────────────────────────────

def print_summary(results: list[dict]) -> None:
    if not results:
        return
    print("\n" + "═" * 66)
    print("SUMMARY")
    print("═" * 66)
    print(f"{'Fixture':<32} {'Cut%':>5} {'Coh':>5} {'Pre':>5} {'Con':>5} {'Avg':>5}")
    print("─" * 66)
    for r in results:
        print(
            f"{r['fixture'][:31]:<32} {r['cut_pct']:>4}%"
            f" {r['coherence']:>5} {r['preservation']:>5} {r['conciseness']:>5} {r['avg']:>5}"
        )
    print("─" * 66)
    cohs = [r["coherence"]    for r in results]
    pres = [r["preservation"] for r in results]
    cons = [r["conciseness"]  for r in results]
    cuts = [r["cut_pct"]      for r in results]
    print(
        f"{'MEAN':<32} {round(sum(cuts)/len(cuts)):>4}%"
        f" {_avg(cohs):>5} {_avg(pres):>5} {_avg(cons):>5} {_avg(cohs+pres+cons):>5}"
    )


def save_results(results: list[dict], model: str) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    ts  = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out = RESULTS_DIR / f"{ts}.json"
    json.dump({"model": model, "results": results}, open(out, "w"), indent=2)
    print(f"\nSaved → {out}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import llm as _llm

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key and not _llm.is_local():
        print("GEMINI_API_KEY not set (or set LLM_BASE_URL for a local backend)", file=sys.stderr)
        sys.exit(1)

    args    = sys.argv[1:]
    model   = None  # use llm.py default for the active backend
    targets = []

    i = 0
    while i < len(args):
        if args[i] == "--model" and i + 1 < len(args):
            model = args[i + 1]
            i += 2
        else:
            targets.append(args[i])
            i += 1

    fixtures = load_fixtures(targets)

    if not fixtures:
        print(f"No fixtures found in {FIXTURES_DIR}/")
        print()
        print("Capture a fixture during a real pipeline run:")
        print("  python scripts/orchestrator.py video.mp4 --save-fixture")
        sys.exit(0)

    backend = os.environ.get("LLM_BASE_URL", "gemini")
    label   = model or os.environ.get("LLM_MODEL", backend)
    print(f"Evaluating {len(fixtures)} fixture(s) with {label}...")
    results = run_eval(fixtures, api_key, model)
    print_summary(results)
    save_results(results, label)


if __name__ == "__main__":
    main()
