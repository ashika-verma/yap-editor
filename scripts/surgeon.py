#!/usr/bin/env python3
"""
Heuristic Surgeon: refines word-level cuts with acoustic boundary detection
and dead-air (silence) detection.

Whisper timestamps are approximate (±50–200 ms). This module uses audio
analysis to find the true acoustic boundaries around each word cut:

  Cut start → energy envelope search: last frame above speech threshold
              before the Whisper timestamp = actual end of preceding word.
  Cut end   → onset detection with backtrack: first transient after the
              Whisper timestamp = actual start of following word.
              (Same technique DAWs use for transient snapping.)

Zero-crossing snap is applied as a final step on both boundaries.

Imported by orchestrator.py; not meant to be run standalone.
"""
from __future__ import annotations
import os, tempfile, subprocess

import librosa
import numpy as np

BUDGET_SEC   = 0.20    # search window around each Whisper timestamp (seconds)
MIN_SPAN     = 0.15    # minimum keep-span after cuts (matches export route)
SILENCE_DB   = -45.0   # dBFS below which a frame is considered silent
MIN_SILENCE  = 0.30    # ignore silence runs shorter than this (seconds)
SPEECH_DB    = -38.0   # dBFS above which a frame is considered speech
FRAME_SEC    = 0.010   # RMS frame size for energy analysis (10 ms)


# ── Audio helpers ────────────────────────────────────────────────────────────

def load_audio_from_video(video_path: str, sr: int = 16000) -> tuple[np.ndarray, int]:
    tmp = tempfile.mktemp(suffix=".wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-ac", "1", "-ar", str(sr), tmp],
            check=True, capture_output=True,
        )
        y, _ = librosa.load(tmp, sr=sr, mono=True)
        return y, sr
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _snap_to_zero_crossing(audio: np.ndarray, sr: int, t: float, budget: float) -> float:
    """Return the nearest zero-crossing to t within ±budget seconds, or t if none."""
    sample = int(t * sr)
    budget_samples = int(budget * sr)
    start  = max(0, sample - budget_samples)
    end    = min(len(audio), sample + budget_samples)
    if start >= end:
        return t

    segment = audio[start:end]
    zc_mask = librosa.zero_crossings(segment, pad=False)
    zc_idx  = np.where(zc_mask)[0] + start
    if len(zc_idx) == 0:
        return t

    nearest = int(zc_idx[np.argmin(np.abs(zc_idx - sample))])
    snapped = nearest / sr
    return snapped if abs(snapped - t) <= budget else t


def _rms_db(audio: np.ndarray, sr: int, t0: float, t1: float) -> np.ndarray:
    """Return per-frame dBFS RMS for audio[t0:t1]."""
    s0 = max(0, int(t0 * sr))
    s1 = min(len(audio), int(t1 * sr))
    chunk = audio[s0:s1]
    if len(chunk) == 0:
        return np.array([])
    frame_len = max(int(FRAME_SEC * sr), 1)
    rms = librosa.feature.rms(y=chunk, frame_length=frame_len, hop_length=frame_len)[0]
    return librosa.power_to_db(rms ** 2 + 1e-10)


def _find_speech_end(audio: np.ndarray, sr: int, t_hint: float, budget: float) -> float:
    """
    Find where speech actually ends near t_hint.

    Searches [t_hint - budget, t_hint + budget/4] for the last frame whose
    energy is above SPEECH_DB. This is the acoustic end of the preceding word,
    which is where the cut should start — not Whisper's token boundary.
    """
    t0 = max(0.0, t_hint - budget)
    t1 = min(len(audio) / sr, t_hint + budget / 4)
    db = _rms_db(audio, sr, t0, t1)
    if db.size == 0:
        return t_hint

    frame_len = max(int(FRAME_SEC * sr), 1)
    speech_frames = np.where(db > SPEECH_DB)[0]
    if speech_frames.size == 0:
        return t_hint  # window is all silent — use Whisper hint

    last_frame = int(speech_frames[-1])
    t = t0 + last_frame * frame_len / sr
    # Clamp to within one budget of the hint so we don't drift too far
    t = min(t, t_hint + budget / 4)
    return _snap_to_zero_crossing(audio, sr, t, 0.015)


def _find_speech_onset(audio: np.ndarray, sr: int, t_hint: float, budget: float) -> float:
    """
    Find where speech resumes near t_hint using onset detection.

    Searches [t_hint - budget/4, t_hint + budget] with backtrack=True so the
    returned time snaps to the attack start, not the peak — matching how DAWs
    place transient markers.
    """
    t0 = max(0.0, t_hint - budget / 4)
    t1 = min(len(audio) / sr, t_hint + budget)
    s0 = int(t0 * sr)
    s1 = int(t1 * sr)
    if s1 - s0 < int(0.02 * sr):
        return t_hint

    chunk = audio[s0:s1]
    try:
        onsets = librosa.onset.onset_detect(
            y=chunk, sr=sr, units="time",
            backtrack=True,   # snap to attack start, not spectral peak
            delta=0.07,       # higher = fewer false positives on breath/noise
            pre_max=3, post_max=3, pre_avg=5, post_avg=5, wait=2,
        )
    except Exception:
        return t_hint

    for onset_t in onsets:
        abs_t = t0 + float(onset_t)
        if abs_t >= t_hint - 0.03:  # small look-back tolerance
            return _snap_to_zero_crossing(audio, sr, abs_t, 0.015)

    return t_hint


# ── Silence detection ────────────────────────────────────────────────────────

def _find_silence_cuts(
    audio: np.ndarray, sr: int, seg_start: float, seg_end: float
) -> list[dict]:
    """Return silence regions ≥ MIN_SILENCE seconds within [seg_start, seg_end]."""
    s0    = max(0, int(seg_start * sr))
    s1    = min(len(audio), int(seg_end * sr))
    chunk = audio[s0:s1]
    if len(chunk) == 0:
        return []

    frame_len = max(int(0.010 * sr), 1)
    hop_len   = frame_len
    rms       = librosa.feature.rms(y=chunk, frame_length=frame_len, hop_length=hop_len)[0]
    rms_db    = librosa.power_to_db(rms ** 2 + 1e-10)

    silent_frames = rms_db < SILENCE_DB
    cuts: list[dict] = []
    in_silence = False
    sil_start  = 0

    for i, is_sil in enumerate(silent_frames):
        if is_sil and not in_silence:
            in_silence = True
            sil_start  = i
        elif not is_sil and in_silence:
            in_silence = False
            dur = (i - sil_start) * hop_len / sr
            if dur >= MIN_SILENCE:
                t0 = seg_start + sil_start * hop_len / sr
                t1 = seg_start + i * hop_len / sr
                cuts.append({"startSec": round(t0, 4), "endSec": round(t1, 4), "word": "<silence>"})

    # Handle silence running to end of segment
    if in_silence:
        dur = (len(silent_frames) - sil_start) * hop_len / sr
        if dur >= MIN_SILENCE:
            t0 = seg_start + sil_start * hop_len / sr
            t1 = seg_end
            cuts.append({"startSec": round(t0, 4), "endSec": round(t1, 4), "word": "<silence>"})

    return cuts


# ── Main entry point ─────────────────────────────────────────────────────────

def refine_word_cuts(
    audio: np.ndarray,
    sr: int,
    segments: list[dict],
    budget_sec: float = BUDGET_SEC,
    add_silence_cuts: bool = True,
    aggressiveness: float = 0.7,
) -> list[dict]:
    """
    Refine word-level cut boundaries using acoustic analysis.

    For each word cut:
      - Cut start: energy-envelope search finds the actual end of the preceding
        word, which is often earlier than Whisper's token boundary.
      - Cut end: onset detection (backtrack=True) finds the actual attack start
        of the following word, which is often later than Whisper's token boundary.
      - Both are then zero-crossing snapped to avoid DC clicks.

    Manual cuts bypass acoustic refinement and stay exactly where placed.

    aggressiveness: 0–1, scales MIN_SILENCE for silence detection.
    """
    min_sil = MIN_SILENCE * (1.0 - 0.5 * aggressiveness)  # 0.3s → 0.15s at max

    result = []
    for seg in segments:
        cuts = list(seg.get("wordCuts") or [])

        refined: list[dict] = []
        seen_starts: set[float] = set()

        for wc in cuts:
            if wc.get("source") == "manual":
                # Manual cuts are placed deliberately — don't move them.
                s, e = wc["startSec"], wc["endSec"]
                min_duration = 0.0
            else:
                # Find actual end of preceding word (cut start) via energy envelope.
                s = _find_speech_end(audio, sr, wc["startSec"], budget_sec)
                # Find actual start of following word (cut end) via onset detection.
                e = _find_speech_onset(audio, sr, wc["endSec"], budget_sec)
                min_duration = MIN_SPAN

            if e - s > min_duration and s not in seen_starts:
                refined.append({**wc, "startSec": round(s, 4), "endSec": round(e, 4)})
                seen_starts.add(s)

        # Add silence-based dead-air cuts for kept segments
        if add_silence_cuts and seg.get("keep", True):
            for sc in _find_silence_cuts(audio, sr, seg["startSec"], seg["endSec"]):
                covered = any(
                    existing["startSec"] <= sc["startSec"] < existing["endSec"]
                    for existing in refined
                )
                if not covered and sc["startSec"] not in seen_starts:
                    refined.append({**sc, "source": "silence"})
                    seen_starts.add(sc["startSec"])

        result.append({
            **seg,
            "wordCuts": sorted(refined, key=lambda x: x["startSec"]),
        })

    return result
