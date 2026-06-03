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


def _detect_speech_regions(audio_path: str,
                           min_silence_sec: float = 0.3,
                           pad_sec: float = 0.1) -> list[float]:
    """
    Returns a flat [start, end, start, end, ...] list for mlx_whisper clip_timestamps.
    Splits audio on silence gaps >= min_silence_sec so Whisper never sees silence,
    which prevents hallucinations and gives cleaner word timestamps.
    """
    import librosa
    import numpy as np

    # librosa can't decode video containers — extract to WAV first
    wav_for_vad = None
    load_path = audio_path
    ext = os.path.splitext(audio_path)[1].lower()
    if ext not in (".wav", ".flac", ".mp3", ".ogg", ".m4a", ".aac"):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_for_vad = tmp.name
        subprocess.run(
            ["ffmpeg", "-y", "-i", audio_path, "-ac", "1", "-ar", "16000",
             "-sample_fmt", "s16", wav_for_vad],
            capture_output=True, check=True,
        )
        load_path = wav_for_vad

    try:
        y, sr = librosa.load(load_path, sr=16000, mono=True)
    finally:
        if wav_for_vad:
            try: os.unlink(wav_for_vad)
            except OSError: pass
    duration = float(len(y) / sr)

    frame_len = int(0.025 * sr)   # 25ms frames
    hop       = int(0.010 * sr)   # 10ms hop
    rms = librosa.feature.rms(y=y, frame_length=frame_len, hop_length=hop)[0]
    rms_db = 20 * np.log10(rms + 1e-10)

    # Adaptive threshold: noise floor (5th percentile) + 20 dB headroom.
    # Clamp noise floor to -70 dBFS minimum — digitally silent sections drag
    # the percentile to -120 dB, making the threshold so low that quiet room
    # noise gets flagged as speech.
    noise_floor = max(float(np.percentile(rms_db, 5)), -70.0)
    threshold   = noise_floor + 20.0

    frame_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    is_speech   = rms_db > threshold

    # Find silence runs that are long enough to split on
    silence_gaps: list[tuple[float, float]] = []
    in_silence   = False
    sil_start    = 0.0
    for t, speech in zip(frame_times, is_speech):
        if not speech and not in_silence:
            in_silence = True
            sil_start  = float(t)
        elif speech and in_silence:
            in_silence = False
            if float(t) - sil_start >= min_silence_sec:
                silence_gaps.append((sil_start, float(t)))
    if in_silence and (duration - sil_start) >= min_silence_sec:
        silence_gaps.append((sil_start, duration))

    if not silence_gaps:
        print("[transcribe] VAD: no silence gaps found, processing full audio", file=sys.stderr)
        return [0.0, duration]

    # Speech regions = inverse of silence gaps, padded outward
    regions: list[tuple[float, float]] = []
    prev_end = 0.0
    for sil_start, sil_end in silence_gaps:
        if sil_start > prev_end:
            regions.append((max(0.0, prev_end - pad_sec), min(duration, sil_start + pad_sec)))
        prev_end = sil_end
    if prev_end < duration:
        regions.append((max(0.0, prev_end - pad_sec), duration))

    # Merge overlapping padded regions
    merged: list[list[float]] = [list(regions[0])]
    for s, e in regions[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    print(f"[transcribe] VAD: {len(merged)} speech regions "
          f"({len(silence_gaps)} silence gaps ≥{min_silence_sec}s skipped)",
          file=sys.stderr)
    for s, e in merged:
        print(f"  speech {s:.2f}s – {e:.2f}s", file=sys.stderr)

    flat: list[float] = []
    for s, e in merged:
        flat.extend([round(s, 3), round(e, 3)])
    return flat


def _filter_hallucinated_words(segments: list[dict], audio, sr: int) -> list[dict]:
    """
    Drop words whose audio window has near-zero energy — Whisper hallucinations.
    Also drops segments that end up with no words after filtering.
    """
    import numpy as np

    rms_all = np.sqrt(np.mean(audio ** 2))
    noise_floor_db = max(20 * np.log10(rms_all + 1e-10) - 30, -70.0)
    threshold_db = noise_floor_db + 15.0  # 15 dB above noise floor = real speech

    kept_segments = []
    dropped_words = 0
    for seg in segments:
        words = seg.get("words", [])
        clean_words = []
        for w in words:
            s = int(w.get("start", 0) * sr)
            e = int(w.get("end", 0) * sr)
            chunk = audio[s:e] if e > s else np.array([])
            if len(chunk) == 0:
                continue
            rms_db = 20 * np.log10(np.sqrt(np.mean(chunk ** 2)) + 1e-10)
            if rms_db >= threshold_db:
                clean_words.append(w)
            else:
                dropped_words += 1
        if not clean_words:
            continue  # entire segment was hallucinated
        seg = {**seg, "words": clean_words}
        # Realign segment start/end to surviving word boundaries
        seg["start"] = clean_words[0]["start"]
        seg["end"]   = clean_words[-1]["end"]
        seg["text"]  = " ".join(w["word"].strip() for w in clean_words)
        kept_segments.append(seg)

    if dropped_words:
        print(f"[transcribe] energy filter: dropped {dropped_words} hallucinated words, "
              f"{len(segments) - len(kept_segments)} empty segments removed",
              file=sys.stderr)
    return kept_segments


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

    import librosa
    import numpy as np

    # Extract to 16kHz WAV once — reused for VAD, per-chunk transcription,
    # energy filter, and forced alignment.
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        _extract_wav(audio_path, wav_path)
        audio, sr = librosa.load(wav_path, sr=16000, mono=True)
    except Exception as e:
        print(json.dumps({"error": f"Audio extraction failed: {e}"}))
        sys.exit(1)

    # Detect speech regions — split audio at silence gaps so each chunk is
    # pure speech with no silent padding.
    speech_regions = _detect_speech_regions(wav_path)
    pairs = [(speech_regions[i], speech_regions[i + 1])
             for i in range(0, len(speech_regions), 2)]

    # Transcribe each speech chunk independently so every chunk gets fresh
    # initial_prompt filler-word priming with condition_on_previous_text=False.
    segments: list[dict] = []
    detected_language = "en"

    print(f"[transcribe] transcribing {len(pairs)} speech chunks...", file=sys.stderr)
    old_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        for start_t, end_t in pairs:
            chunk = audio[int(start_t * sr):int(end_t * sr)]
            if len(chunk) < int(0.1 * sr):   # skip <100ms slivers
                continue

            result = mlx_whisper.transcribe(
                chunk,
                path_or_hf_repo=model,
                word_timestamps=True,
                verbose=False,
                language="en",
                initial_prompt="Um, well, uh, like I said, he- he went to the store. A lot of- a lot of things happened.",
                condition_on_previous_text=False,
                compression_ratio_threshold=2.8,
                no_speech_threshold=0.4,
                hallucination_silence_threshold=2.0,
            )
            detected_language = result.get("language", detected_language)

            for seg in result.get("segments", []):
                text = seg["text"].strip()
                if not text:
                    continue
                words = [
                    {
                        "word":  w["word"].strip(),
                        "start": round(w["start"] + start_t, 3),
                        "end":   round(w["end"]   + start_t, 3),
                    }
                    for w in seg.get("words", [])
                ]
                segments.append({
                    "start": round(seg["start"] + start_t, 3),
                    "end":   round(seg["end"]   + start_t, 3),
                    "text":  text,
                    "words": words,
                })
    finally:
        sys.stdout = old_stdout

    # Energy filter: drop words whose audio window is near-silent (hallucinations).
    segments = _filter_hallucinated_words(segments, audio, sr)

    # Forced alignment: replace Whisper timestamps with phoneme-accurate ones.
    old_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        pre_align = [
            (w["word"], w["start"], w["end"])
            for seg in segments for w in seg.get("words", [])
        ]
        segments = _forced_align_segments(wav_path, segments)
        post_align = [
            (w["word"], w["start"], w["end"])
            for seg in segments for w in seg.get("words", [])
        ]
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
        "language": detected_language,
    }))


if __name__ == "__main__":
    main()
