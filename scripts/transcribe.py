#!/usr/bin/env python3
"""
Transcribe a video/audio file using MLX-Whisper and emit JSON to stdout.
Usage: python transcribe.py <file_path> [model_repo]
"""
import sys
import json
import mlx_whisper

# Minimum consecutive repeats of a token cycle before we treat it as a Whisper
# hallucination loop (e.g. "of being of being of being…"). Genuine emphasis
# ("no no no") tops out around 3, so 4 is a safe floor.
LOOP_MIN_REPEATS = 4
LOOP_MAX_PERIOD  = 4  # longest repeating unit to detect (in words)


def _collapse_loops(words: list[dict]) -> tuple[list[dict], bool]:
    """Collapse degenerate repeated word-cycles (Whisper hallucination loops).

    Detects a unit of 1–LOOP_MAX_PERIOD words repeating ≥ LOOP_MIN_REPEATS times
    consecutively and keeps a single copy, dropping the looped tail. Returns
    (words, changed).
    """
    if not words:
        return words, False
    toks = [w["word"].strip().lower() for w in words]
    n = len(words)
    out: list[dict] = []
    i = 0
    changed = False
    while i < n:
        collapsed = False
        for p in range(1, LOOP_MAX_PERIOD + 1):
            if i + p * LOOP_MIN_REPEATS > n:
                continue
            unit = toks[i : i + p]
            reps, j = 1, i + p
            while j + p <= n and toks[j : j + p] == unit:
                reps += 1
                j += p
            if reps >= LOOP_MIN_REPEATS:
                out.extend(words[i : i + p])  # keep one copy of the unit
                i = j
                collapsed = True
                changed = True
                break
        if not collapsed:
            out.append(words[i])
            i += 1
    return out, changed


_SEG_DEDUP_REPEATS = 3   # ≥ this many consecutive identical segments → collapse to 1


def _norm_seg(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _dedup_segments(segments: list[dict]) -> list[dict]:
    """Collapse consecutive near-identical Whisper segments (cross-segment hallucination loops).

    Whisper sometimes emits the same short phrase (e.g. "Bye!") as 4-6 separate
    1-word segments at the end of a file. Keep the first copy, drop the rest
    when ≥ SEG_DEDUP_REPEATS in a row match.
    """
    if not segments:
        return segments
    out: list[dict] = []
    i = 0
    while i < len(segments):
        key = _norm_seg(segments[i]["text"])
        if not key:
            out.append(segments[i])
            i += 1
            continue
        # Count consecutive matches
        j = i + 1
        while j < len(segments) and _norm_seg(segments[j]["text"]) == key:
            j += 1
        reps = j - i
        if reps >= _SEG_DEDUP_REPEATS:
            # Keep first, skip the rest
            out.append(segments[i])
            print(f"[transcribe] collapsed {reps}× repeated segment: {segments[i]['text']!r}",
                  file=__import__('sys').stderr)
        else:
            out.extend(segments[i:j])
        i = j
    return out


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: transcribe.py <file_path> [model]"}))
        sys.exit(1)

    audio_path = sys.argv[1]
    model = sys.argv[2] if len(sys.argv) > 2 else "mlx-community/whisper-large-v3-turbo"

    # Redirect stdout to stderr during transcription so any mlx_whisper
    # diagnostic prints ("Detected language: ...") don't corrupt our JSON output.
    import io
    old_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        result = mlx_whisper.transcribe(
            audio_path,
            path_or_hf_repo=model,
            word_timestamps=True,
            verbose=False,
            language="en",
        )
    finally:
        sys.stdout = old_stdout

    segments = []
    for seg in result.get("segments", []):
        words = [
            {"word": w["word"].strip(), "start": round(w["start"], 3), "end": round(w["end"], 3)}
            for w in seg.get("words", [])
        ]
        words, looped = _collapse_loops(words)
        if looped and words:
            text = " ".join(w["word"] for w in words).strip()
            seg_end = words[-1]["end"]
        else:
            text = seg["text"].strip()
            seg_end = round(seg["end"], 3)
        segments.append({
            "start": round(seg["start"], 3),
            "end": seg_end,
            "text": text,
            "words": words,
        })

    segments = _dedup_segments(segments)

    print(json.dumps({
        "segments": segments,
        "language": result.get("language", "en"),
    }))

if __name__ == "__main__":
    main()
