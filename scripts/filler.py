#!/usr/bin/env python3
"""
Backend filler-word pass for authoritative edit plans.
"""
from __future__ import annotations

import re

FILLER_TIERS = {
    "conservative": {"um", "uh", "hmm", "er", "erm"},
    "balanced": {"um", "uh", "hmm", "er", "erm"},
    "aggressive": {
        "um",
        "uh",
        "hmm",
        "er",
        "erm",
        "like",
        "literally",
        "basically",
        "actually",
        "honestly",
        "anyways",
    },
}

# Multi-word filler phrases (tuples of cleaned lowercase tokens).
FILLER_PHRASE_TIERS: dict[str, frozenset[tuple[str, ...]]] = {
    "conservative": frozenset(),
    "balanced":     frozenset({("you", "know")}),
    "aggressive":   frozenset({("you", "know"), ("i", "mean"), ("you", "see")}),
}

MIN_FILLER_DURATION_SEC = 0.08
MAX_FILLER_DURATION_SEC = 0.75
MAX_PHRASE_DURATION_SEC = 1.5   # phrases can span longer than single fillers
MIN_SEAM_GAP_SEC = 0.035
MAX_GAP_ABSORB_SEC = 0.08


def _clean_word(word: str) -> str:
    return re.sub(r"[^a-z]", "", word.lower())


def _word_cut_id(segment: dict, word_index: int) -> str:
    return f"{float(segment['startSec']):.3f}:{word_index}"


def _build_word_cut(
    segment: dict,
    word: dict,
    word_index: int,
    previous_word: dict | None,
    next_word: dict | None,
    source: str,
) -> dict | None:
    duration = float(word["end"] - word["start"])
    gap_before = (
        float(word["start"] - previous_word["end"])
        if previous_word
        else MAX_GAP_ABSORB_SEC
    )
    gap_after = (
        float(next_word["start"] - word["end"])
        if next_word
        else MAX_GAP_ABSORB_SEC
    )
    has_safe_seam = (
        previous_word is None
        or next_word is None
        or gap_before >= MIN_SEAM_GAP_SEC
        or gap_after >= MIN_SEAM_GAP_SEC
    )

    if (
        duration < MIN_FILLER_DURATION_SEC
        or duration > MAX_FILLER_DURATION_SEC
        or not has_safe_seam
    ):
        return None

    return {
        "id": _word_cut_id(segment, word_index),
        "startSec": round(float(word["start"]), 4),
        "endSec": round(float(word["end"]), 4),
        "word": word["word"],
        "source": source,
        "renderStartSec": round(
            float(word["start"]) - min(max(gap_before, 0) / 2, MAX_GAP_ABSORB_SEC),
            4,
        ),
        "renderEndSec": round(
            float(word["end"]) + min(max(gap_after, 0) / 2, MAX_GAP_ABSORB_SEC),
            4,
        ),
    }


def _build_phrase_cut(
    segment: dict,
    words: list[dict],
    start_idx: int,
    end_idx: int,
    source: str,
) -> dict | None:
    first, last = words[start_idx], words[end_idx]
    duration = float(last["end"] - first["start"])
    previous_word = words[start_idx - 1] if start_idx > 0 else None
    next_word = words[end_idx + 1] if end_idx + 1 < len(words) else None
    gap_before = float(first["start"] - previous_word["end"]) if previous_word else MAX_GAP_ABSORB_SEC
    gap_after = float(next_word["start"] - last["end"]) if next_word else MAX_GAP_ABSORB_SEC
    has_safe_seam = (
        previous_word is None
        or next_word is None
        or gap_before >= MIN_SEAM_GAP_SEC
        or gap_after >= MIN_SEAM_GAP_SEC
    )
    if duration < MIN_FILLER_DURATION_SEC or duration > MAX_PHRASE_DURATION_SEC or not has_safe_seam:
        return None
    phrase_text = " ".join(w.get("word", "").strip() for w in words[start_idx : end_idx + 1])
    return {
        "id": _word_cut_id(segment, start_idx),
        "startSec": round(float(first["start"]), 4),
        "endSec": round(float(last["end"]), 4),
        "word": phrase_text,
        "source": source,
        "renderStartSec": round(float(first["start"]) - min(max(gap_before, 0) / 2, MAX_GAP_ABSORB_SEC), 4),
        "renderEndSec": round(float(last["end"]) + min(max(gap_after, 0) / 2, MAX_GAP_ABSORB_SEC), 4),
    }


def compute_filler_cuts(segment: dict, sensitivity: str) -> list[dict]:
    fillers = FILLER_TIERS.get(sensitivity, FILLER_TIERS["balanced"])
    phrases = FILLER_PHRASE_TIERS.get(sensitivity, FILLER_PHRASE_TIERS["balanced"])
    words = segment.get("words") or []
    cuts: list[dict] = []
    used_ids: set[str] = set()
    skip_indices: set[int] = set()

    for word_index, word in enumerate(words):
        if word_index in skip_indices:
            continue
        clean = _clean_word(word.get("word", ""))
        cut_id = _word_cut_id(segment, word_index)

        # Multi-word phrase check (takes priority over single-word)
        phrase_matched = False
        for phrase in phrases:
            if clean != phrase[0]:
                continue
            if any(
                word_index + off >= len(words)
                or _clean_word(words[word_index + off].get("word", "")) != tok
                for off, tok in enumerate(phrase[1:], 1)
            ):
                continue
            if cut_id not in used_ids:
                cut = _build_phrase_cut(segment, words, word_index, word_index + len(phrase) - 1, "filler")
                if cut:
                    cuts.append(cut)
                    used_ids.add(cut_id)
                    for off in range(1, len(phrase)):
                        skip_indices.add(word_index + off)
                phrase_matched = True
                break
        if phrase_matched:
            continue

        if clean in fillers and cut_id not in used_ids:
            cut = _build_word_cut(
                segment,
                word,
                word_index,
                words[word_index - 1] if word_index > 0 else None,
                words[word_index + 1] if word_index + 1 < len(words) else None,
                "filler",
            )
            if cut:
                cuts.append(cut)
                used_ids.add(cut_id)
            continue

        if sensitivity != "conservative" and word_index + 1 < len(words) and len(clean) > 1:
            next_clean = _clean_word(words[word_index + 1].get("word", ""))
            if clean == next_clean and cut_id not in used_ids:
                cut = _build_word_cut(
                    segment,
                    word,
                    word_index,
                    words[word_index - 1] if word_index > 0 else None,
                    words[word_index + 1],
                    "filler",
                )
                if cut:
                    cuts.append(cut)
                    used_ids.add(cut_id)

    return cuts


def apply_filler_cuts(
    segments: list[dict],
    sensitivity: str,
    preserve_manual: bool = True,
) -> list[dict]:
    result = []
    for segment in segments:
        manual_cuts = [
            cut
            for cut in segment.get("wordCuts") or []
            if preserve_manual and cut.get("source") in ("manual", "trim")
        ]
        auto_cuts = compute_filler_cuts(segment, sensitivity)
        seen = {cut.get("id") for cut in manual_cuts if cut.get("id")}
        merged = manual_cuts + [
            cut for cut in auto_cuts if not cut.get("id") or cut.get("id") not in seen
        ]
        result.append({
            **segment,
            "wordCuts": sorted(merged, key=lambda cut: cut["startSec"]),
        })
    return result
