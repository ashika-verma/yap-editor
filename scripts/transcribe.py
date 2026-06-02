#!/usr/bin/env python3
"""
Transcribe a video/audio file using MLX-Whisper and emit JSON to stdout.
Usage: python transcribe.py <file_path> [model_repo]
"""
import sys
import json
import os
import subprocess
import tempfile
import mlx_whisper

# Fix macOS SSL cert verification so torchaudio can download alignment model
import ssl
try:
    import certifi
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())
except ImportError:
    pass


def _extract_wav(video_path: str, wav_path: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-ac", "1", "-ar", "16000",
         "-sample_fmt", "s16", wav_path],
        capture_output=True, check=True,
    )


def _forced_align_segments(wav_path: str, segments: list[dict]) -> list[dict]:
    """
    Replace Whisper word timestamps with WhisperX forced-alignment timestamps.
    Uses wav2vec2-base-960h (English-specific, 360MB) via whisperx.alignment.
    Falls back to original Whisper timestamps if anything goes wrong.
    """
    import numpy as np
    import librosa
    from whisperx.alignment import load_align_model, align

    print("[transcribe] loading WhisperX alignment model...", file=sys.stderr)
    model_a, metadata = load_align_model(language_code="en", device="cpu")

    # Load audio as float32 numpy array at 16kHz (whisperx expects this format)
    audio, _ = librosa.load(wav_path, sr=16000, mono=True)

    # whisperx.align expects segments with "text", "start", "end" fields
    wx_segments = [
        {"text": seg["text"], "start": seg["start"], "end": seg["end"]}
        for seg in segments
    ]

    print("[transcribe] running forced alignment...", file=sys.stderr)
    result = align(
        wx_segments,
        model_a,
        metadata,
        audio,
        device="cpu",
        return_char_alignments=False,
    )

    # Merge aligned word timestamps back into original segments
    aligned = result.get("segments", [])
    out = []
    for orig_seg, aligned_seg in zip(segments, aligned):
        aligned_words = aligned_seg.get("words", [])
        if not aligned_words:
            out.append(orig_seg)
            continue

        # Map aligned words back to original words by position
        orig_words = orig_seg.get("words", [])
        new_words = []
        _MAX_SHIFT = 0.5  # discard alignment if it moves a boundary more than 500ms
        import math
        for i, orig_w in enumerate(orig_words):
            if i < len(aligned_words):
                aw = aligned_words[i]
                aw_start = aw.get("start", orig_w["start"])
                aw_end   = aw.get("end",   orig_w["end"])
                # Reject NaN/inf or unreasonable drift — keep Whisper's timestamp
                if (math.isfinite(aw_start) and math.isfinite(aw_end)
                        and aw_end > aw_start
                        and abs(aw_end - orig_w["end"]) <= _MAX_SHIFT
                        and abs(aw_start - orig_w["start"]) <= _MAX_SHIFT):
                    new_words.append({**orig_w, "start": round(aw_start, 3), "end": round(aw_end, 3)})
                else:
                    new_words.append(orig_w)
            else:
                new_words.append(orig_w)

        out.append({**orig_seg, "words": new_words})

    return out


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: transcribe.py <file_path> [model]"}))
        sys.exit(1)

    audio_path = sys.argv[1]
    model = sys.argv[2] if len(sys.argv) > 2 else "mlx-community/whisper-large-v3-turbo"

    old_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        result = mlx_whisper.transcribe(
            audio_path,
            path_or_hf_repo=model,
            word_timestamps=True,
            verbose=False,
            language="en",
            initial_prompt="Um, well, uh, like I said, he- he went to the store. A lot of- a lot of things happened.",
            condition_on_previous_text=True,
            compression_ratio_threshold=2.8,
            no_speech_threshold=0.4,
        )
    finally:
        sys.stdout = old_stdout

    segments = []
    for seg in result.get("segments", []):
        words = [
            {"word": w["word"].strip(), "start": round(w["start"], 3), "end": round(w["end"], 3)}
            for w in seg.get("words", [])
        ]
        text = seg["text"].strip()
        if not text:
            continue
        segments.append({
            "start": round(seg["start"], 3),
            "end":   round(seg["end"], 3),
            "text":  text,
            "words": words,
        })

    # Forced alignment: replace Whisper timestamps with phoneme-accurate ones
    # Redirect stdout → stderr during alignment so WhisperX's internal print()
    # calls ("Failed to align segment ...") don't pollute our JSON output.
    old_stdout = sys.stdout
    sys.stdout = sys.stderr
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        print("[transcribe] extracting audio for forced alignment...", file=sys.stderr)
        _extract_wav(audio_path, wav_path)
        pre_align = [
            (w["word"], w["start"], w["end"])
            for seg in segments for w in seg.get("words", [])
        ]
        segments = _forced_align_segments(wav_path, segments)
        post_align = [
            (w["word"], w["start"], w["end"])
            for seg in segments for w in seg.get("words", [])
        ]
        # Log words where alignment shifted the boundary by >20ms
        diffs = [
            f"  {pre[0]!r}: end {pre[2]:.3f}→{post[2]:.3f} ({(post[2]-pre[2])*1000:+.0f}ms)"
            for pre, post in zip(pre_align, post_align)
            if abs(post[2] - pre[2]) > 0.02
        ]
        if diffs:
            print(f"[transcribe] alignment shifted {len(diffs)} word boundaries >20ms:",
                  file=sys.stderr)
            for d in diffs[:20]:
                print(d, file=sys.stderr)
        else:
            print("[transcribe] alignment: no boundaries shifted >20ms", file=sys.stderr)
    except Exception as e:
        print(f"[transcribe] forced alignment skipped: {e}", file=sys.stderr)
    finally:
        sys.stdout = old_stdout
        try:
            os.unlink(wav_path)
        except OSError:
            pass

    print(json.dumps({
        "segments": segments,
        "language": result.get("language", "en"),
    }))


if __name__ == "__main__":
    main()
