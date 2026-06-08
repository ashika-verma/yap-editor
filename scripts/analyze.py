#!/usr/bin/env python3
"""
Multimodal signal extraction for the Vlog Compiler pipeline.

Computes per-second buckets with:
  - motion_score: OpenCV frame-differencing MSE, normalized to [0,1]
  - audio_rms:    Librosa RMS energy, normalized to [0,1]
  - pitch_delta:  Librosa YIN F0 inter-bucket change, normalized to [0,1]
  - pitch_var:    Intra-bucket F0 variance, normalized to [0,1]
  - silence:      bool — bucket is predominately silent (< SILENCE_DB)
  - visual_tag:   Gemma 4 caption at every SAMPLE_INTERVAL seconds (null elsewhere)

Top-level output also includes:
  - silence_regions: [{start, end}] merged runs of silent buckets

Usage:
  python analyze.py <video_path>
Output:
  JSON to stdout — {"buckets": [...], "duration", "silence_regions": [...]}
"""

from __future__ import annotations
import sys, json, os, tempfile, subprocess
from typing import Optional

# Fix SSL cert path on macOS / Python 3.14 before any HTTPS calls
try:
    import certifi as _certifi
    os.environ.setdefault("SSL_CERT_FILE", _certifi.where())
except ImportError:
    pass

import cv2
import librosa
import numpy as np

BUCKET_SIZE = 1.0       # seconds per bucket
SAMPLE_INTERVAL = 5.0   # Gemma samples every N seconds
SILENCE_DB = -45.0      # dBFS below which a bucket is considered silent


# ── Audio extraction ────────────────────────────────────────────────────────

def _probe_duration(video_path: str) -> float:
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", video_path],
        capture_output=True, text=True,
    )
    try:
        return float(probe.stdout.strip())
    except (ValueError, AttributeError):
        return 60.0


def extract_audio(video_path: str) -> tuple[np.ndarray, int]:
    SR = 16000
    tmp = tempfile.mktemp(suffix=".wav")
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-ac", "1", "-ar", str(SR), tmp],
            capture_output=True,
        )
        if result.returncode != 0:
            print(f"[analyze] audio extraction failed (exit {result.returncode}) — no audio stream, using silence", file=sys.stderr)
            dur = _probe_duration(video_path)
            return np.zeros(int(dur * SR), dtype=np.float32), SR
        y, sr = librosa.load(tmp, sr=SR, mono=True)
        return y, int(sr)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def compute_audio_buckets(
    y: np.ndarray, sr: int, n: int
) -> tuple[list[float], list[float], list[float], list[bool]]:
    """Returns (rms_list, pitch_list, pitch_var_list, silence_list)."""
    rms_list, pitch_list, pitch_var_list, silence_list = [], [], [], []
    for i in range(n):
        start = int(i * BUCKET_SIZE * sr)
        end   = min(int((i + 1) * BUCKET_SIZE * sr), len(y))
        seg   = y[start:end]

        # RMS energy
        rms = float(np.sqrt(np.mean(seg**2))) if len(seg) > 0 else 0.0
        rms_list.append(rms)

        # Silence detection
        rms_db = 20 * np.log10(rms + 1e-10)
        silence_list.append(rms_db < SILENCE_DB)

        # Pitch (mean + variance of voiced frames)
        if len(seg) >= 2048:
            try:
                f0     = librosa.yin(seg, fmin=80.0, fmax=400.0, sr=sr)
                voiced = f0[f0 > 0]
                pitch_list.append(float(np.mean(voiced)) if len(voiced) > 0 else 0.0)
                pitch_var_list.append(float(np.std(voiced)) if len(voiced) > 1 else 0.0)
            except Exception:
                pitch_list.append(0.0)
                pitch_var_list.append(0.0)
        else:
            pitch_list.append(0.0)
            pitch_var_list.append(0.0)

    return rms_list, pitch_list, pitch_var_list, silence_list


# ── Motion analysis ─────────────────────────────────────────────────────────

def compute_motion_buckets(video_path: str, n: int) -> list[float]:
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    sums, counts = [0.0] * n, [0] * n
    prev_gray = None
    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        bi = int((frame_idx / fps) / BUCKET_SIZE)
        if bi >= n:
            break
        gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (320, 180))
        if prev_gray is not None:
            diff = cv2.absdiff(gray, prev_gray).astype(np.float32)
            sums[bi] += float(np.mean(diff**2))
            counts[bi] += 1
        prev_gray = gray
        frame_idx += 1
    cap.release()
    return [sums[i] / counts[i] if counts[i] > 0 else 0.0 for i in range(n)]


# ── Gemma 4 visual tagging via MLX-VLM ──────────────────────────────────────

GEMMA_MODEL = "mlx-community/gemma-4-e4b-it-4bit"
VISUAL_PROMPT = "Describe what the person is doing and their energy level in one short sentence."


def try_load_gemma() -> Optional[tuple]:
    """Load Gemma 4 E4B via mlx-vlm. Returns (model, processor, config) or None."""
    try:
        from mlx_vlm import load  # type: ignore
        from mlx_vlm.utils import load_config  # type: ignore

        model, processor = load(GEMMA_MODEL)
        config = load_config(GEMMA_MODEL)
        return model, processor, config
    except Exception as e:
        print(f"Gemma 4 VLM load failed: {e}", file=sys.stderr)
        return None


def extract_frame_at(video_path: str, t: float):
    """Extract a single frame as a PIL Image at time t (seconds)."""
    from PIL import Image  # type: ignore

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return None
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def compute_visual_tags(
    video_path: str, duration: float, n_buckets: int, vlm_tuple: tuple
) -> list[Optional[str]]:
    """
    Run Gemma 4 on one frame every SAMPLE_INTERVAL seconds.
    Returns a list of length n_buckets where non-sampled buckets are None.
    """
    from mlx_vlm import generate  # type: ignore
    from mlx_vlm.prompt_utils import apply_chat_template  # type: ignore

    model, processor, config = vlm_tuple
    tags: list[Optional[str]] = [None] * n_buckets
    sample_times = np.arange(SAMPLE_INTERVAL / 2, duration, SAMPLE_INTERVAL)

    for t in sample_times:
        bi = int(t / BUCKET_SIZE)
        if bi >= n_buckets:
            break
        frame = extract_frame_at(video_path, float(t))
        if frame is None:
            continue
        try:
            prompt = apply_chat_template(processor, config, VISUAL_PROMPT, num_images=1)
            result = generate(model, processor, prompt, [frame], max_tokens=60, verbose=False)
            tags[bi] = result.text.strip() or None
        except Exception as e:
            print(f"Gemma 4 query at t={t:.1f}s failed: {e}", file=sys.stderr)

    return tags


# ── Normalisation ────────────────────────────────────────────────────────────

def normalize(arr: list[float]) -> list[float]:
    a = np.array(arr, dtype=float)
    m = float(a.max())
    return (a / m).tolist() if m > 0 else a.tolist()


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        sys.stdout.write(json.dumps({"error": "Usage: analyze.py <video_path>"}) + "\n")
        sys.exit(1)

    video_path = sys.argv[1]

    # Keep stdout clean for JSON output — redirect during noisy library calls
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        y, sr = extract_audio(video_path)
        duration = float(len(y) / sr)
        n_buckets = max(1, int(np.ceil(duration / BUCKET_SIZE)))

        rms_raw, pitch_raw, pitch_var_raw, silence_raw = compute_audio_buckets(y, sr, n_buckets)
        motion_raw = compute_motion_buckets(video_path, n_buckets)

        pitch_arr       = np.array(pitch_raw, dtype=float)
        pitch_delta_raw = np.abs(np.diff(pitch_arr, prepend=pitch_arr[:1])).tolist()

        motion_norm      = normalize(motion_raw)
        rms_norm         = normalize(rms_raw)
        pitch_delta_norm = normalize(pitch_delta_raw)
        pitch_var_norm   = normalize(pitch_var_raw)

        # Gemma 4 visual tagging (optional — skips gracefully if mlx-vlm unavailable
        # or --no-vision flag is passed)
        visual_tags: list[Optional[str]]
        if "--no-vision" in sys.argv:
            print("Gemma 4 VLM disabled by --no-vision flag", file=sys.stderr)
            visual_tags = [None] * n_buckets
        else:
            vlm_tuple = try_load_gemma()
            if vlm_tuple is not None:
                print(f"Gemma 4 loaded — tagging {int(np.ceil(duration / SAMPLE_INTERVAL))} frames...", file=sys.stderr)
                visual_tags = compute_visual_tags(video_path, duration, n_buckets, vlm_tuple)
            else:
                print("Gemma 4 VLM not available — skipping visual tags", file=sys.stderr)
                visual_tags = [None] * n_buckets

        buckets = [
            {
                "t":            round(i * BUCKET_SIZE, 2),
                "motion_score": round(motion_norm[i], 4),
                "audio_rms":    round(rms_norm[i], 4),
                "pitch_delta":  round(pitch_delta_norm[i], 4),
                "pitch_var":    round(pitch_var_norm[i], 4),
                "silence":      bool(silence_raw[i]),
                "visual_tag":   visual_tags[i],
            }
            for i in range(n_buckets)
        ]

        # Merge consecutive silent buckets into regions
        silence_regions: list[dict] = []
        in_sil = False
        sil_start = 0.0
        for b in buckets:
            if b["silence"] and not in_sil:
                in_sil = True
                sil_start = b["t"]
            elif not b["silence"] and in_sil:
                in_sil = False
                silence_regions.append({"start": sil_start, "end": b["t"]})
        if in_sil:
            silence_regions.append({"start": sil_start, "end": round(duration, 2)})

    finally:
        sys.stdout = real_stdout

    sys.stdout.write(
        json.dumps({
            "buckets": buckets,
            "duration": round(duration, 2),
            "silence_regions": silence_regions,
        }) + "\n"
    )


if __name__ == "__main__":
    main()
