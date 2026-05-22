#!/usr/bin/env python3
"""
Segmentation eval: Whisper segments vs. LLM semantic segments.

For each unique video source in fixtures/, picks the latest fixture and:
  1. Reports structural stats (count, avg duration, fragment rates)
  2. Runs the LLM segmenter to produce semantic segments
  3. Scores both with an LLM judge ("is each segment a complete thought?")

Usage:
    python3 scripts/eval_segments.py                        # all sources
    python3 scripts/eval_segments.py fixtures/foo.json      # specific fixture
"""
from __future__ import annotations
import collections, glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from segment import segment as llm_segment

JUDGE_SAMPLE = 25  # max segments shown to judge per segmentation

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": ["index", "score"],
            },
        },
        "overall": {"type": "integer", "minimum": 0, "maximum": 100},
        "notes":   {"type": "string"},
    },
    "required": ["scores", "overall", "notes"],
}


# ── Stats ──────────────────────────────────────────────────────────────────────

def _stats(segments: list[dict]) -> dict:
    durs = [s.get("endSec", 0) - s.get("startSec", 0) for s in segments]
    n = max(len(durs), 1)
    return {
        "count":        len(segments),
        "avg_dur":      round(sum(durs) / n, 2),
        "pct_under_1s": round(sum(1 for d in durs if d < 1.0) / n * 100, 1),
        "pct_under_2s": round(sum(1 for d in durs if d < 2.0) / n * 100, 1),
        "pct_over_15s": round(sum(1 for d in durs if d > 15.0) / n * 100, 1),
    }


def _fmt_stats(label: str, s: dict) -> str:
    return (
        f"{label:<10} {s['count']:>4} segs | avg {s['avg_dur']:>5}s | "
        f"<1s {s['pct_under_1s']:>5}% | <2s {s['pct_under_2s']:>5}% | >15s {s['pct_over_15s']:>4}%"
    )


# ── Judge ──────────────────────────────────────────────────────────────────────

def _judge(segments: list[dict], api_key: str) -> dict:
    import llm

    sample = segments[:JUDGE_SAMPLE]
    lines = "\n".join(
        f"[{i}] ({round(s.get('endSec', 0) - s.get('startSec', 0), 1)}s) {s.get('text', '').strip()}"
        for i, s in enumerate(sample)
    )
    prompt = f"""You are evaluating transcript segmentation quality for a casual vlog editor.
Each segment is a unit the editor will keep or cut as a whole — so each segment should be a complete thought.

Score each segment 0–100:
  90–100  Complete, standalone thought. Starts and ends at a natural sentence/idea boundary.
  60–89   Mostly complete but slightly clipped or slightly run-on.
  30–59   Partial — belongs merged with adjacent segment(s).
  0–29    Fragment (filler word alone, mid-clause break, dangling "Um, but...").

Then give an OVERALL score 0–100 for the full segmentation:
how well do these segments represent complete thought units a human editor could make keep/cut decisions on?

Segments (index, duration, text) — first {len(sample)} of {len(segments)}:
{lines}

Return JSON with "scores" (array of {{index, score}}), "overall" (0–100), "notes" (1–2 sentences)."""

    raw = llm.generate(prompt, schema=JUDGE_SCHEMA, api_key=api_key)
    result = json.loads(raw)
    scored = [s.get("score", 0) for s in result.get("scores", [])]
    result["avg_per_seg"] = round(sum(scored) / max(len(scored), 1), 1)
    return result


# ── Per-fixture eval ───────────────────────────────────────────────────────────

def eval_fixture(path: str, api_key: str) -> None:
    with open(path) as f:
        data = json.load(f)
    plan = data.get("plan", data)
    whisper_segs = plan.get("segments", [])
    src = os.path.basename(path).split("_")[0]

    print(f"\n{'═' * 65}")
    print(f"  {src}  ·  {os.path.basename(path)}")
    print(f"{'═' * 65}")

    w_stats = _stats(whisper_segs)
    print(f"\n{_fmt_stats('Whisper', w_stats)}")

    # Run segmenter
    print(f"  → running LLM segmenter…", file=sys.stderr)
    llm_segs = llm_segment(whisper_segs, api_key)

    l_stats = _stats(llm_segs)
    print(f"{_fmt_stats('LLM', l_stats)}")

    delta_count = l_stats["count"] - w_stats["count"]
    delta_dur   = round(l_stats["avg_dur"] - w_stats["avg_dur"], 2)
    print(f"  Δ  {delta_count:+d} segments | avg dur {delta_dur:+.2f}s")

    # Judge Whisper
    print(f"  → judging Whisper…", file=sys.stderr)
    w_judge = _judge(whisper_segs, api_key)
    print(f"\nWhisper quality   overall={w_judge['overall']:>3}/100  avg/seg={w_judge['avg_per_seg']:>5}/100")
    print(f"  {w_judge.get('notes', '')}")

    # Judge LLM
    print(f"  → judging LLM…", file=sys.stderr)
    l_judge = _judge(llm_segs, api_key)
    print(f"LLM quality       overall={l_judge['overall']:>3}/100  avg/seg={l_judge['avg_per_seg']:>5}/100")
    print(f"  {l_judge.get('notes', '')}")

    # Sample
    print(f"\nSample LLM segments (first 12):")
    for i, s in enumerate(llm_segs[:12]):
        dur  = round(s.get("endSec", 0) - s.get("startSec", 0), 1)
        text = s.get("text", "").strip()
        # Show if this is a merge (text has multiple Whisper texts concatenated)
        print(f"  [{i:>2}] ({dur:>5}s) {text[:95]}")


# ── Main ───────────────────────────────────────────────────────────────────────

def _latest_per_source() -> list[str]:
    fixture_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures")
    by_src: dict[str, list[str]] = collections.defaultdict(list)
    for f in glob.glob(os.path.join(fixture_dir, "*.json")):
        src = os.path.basename(f).split("_")[0]
        by_src[src].append(f)
    return [sorted(paths)[-1] for paths in by_src.values()]


def main() -> None:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    paths = sys.argv[1:] if len(sys.argv) > 1 else _latest_per_source()

    if not paths:
        print("No fixtures found. Run orchestrator.py with --save-fixture first.")
        sys.exit(1)

    print(f"Evaluating {len(paths)} source(s)…")
    for path in paths:
        try:
            eval_fixture(path, api_key)
        except Exception as e:
            import traceback
            print(f"\n[eval_segments] {os.path.basename(path)}: {e}")
            traceback.print_exc()

    print(f"\n{'═' * 65}")
    print("Done.")


if __name__ == "__main__":
    main()
