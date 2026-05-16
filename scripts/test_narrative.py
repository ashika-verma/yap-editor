#!/usr/bin/env python3
"""
Standalone test for run_narrative_architect.
Pastes a transcript directly — no video needed.

Usage:
  cd /Users/ashikaverma/transcript-editor
  python scripts/test_narrative.py

Edit TRANSCRIPT below to match the last video you tested.
"""
import os, json, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Load .env.local ────────────────────────────────────────────────────────────
env_path = os.path.join(os.path.dirname(__file__), "..", ".env.local")
if os.path.exists(env_path):
    for line in open(env_path):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from orchestrator import run_narrative_architect, _DIRECTOR_DEFAULTS

# ── Paste the raw transcript here ──────────────────────────────────────────────
TRANSCRIPT = """
PASTE THE RAW TRANSCRIPT TEXT HERE.
Each sentence will be treated as one segment.
"""
# ─────────────────────────────────────────────────────────────────────────────

def make_segments(text: str) -> list[dict]:
    """Split transcript into fake 3-second Whisper segments."""
    import re
    # Split on sentence-ending punctuation followed by space
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    segs = []
    for i, s in enumerate(sentences):
        segs.append({
            "start": float(i * 3),
            "end":   float((i + 1) * 3),
            "text":  s,
            "words": [],
        })
    return segs


def main():
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set. Check .env.local", file=sys.stderr)
        sys.exit(1)

    segs = make_segments(TRANSCRIPT)
    print(f"Segmented into {len(segs)} fake segments", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    result = run_narrative_architect(
        api_key,
        {"segments": segs},
        {"buckets": []},
        _DIRECTOR_DEFAULTS,
    )

    print("\n" + "=" * 60)
    print("=== PASS 1: NARRATIVE ANALYSIS ===")
    na = result.get("narrativeAnalysis", {})
    print(f"\ncoreStory: {na.get('coreStory', '(none)')}")
    print(f"\nnarrativeArc: {na.get('narrativeArc', '(none)')}")

    tangents = na.get("tangents", [])
    print(f"\n── Tangents ({len(tangents)}) ──")
    for t in tangents:
        indices = t.get("segIndices", [])
        label   = t.get("label", "?")
        texts   = [segs[i]["text"][:60] if i < len(segs) else "OUT_OF_RANGE" for i in indices]
        print(f"  '{label}' → segs {indices}")
        for idx, txt in zip(indices, texts):
            print(f"    [{idx}] {txt}")

    reps = na.get("repetitionGroups", [])
    print(f"\n── Repetition Groups ({len(reps)}) ──")
    for r in reps:
        best = r.get("bestIndex", -1)
        dups = r.get("duplicateIndices", [])
        topic = r.get("topic", "?")
        print(f"  '{topic}' → keep [{best}], drop {dups}")
        for idx in [best] + dups:
            if 0 <= idx < len(segs):
                print(f"    [{idx}] {segs[idx]['text'][:60]}")

    circs = na.get("circularSections", [])
    print(f"\n── Circular Sections ({len(circs)}) ──")
    for c in circs:
        indices = c.get("segIndices", [])
        print(f"  '{c.get('label','?')}' → segs {indices}")

    print("\n" + "=" * 60)
    print("=== FINAL: DROPPED SEGMENTS ===")
    dropped = [s for s in result.get("segments", []) if not s["keep"]]
    kept    = [s for s in result.get("segments", []) if s["keep"]]
    print(f"Kept: {len(kept)}, Dropped: {len(dropped)}\n")
    for s in dropped:
        idx = s["index"]
        txt = segs[idx]["text"][:70] if idx < len(segs) else "OUT_OF_RANGE"
        print(f"  [{idx}] {s['dropReason']:12s}  {txt}")


if __name__ == "__main__":
    main()
