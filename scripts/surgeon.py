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
MIN_SILENCE  = 0.30    # ignore silence runs shorter than this (seconds)
FRAME_SEC    = 0.010   # RMS frame size for energy analysis (10 ms)

# Envelope follower parameters (noise-gate model).
# These are the physical parameters that matter — not arbitrary dBFS thresholds.
GATE_ATTACK_MS  = 5    # ms — gate opens fast (onset detection isn't affected)
GATE_RELEASE_MS = 40   # ms — gate closes slowly: holds through trailing consonants
                        #       40 ms covers English stops/fricatives (t, s, k, p)
                        #       Increase to 60–80 ms for reverberant rooms.

# Adaptive threshold: speech is X dB above the estimated noise floor.
# Relative to the recording rather than absolute dBFS — works regardless of
# mic gain, room level, or whether the speaker is close or far from the mic.
SPEECH_SNR_DB   = 12   # dB above noise floor → "speech"
SILENCE_SNR_DB  =  4   # dB above noise floor → "definitely silent" (for dead-air)


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


def _estimate_noise_floor(audio: np.ndarray, sr: int) -> float:
    """15th-percentile RMS across 25 ms frames — captures near-silence level."""
    frame_len = max(1, int(0.025 * sr))
    n_frames  = len(audio) // frame_len
    if n_frames == 0:
        return 1e-6
    frames = audio[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms    = np.sqrt(np.mean(frames ** 2, axis=1))
    return float(np.percentile(rms, 15)) + 1e-8


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


def _apply_gate_envelope(db: np.ndarray, attack_frames: int, release_frames: int) -> np.ndarray:
    """
    Noise-gate envelope follower on a dBFS RMS sequence.

    Models how a hardware noise gate works: the envelope rises fast (attack)
    and falls slowly (release). The release time is what holds the gate open
    through trailing consonants — the physical parameter that matters, not a
    fixed dBFS threshold.

    Returns the smoothed envelope in dBFS.
    """
    if db.size == 0:
        return db
    env = db.copy().astype(float)
    for i in range(1, len(env)):
        if db[i] > env[i - 1]:
            # Attack: blend toward the new peak quickly
            alpha = 1.0 - (1.0 / max(attack_frames, 1))
            env[i] = env[i - 1] * alpha + db[i] * (1.0 - alpha)
        else:
            # Release: decay slowly — keeps gate open through consonant tails
            alpha = 1.0 - (1.0 / max(release_frames, 1))
            env[i] = env[i - 1] * alpha + db[i] * (1.0 - alpha)
    return env


def _find_word_tail(
    audio: np.ndarray, sr: int, t: float,
    noise_floor_db: float,
    max_extend: float = 0.25,
) -> float:
    """
    Extend t forward to include the natural tail of a word still ringing.

    Uses an envelope follower (GATE_RELEASE_MS release) so the gate stays open
    through trailing consonants that have decayed from peak but are still audible.
    Only extends when the boundary is actively in speech (first frame above the
    adaptive speech threshold); returns t unchanged when already at silence, to
    avoid pulling in dropped content.
    """
    db = _rms_db(audio, sr, t, t + max_extend)
    if db.size == 0:
        return t

    frame_len     = max(int(FRAME_SEC * sr), 1)
    speech_thresh = noise_floor_db + SPEECH_SNR_DB

    # Guard: if the boundary is already below speech threshold, cut is clean.
    if db[0] <= speech_thresh:
        return t

    attack_frames  = max(1, int(GATE_ATTACK_MS  / 1000 / FRAME_SEC))
    release_frames = max(1, int(GATE_RELEASE_MS / 1000 / FRAME_SEC))
    env = _apply_gate_envelope(db, attack_frames, release_frames)

    speech_frames = np.where(env > speech_thresh)[0]
    if speech_frames.size == 0:
        return t

    last_frame = int(speech_frames[-1])
    t_end = t + (last_frame + 1) * frame_len / sr
    return _snap_to_zero_crossing(audio, sr, t_end, 0.015)


def _find_speech_end(
    audio: np.ndarray, sr: int, t_hint: float, budget: float,
    noise_floor_db: float,
) -> float:
    """
    Find where speech actually ends near t_hint.

    Searches [t_hint - budget, t_hint] (backward only, so the filler word at
    t_hint doesn't pollute the search). Applies an envelope follower with
    GATE_RELEASE_MS release so trailing consonants are included in the
    "speech" region. Threshold is adaptive: SPEECH_SNR_DB above the noise floor.
    """
    t0 = max(0.0, t_hint - budget)
    t1 = min(len(audio) / sr, t_hint)   # don't search into the filler itself
    db = _rms_db(audio, sr, t0, t1)
    if db.size == 0:
        return t_hint

    frame_len     = max(int(FRAME_SEC * sr), 1)
    speech_thresh = noise_floor_db + SPEECH_SNR_DB

    attack_frames  = max(1, int(GATE_ATTACK_MS  / 1000 / FRAME_SEC))
    release_frames = max(1, int(GATE_RELEASE_MS / 1000 / FRAME_SEC))
    env = _apply_gate_envelope(db, attack_frames, release_frames)

    speech_frames = np.where(env > speech_thresh)[0]
    if speech_frames.size == 0:
        return t_hint  # window is all silent — Whisper hint is fine

    last_frame = int(speech_frames[-1])
    t = t0 + last_frame * frame_len / sr
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
    audio: np.ndarray, sr: int, seg_start: float, seg_end: float,
    noise_floor_db: float,
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

    silence_thresh = noise_floor_db + SILENCE_SNR_DB
    silent_frames = rms_db < silence_thresh
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

    # Estimate noise floor once for the whole file — used as the adaptive
    # threshold base for all boundary detection in this pass.
    noise_floor     = _estimate_noise_floor(audio, sr)
    noise_floor_db  = float(librosa.power_to_db(np.array([noise_floor ** 2 + 1e-10]))[0])

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
                # Find actual end of preceding word (cut start) via envelope follower.
                s = _find_speech_end(audio, sr, wc["startSec"], budget_sec, noise_floor_db)
                # Find actual start of following word (cut end) via onset detection.
                e = _find_speech_onset(audio, sr, wc["endSec"], budget_sec)
                min_duration = MIN_SPAN

            if e - s > min_duration and s not in seen_starts:
                refined.append({**wc, "startSec": round(s, 4), "endSec": round(e, 4)})
                seen_starts.add(s)

        # Add silence-based dead-air cuts for kept segments
        if add_silence_cuts and seg.get("keep", True):
            for sc in _find_silence_cuts(audio, sr, seg["startSec"], seg["endSec"], noise_floor_db):
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

    # ── Snap segment endSec/startSec at real cut boundaries ────────────────────
    # surgeon.py already snaps word-cut boundaries acoustically; this pass does
    # the same for the segment edges that sit at real gaps (dropped content on
    # either side). Without this, Whisper boundaries — which can land mid-word —
    # become the export cut points, causing audible clipping at segment ends.
    #
    # endSec snap: search forward up to budget/4 to include the full tail of the
    # last word (avoids early truncation). Capped so we don't pull in content
    # from the dropped next segment beyond one word-tail (~50 ms).
    #
    # startSec snap: find the actual speech onset after a gap, trimming dead air.
    #
    # Only applied to boundaries with a real gap (>50 ms); adjacent kept segments
    # (gap ≈ 0) have continuous speech and do not need boundary correction.

    GAP_MIN = 0.05  # seconds — below this, segments are effectively adjacent

    for i, seg in enumerate(result):
        if not seg.get("keep"):
            continue

        prev_kept  = next((result[j] for j in range(i - 1, -1, -1) if result[j].get("keep")), None)
        next_kept  = next((result[j] for j in range(i + 1,  len(result)) if result[j].get("keep")), None)
        gap_before = (seg["startSec"] - prev_kept["endSec"]) if prev_kept else float("inf")
        gap_after  = (next_kept["startSec"] - seg["endSec"]) if next_kept else float("inf")

        updated = dict(result[i])

        if gap_after >= GAP_MIN:
            # Extend endSec to include the tail of the last word.
            # _find_word_tail searches forward from endSec up to 250 ms.
            # Cap: don't exceed (next_kept.startSec - 3 ms) to avoid overlap.
            cap = (next_kept["startSec"] - 0.003) if next_kept else float("inf")
            new_end = _find_word_tail(audio, sr, seg["endSec"], noise_floor_db, max_extend=0.25)
            new_end = min(new_end, cap)
            if new_end != seg["endSec"]:
                updated["endSec"] = round(new_end, 4)

        if gap_before >= GAP_MIN:
            # Trim startSec to the actual speech onset after the gap.
            # Cap: don't go earlier than (prev_kept.endSec + 3 ms).
            floor = (prev_kept["endSec"] + 0.003) if prev_kept else 0.0
            new_start = _find_speech_onset(audio, sr, seg["startSec"], budget_sec)
            new_start = max(new_start, floor)
            if new_start != seg["startSec"]:
                updated["startSec"] = round(new_start, 4)

        result[i] = updated

    return result
