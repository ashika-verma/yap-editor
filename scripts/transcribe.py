#!/usr/bin/env python3
"""
Transcribe a video/audio file using MLX-Whisper and emit JSON to stdout.
Usage: python transcribe.py <file_path> [model_repo]
"""
import sys
import json
import mlx_whisper

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
        )
    finally:
        sys.stdout = old_stdout

    segments = []
    for seg in result.get("segments", []):
        words = [
            {"word": w["word"].strip(), "start": round(w["start"], 3), "end": round(w["end"], 3)}
            for w in seg.get("words", [])
        ]
        segments.append({
            "start": round(seg["start"], 3),
            "end": round(seg["end"], 3),
            "text": seg["text"].strip(),
            "words": words,
        })

    print(json.dumps({
        "segments": segments,
        "language": result.get("language", "en"),
    }))

if __name__ == "__main__":
    main()
