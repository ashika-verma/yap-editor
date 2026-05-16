#!/usr/bin/env python3
"""
Re-run backend-owned edit-plan post-processing while preserving manual edits.
Reads {"filePath": str, "plan": dict, "fillerSensitivity": str?} from stdin.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from continuity import apply_continuity_guard
from filler import apply_filler_cuts
from linter import lint
from surgeon import load_audio_from_video, refine_word_cuts


def _fmt_ts(sec: float) -> str:
    m = int(sec // 60)
    s = int(sec % 60)
    return f"{m}:{s:02d}"


def _duration_from_plan(plan: dict) -> float:
    segments = plan.get("segments") or []
    if not segments:
        return 0.0
    return max(float(segment.get("endSec", 0)) for segment in segments)


def rebuild_plan(file_path: str, plan: dict, filler_sensitivity: str | None = None) -> dict:
    sensitivity = (
        filler_sensitivity
        or plan.get("settings", {}).get("fillerSensitivity")
        or "balanced"
    )
    segments = plan.get("segments") or []
    segments = apply_filler_cuts(segments, sensitivity, preserve_manual=True)

    if file_path and os.path.exists(file_path):
        try:
            audio, sr = load_audio_from_video(file_path)
            segments = refine_word_cuts(audio, sr, segments, add_silence_cuts=True)
        except Exception as exc:
            print(f"replan surgeon failed: {exc}", file=sys.stderr)

    segments, continuity_issues = apply_continuity_guard(segments)
    duration = _duration_from_plan({"segments": segments})
    lint_result = lint(segments, duration)
    segments = lint_result["segments"]
    kept_sec = sum(
        segment["endSec"] - segment["startSec"]
        for segment in segments
        if segment.get("keep", True)
    )

    return {
        **plan,
        "version": 1,
        "settings": {
            **(plan.get("settings") or {}),
            "fillerSensitivity": sensitivity,
        },
        "segments": segments,
        "issues": continuity_issues + lint_result["issues"],
        "linterIssues": continuity_issues + lint_result["issues"],
        "linterPassed": lint_result["passed"],
        "editedDuration": _fmt_ts(kept_sec),
    }


def main() -> None:
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = json.loads(sys.stdin.read())
    plan = rebuild_plan(
        payload.get("filePath", ""),
        payload.get("plan") or {},
        payload.get("fillerSensitivity"),
    )
    sys.stdout.write(json.dumps({"plan": plan}) + "\n")


if __name__ == "__main__":
    main()
