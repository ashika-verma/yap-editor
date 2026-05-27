#!/usr/bin/env python3
"""
Continuity Guard: deterministic post-pass that repairs edit-plan jumps.

Imported by orchestrator.py; not meant to be run standalone.
"""
from __future__ import annotations

import re

MAX_BRIDGE_SEC = 7.5
MAX_BRIDGE_SEGMENTS = 2
MAX_RESTORED_SEC = 4.0       # for pair-bridge restoration
MAX_DIRECT_BRIDGE_SEC = 20.0 # for segments directly between two kept neighbours
MIN_WORD_CUT_MARGIN = 0.06

SOFT_DROP_REASONS = {
    "",
    "filler",
    "false_start",
    "ramble",
    "superseded",
}

DEPENDENT_STARTERS = {
    "and",
    "but",
    "so",
    "then",
    "because",
    "however",
    "therefore",
    "though",
    "although",
    "which",
    "who",
    "whom",
    "whose",
    "that",
    "this",
    "these",
    "those",
    "it",
    "they",
    "them",
    "he",
    "she",
    "we",
    "if",
    "when",
    "where",
    "while",
    "aka",
}

LIST_STARTERS = {
    "one",
    "two",
    "three",
    "first",
    "second",
    "third",
    "another",
    "also",
    "finally",
}

OPEN_ENDINGS = {
    "and",
    "but",
    "or",
    "because",
    "if",
    "when",
    "where",
    "which",
    "who",
    "whom",
    "whose",
    "that",
    "to",
    "of",
    "for",
    "with",
    "about",
    "into",
    "like",
    "as",
}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _first_word(segment: dict) -> str:
    words = _words(segment.get("text", ""))
    return words[0] if words else ""


def _last_word(segment: dict) -> str:
    words = _words(segment.get("text", ""))
    return words[-1] if words else ""


def _duration(segment: dict) -> float:
    return float(segment["endSec"] - segment["startSec"])


def _starts_dependently(segment: dict) -> bool:
    text = segment.get("text", "").strip()
    first_word = _first_word(segment)
    first_two = " ".join(_words(text)[:2])
    return (
        first_word in DEPENDENT_STARTERS
        or first_word in LIST_STARTERS
        or first_two in {"with that", "and then", "so then", "even though"}
    )


def _ends_openly(segment: dict) -> bool:
    text = segment.get("text", "").strip()
    if not text:
        return False
    return text[-1] not in ".?!" or _last_word(segment) in OPEN_ENDINGS


def _is_restorable_bridge(segment: dict, direct: bool = False) -> bool:
    if segment.get("decisionSource") == "user":
        return False
    reason = segment.get("dropReason", "")
    if reason not in SOFT_DROP_REASONS:
        return False
    limit = MAX_DIRECT_BRIDGE_SEC if direct else MAX_RESTORED_SEC
    return _duration(segment) <= limit


def _restore(segment: dict, reason: str, repairs: list[dict], segment_index: int) -> None:
    original_reason = segment.get("dropReason", "")
    segment["keep"] = True
    segment["decisionSource"] = "repair"
    segment["dropReason"] = ""
    segment["continuityRepair"] = {
        "reason": reason,
        "originalDropReason": original_reason,
    }
    repairs.append({
        "severity": "warning",
        "type": "continuity_bridge_restored",
        "segIdx": segment_index,
        "detail": f"restored {reason} bridge originally marked '{original_reason or 'drop'}'",
    })


def _sanitize_word_cuts(segment: dict, repairs: list[dict], segment_index: int) -> None:
    safe_cuts = []
    changed = False
    segment_start = segment["startSec"]
    segment_end = segment["endSec"]

    for word_cut in segment.get("wordCuts") or []:
        cut_start = float(word_cut["startSec"])
        cut_end = float(word_cut["endSec"])
        too_close_to_edge = (
            cut_start - segment_start < MIN_WORD_CUT_MARGIN
            or segment_end - cut_end < MIN_WORD_CUT_MARGIN
        )
        inverted = cut_end <= cut_start
        edge_exempt = word_cut.get("source") in ("manual", "silence")
        if inverted or (too_close_to_edge and not edge_exempt):
            changed = True
            continue
        safe_cuts.append(word_cut)

    if changed:
        segment["wordCuts"] = safe_cuts
        repairs.append({
            "severity": "warning",
            "type": "unsafe_word_cut_removed",
            "segIdx": segment_index,
            "detail": "removed a filler/silence cut too close to a segment boundary",
        })


def sanitize_word_cuts(segments: list[dict]) -> list[dict]:
    """Run only the word-cut edge sanitization pass, without bridge restoration."""
    result = [dict(segment) for segment in segments]
    repairs: list[dict] = []
    for segment_index, segment in enumerate(result):
        _sanitize_word_cuts(segment, repairs, segment_index)
    return result


def apply_continuity_guard(segments: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Restore short context bridges when adjacent kept segments form an abrupt join.

    Returns (segments, repairs). The returned segments are shallow copies.
    """
    result = [dict(segment) for segment in segments]
    repairs: list[dict] = []

    for segment_index, segment in enumerate(result):
        _sanitize_word_cuts(segment, repairs, segment_index)

    kept_indices = [
        segment_index
        for segment_index, segment in enumerate(result)
        if segment.get("keep", True)
    ]

    for previous_kept_index, next_kept_index in zip(kept_indices, kept_indices[1:]):
        bridge_indices = list(range(previous_kept_index + 1, next_kept_index))
        if not bridge_indices:
            continue

        bridge_segments = [result[bridge_index] for bridge_index in bridge_indices]
        bridge_duration = sum(_duration(segment) for segment in bridge_segments)
        if (
            bridge_duration > MAX_BRIDGE_SEC
            or len(bridge_segments) > MAX_BRIDGE_SEGMENTS
        ):
            continue

        previous_segment = result[previous_kept_index]
        next_segment = result[next_kept_index]
        abrupt_join = _ends_openly(previous_segment) or _starts_dependently(next_segment)
        if not abrupt_join:
            continue

        if _starts_dependently(next_segment):
            bridge_index = bridge_indices[-1]
            bridge_segment = result[bridge_index]
            if not bridge_segment.get("keep", True) and _is_restorable_bridge(bridge_segment):
                _restore(bridge_segment, "incoming segment depends on missing context", repairs, bridge_index)

        if _ends_openly(previous_segment):
            bridge_index = bridge_indices[0]
            bridge_segment = result[bridge_index]
            if not bridge_segment.get("keep", True) and _is_restorable_bridge(bridge_segment):
                _restore(bridge_segment, "previous segment ends mid-thought", repairs, bridge_index)

    for segment_index, segment in enumerate(result):
        if segment.get("keep", True) or not _is_restorable_bridge(segment):
            continue

        previous_segment = result[segment_index - 1] if segment_index > 0 else None
        next_segment = result[segment_index + 1] if segment_index + 1 < len(result) else None
        bridges_kept_neighbors = (
            previous_segment is not None
            and next_segment is not None
            and previous_segment.get("keep", True)
            and next_segment.get("keep", True)
        )
        if not bridges_kept_neighbors:
            continue

        if _starts_dependently(segment) or _ends_openly(segment):
            if _is_restorable_bridge(segment, direct=True):
                _restore(segment, "dropped segment carries connective tissue", repairs, segment_index)

    return result, repairs
