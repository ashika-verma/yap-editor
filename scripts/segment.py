#!/usr/bin/env python3
"""
LLM-based semantic segmenter.

Takes Whisper segments (split at acoustic pauses) and merges consecutive
segments into semantic thought units — complete sentences or ideas.

Works in chunks of CHUNK_SIZE so it fits in Gemma's 8k context window.

Usage (standalone):
    python3 scripts/segment.py <fixture.json>
"""
from __future__ import annotations
import json, os, sys

CHUNK_SIZE = 40       # max segments per LLM call — Gemma truncates output past ~40 groups
SPLIT_THRESHOLD = 10.0  # segments longer than this get word-level sentence splitting
MIN_SPLIT_DUR   = 2.0   # minimum duration for a split sub-segment

SEGMENTER_SCHEMA = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "description": "Each inner array is a list of consecutive segment indices (within this chunk) to merge.",
            "items": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 1,
            },
        }
    },
    "required": ["groups"],
}


def _start(s: dict) -> float:
    """Return start timestamp regardless of whether segment uses start/startSec."""
    v = s.get("startSec")
    return float(v) if v is not None else float(s.get("start", 0.0))


def _end(s: dict) -> float:
    """Return end timestamp regardless of whether segment uses end/endSec."""
    v = s.get("endSec")
    return float(v) if v is not None else float(s.get("end", 0.0))


def _build_prompt(chunk: list[dict]) -> str:
    n = len(chunk)
    lines = "\n".join(
        f"[{i}] ({round(_end(s) - _start(s), 1)}s) {s.get('text', '').strip()}"
        for i, s in enumerate(chunk)
    )
    return f"""You are a transcript editor. The speech below was split into {n} segments at acoustic pauses by Whisper.
Many segments are fragments — mid-sentence breaks, filler words alone, dangling clauses like "Um, but..." or "And I—".

Task: group CONSECUTIVE segments into semantic units. Each group should be ONE complete sentence or short clause.

Rules:
- Every index [0]–[{n - 1}] must appear in exactly one group (no gaps, no skips, no reordering).
- Groups must be contiguous — [1, 2, 3] is valid; [1, 3] is not.
- Merge a filler/starter into the sentence it belongs to ("Um," + "the key point is X." → one group).
- Keep groups SHORT — target 1–3 segments. One clear sentence per group.
- Hard limit: total duration of a group must stay under 7 seconds. Sum the durations shown.
  If a single segment is already ≥7s, keep it as its own group — do not merge it with anything.
- Do NOT merge two complete sentences into one group just because they're related.
- Single-word fragments ("Um,", "But...") should attach to the NEXT sentence, not be left alone.

Segments (index, duration, text):
{lines}

Return JSON: {{"groups": [[0, 1], [2], [3, 4], ...]}}"""


MAX_GROUP_DURATION = 7.0  # seconds — groups longer than this are split back into singles


def _validate_groups(groups: list[list[int]], n: int) -> bool:
    """Groups must cover [0..n-1] exactly, in ascending order, no gaps."""
    flat = [idx for g in groups for idx in g]
    return flat == list(range(n))


def _enforce_duration(groups: list[list[int]], chunk: list[dict]) -> list[list[int]]:
    """Split any multi-segment group whose total duration exceeds MAX_GROUP_DURATION."""
    result = []
    for g in groups:
        if len(g) == 1:
            result.append(g)
            continue
        dur = sum(_end(chunk[i]) - _start(chunk[i]) for i in g)
        if dur > MAX_GROUP_DURATION:
            result.extend([[i] for i in g])
        else:
            result.append(g)
    return result


def _merge_group(chunk: list[dict], indices: list[int]) -> dict:
    segs = [chunk[i] for i in indices]
    all_words = [w for s in segs for w in s.get("words", [])]
    text = " ".join(s.get("text", "").strip() for s in segs)
    n = len(segs)
    t_start = _start(segs[0])
    t_end   = _end(segs[-1])
    return {
        # Support both raw Whisper format (start/end floats) and fixture format (startSec/endSec)
        "start":        t_start,
        "end":          t_end,
        "startSec":     t_start,
        "endSec":       t_end,
        "text":         text,
        "words":        all_words,
        "motionScore":  sum(s.get("motionScore", 0) or 0 for s in segs) / n,
        "audioRms":     sum(s.get("audioRms",    0) or 0 for s in segs) / n,
        "energyScore":  sum(s.get("energyScore", 0) or 0 for s in segs) / n,
        "visualTag":    segs[0].get("visualTag"),
        "keep":         True,
        "decisionSource": "pipeline",
        "dropReason":   "",
        "jobRisk":      "",
        "wordCuts":     [],
    }


def _segment_chunk(chunk: list[dict], api_key: str, model: str | None) -> list[dict]:
    import llm
    kwargs: dict = {"schema": SEGMENTER_SCHEMA, "api_key": api_key}
    if model:
        kwargs["model"] = model

    raw = llm.generate(_build_prompt(chunk), **kwargs)
    result = json.loads(raw)
    groups = result.get("groups", [])

    if not _validate_groups(groups, len(chunk)):
        print(f"[segment] invalid groups (got {groups!r:.120}) — keeping chunk as-is", file=sys.stderr)
        return chunk

    groups = _enforce_duration(groups, chunk)
    return [_merge_group(chunk, g) for g in groups]


ABSORB_MAX_DUR   = 2.0   # segments shorter than this are fragments to absorb
ABSORB_MAX_WORDS = 5     # and fewer than this many words
ABSORB_CAP       = 10.0  # don't absorb into a neighbor if combined result exceeds this

_SENTENCE_ENDERS = frozenset(".!?")


def _split_long_segments(segments: list[dict]) -> list[dict]:
    """Word-level sentence splitter for segments over SPLIT_THRESHOLD.

    Whisper sometimes produces single segments that span multiple sentences
    (e.g. a 26s opener). Merge-only can't fix these. This pass walks the
    word timestamps, finds sentence-ending punctuation, and splits there —
    as long as each resulting sub-segment is at least MIN_SPLIT_DUR seconds.
    """
    result: list[dict] = []
    split_count = 0
    for seg in segments:
        dur = _end(seg) - _start(seg)
        words = seg.get("words", [])
        if dur <= SPLIT_THRESHOLD or not words:
            result.append(seg)
            continue

        # Find split points: word indices where word ends with sentence punctuation
        # and the next word starts far enough in to make a valid sub-segment.
        splits: list[int] = []  # word indices AFTER which to split (exclusive end of sub-seg)
        for wi, w in enumerate(words[:-1]):  # never split after the last word
            if w.get("word", "").rstrip().endswith((".", "!", "?")):
                # Sub-segment from last split to here
                sub_start = _start(seg) if not splits else words[splits[-1]]["end"]
                sub_end = w["end"]
                sub_dur = sub_end - sub_start
                remaining_dur = _end(seg) - sub_end
                if sub_dur >= MIN_SPLIT_DUR and remaining_dur >= MIN_SPLIT_DUR:
                    splits.append(wi + 1)  # next sub-segment starts at wi+1

        if not splits:
            result.append(seg)
            continue

        # Build sub-segments from split points
        boundaries = [0] + splits + [len(words)]
        base = {k: v for k, v in seg.items() if k not in ("start", "end", "startSec", "endSec", "text", "words")}
        for b_start, b_end in zip(boundaries, boundaries[1:]):
            sub_words = words[b_start:b_end]
            if not sub_words:
                continue
            sub_text = " ".join(w.get("word", "") for w in sub_words).strip()
            t0 = sub_words[0].get("start", _start(seg))
            t1 = sub_words[-1].get("end", _end(seg))
            result.append({**base, "start": t0, "end": t1, "startSec": t0, "endSec": t1,
                            "text": sub_text, "words": sub_words})
        split_count += 1

    if split_count:
        print(f"[segment] word-split {split_count} long segment(s)", file=sys.stderr)
    return result


def _absorb_fragments(segments: list[dict]) -> list[dict]:
    """Deterministic pass: merge sub-2s filler fragments into an adjacent neighbor.

    The LLM can't merge a filler into a neighbor that is already ≥7s (the duration
    rule blocks it). This pass does it deterministically: prefer the next neighbor,
    fall back to the previous one.
    """
    if not segments:
        return segments

    absorbed = 0
    result = list(segments)
    changed = True
    while changed:
        changed = False
        i = 0
        new_result: list[dict] = []
        while i < len(result):
            seg = result[i]
            dur = _end(seg) - _start(seg)
            words = seg.get("text", "").split()
            is_fragment = dur < ABSORB_MAX_DUR and len(words) <= ABSORB_MAX_WORDS
            if is_fragment:
                # Try to absorb into next neighbor
                if i + 1 < len(result):
                    nxt = result[i + 1]
                    if _end(nxt) - _start(seg) <= ABSORB_CAP:
                        new_result.append(_merge_group([seg, nxt], [0, 1]))
                        absorbed += 1
                        i += 2
                        changed = True
                        continue
                # Fall back: absorb into previous
                if new_result:
                    prev = new_result.pop()
                    if _end(seg) - _start(prev) <= ABSORB_CAP:
                        new_result.append(_merge_group([prev, seg], [0, 1]))
                        absorbed += 1
                        i += 1
                        changed = True
                        continue
            new_result.append(seg)
            i += 1
        result = new_result

    if absorbed:
        print(f"[segment] absorbed {absorbed} fragment(s) into neighbors", file=sys.stderr)
    return result


def _stitch_boundaries(segments: list[dict]) -> list[dict]:
    """Merge adjacent segments where the first ends mid-sentence (no terminal punctuation).

    This fixes chunk-boundary artifacts: when a sentence spans the end of one chunk
    and the start of the next, the two LLM calls can't see each other and leave the
    sentence split. A trailing segment like "...so the first thing I wanted" has no
    terminal punctuation — it clearly continues into the next segment.
    """
    result: list[dict] = []
    i = 0
    stitched = 0
    while i < len(segments):
        seg = segments[i]
        text = seg.get("text", "").strip().rstrip('"\'')
        ends_incomplete = bool(text) and text[-1] not in _SENTENCE_ENDERS
        if ends_incomplete and i + 1 < len(segments):
            next_seg = segments[i + 1]
            combined_dur = _end(next_seg) - _start(seg)
            if combined_dur <= MAX_GROUP_DURATION * 2:  # more lenient at boundaries
                result.append(_merge_group([seg, next_seg], [0, 1]))
                stitched += 1
                i += 2
                continue
        result.append(seg)
        i += 1
    if stitched:
        print(f"[segment] stitched {stitched} cross-chunk boundary split(s)", file=sys.stderr)
    return result


def segment(
    segments: list[dict],
    api_key: str = "",
    model: str | None = None,
) -> list[dict]:
    """Re-segment Whisper segments into semantic thought units. Handles large videos by chunking."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    # Pre-pass: word-level sentence splitting for already-long single segments.
    # Runs before the LLM so the LLM sees sensibly-sized units to merge.
    segments = _split_long_segments(segments)

    result: list[dict] = []
    for start in range(0, len(segments), CHUNK_SIZE):
        chunk = segments[start : start + CHUNK_SIZE]
        chunk_label = f"{start}–{start + len(chunk) - 1}"
        print(f"[segment] chunk {chunk_label} ({len(chunk)} segs)…", file=sys.stderr)
        try:
            merged = _segment_chunk(chunk, api_key, model)
        except Exception as e:
            print(f"[segment] chunk {chunk_label} failed ({e}) — keeping as-is", file=sys.stderr)
            merged = chunk
        result.extend(merged)

    result = _absorb_fragments(result)
    result = _stitch_boundaries(result)
    print(f"[segment] {len(segments)} Whisper → {len(result)} semantic segments", file=sys.stderr)
    return result


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: segment.py <fixture.json>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        data = json.load(f)

    plan = data.get("plan", data)
    segs = plan.get("segments", [])
    api_key = os.environ.get("GEMINI_API_KEY", "")

    new_segs = segment(segs, api_key)
    # Print sample to stdout for inspection
    for i, s in enumerate(new_segs[:20]):
        dur = round(s["endSec"] - s["startSec"], 1)
        print(f"[{i}] ({dur}s) {s['text'].strip()[:100]}")


if __name__ == "__main__":
    main()
