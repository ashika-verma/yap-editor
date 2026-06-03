#!/usr/bin/env python3
"""
Agent Orchestrator for the Yap Editor pipeline.

Runs five agents in order, with Gemini acting as a pipeline director
that configures each downstream agent based on video sensor data.

Pipeline:
  1. Sensor Array    — analyze.py + transcribe.py (parallel)
  2. Director        — Gemini reads sensor summary, emits per-agent config
  3. Narrative Arch  — Gemini keep/drop decisions
  4. Heuristic Surg  — zero-crossing word cuts + silence removal
  5. Continuity Guard — restores short context bridges around bad joins
  6. The Rhythmist   — J/L-cuts, ducking, zoom hints
  7. Integrity Lint  — deterministic QA pass

Usage:
  python orchestrator.py <video_path> [whisper_model]

Environment:
  GEMINI_API_KEY  — required for Director + Narrative Architect

Output: JSON to stdout — full edit plan consumed by Next.js transcribe route.
"""
from __future__ import annotations

import sys, json, os, subprocess
from concurrent.futures import ThreadPoolExecutor

# Ensure sibling scripts are importable regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Fix SSL certs on macOS before any HTTPS calls
try:
    import certifi as _certifi
    os.environ.setdefault("SSL_CERT_FILE", _certifi.where())
except ImportError:
    pass

import numpy as np

from surgeon   import load_audio_from_video, refine_word_cuts
from filler    import apply_filler_cuts
from rhythmist import apply_rhythm
from linter    import lint

# ── Config ───────────────────────────────────────────────────────────────────

PYTHON = sys.executable
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

FILLER_WORDS = [
    "um", "uh", "like", "you know", "literally", "basically",
    "honestly", "right", "so yeah", "kind of", "sort of", "i mean",
    "you see", "anyways",
]


# ── LLM helper ───────────────────────────────────────────────────────────────

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm as _llm

def _gemini_generate(
    api_key: str,
    prompt: str,
    schema: dict | None = None,
    model: str = "gemini-2.5-flash",
) -> str:
    """Call the configured LLM backend and return the raw text response."""
    return _llm.generate(prompt, schema=schema, model=model, api_key=api_key)


def _parse_json(text: str) -> dict:
    """Parse JSON from Gemini, stripping markdown fences if present."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]          # drop opening fence line
        t = t.rsplit("```", 1)[0].strip() # drop closing fence
    return json.loads(t)


# ── Step 1: Sensor Array (parallel) ──────────────────────────────────────────

def _run_script(script_name: str, args: list[str]) -> dict:
    path = os.path.join(SCRIPT_DIR, script_name)
    proc = subprocess.run(
        [PYTHON, path] + args,
        capture_output=True, text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{script_name} failed: {proc.stderr[-1200:]}")
    return json.loads(proc.stdout)


def run_sensor_array(video_path: str, whisper_model: str, no_vision: bool = False, transcribe_source: str | None = None) -> tuple[dict, dict]:
    """Run analyze.py and transcribe.py in parallel. Returns (analyze_result, whisper_result)."""
    analyze_args = [video_path] + (["--no-vision"] if no_vision else [])
    audio_source = transcribe_source or video_path
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_analyze    = pool.submit(_run_script, "analyze.py",   analyze_args)
        f_transcribe = pool.submit(_run_script, "transcribe.py", [audio_source, whisper_model])
        analyze_result   = f_analyze.result()
        transcribe_result = f_transcribe.result()
    return analyze_result, transcribe_result


# ── Step 2: Director (LLM pipeline config) ────────────────────────────────────

DIRECTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "content_type": {
            "type": "string",
            "description": "vlog | tutorial | interview | talking_head | other",
        },
        "surgeon": {
            "type": "object",
            "properties": {
                "aggressiveness": {"type": "number"},
                "add_silence_cuts": {"type": "boolean"},
            },
            "required": ["aggressiveness", "add_silence_cuts"],
        },
        "narrative": {
            "type": "object",
            "properties": {
                "cut_target_pct": {"type": "number"},
            },
            "required": ["cut_target_pct"],
        },
        "rhythmist": {
            "type": "object",
            "properties": {
                "j_cut_threshold": {"type": "number"},
                "l_cut_threshold": {"type": "number"},
                "ducking_enabled": {"type": "boolean"},
            },
            "required": ["j_cut_threshold", "l_cut_threshold", "ducking_enabled"],
        },
    },
    "required": ["content_type", "surgeon", "narrative", "rhythmist"],
}

_DIRECTOR_DEFAULTS = {
    "content_type": "vlog",
    "surgeon":   {"aggressiveness": 0.65, "add_silence_cuts": True},
    "narrative": {"cut_target_pct": 0.55},
    "rhythmist": {"j_cut_threshold": 0.30, "l_cut_threshold": 0.30, "ducking_enabled": True},
}


def _fmt_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    frac = int((seconds - int(seconds)) * 10)
    return f"{m}:{s:02d}.{frac}"


def _has_filler(text: str) -> bool:
    import re
    lower = text.lower()
    return any(
        re.search(r"\b" + f.replace(" ", r"\s+") + r"\b", lower)
        for f in FILLER_WORDS
    )


def _rule_based_fallback_segments(whisper_segments: list[dict]) -> list[dict]:
    """Return §3.3 segments shaped like tighten output, using simple heuristic keep/drop."""
    ABANDON = {"anyway", "i don't know", "i'm not sure", "so yeah", "nevermind"}
    out = []
    for seg in whisper_segments:
        text  = seg.get("text", "").strip()
        words = text.split()
        lower = text.lower()
        is_filler_segment = _has_filler(text) and len(words) < 8
        is_fragment       = len(words) < 4
        is_abandon        = any(ph in lower for ph in ABANDON) and len(words) < 10
        drop = is_filler_segment or is_fragment or is_abandon
        start_sec = seg.get("startSec", seg.get("start", 0.0))
        end_sec   = seg.get("endSec",   seg.get("end",   0.0))
        m, s = divmod(int(start_sec), 60)
        em, es = divmod(int(end_sec), 60)
        out.append({
            "startSec":       start_sec,
            "endSec":         end_sec,
            "start":          f"{m}:{s:02d}",
            "end":            f"{em}:{es:02d}",
            "text":           text,
            "keep":           not drop,
            "words":          seg.get("words", []),
            "wordCuts":       [],
            "decisionSource": "pipeline",
            "dropReason":     "filler" if is_filler_segment else ("false_start" if is_fragment else ("ramble" if is_abandon else "")),
            "jobRisk":        "",
            "motionScore":    0,
            "audioRms":       0,
            "energyScore":    0,
            "visualTag":      None,
        })
    return out


# Words that signal a dangling reference to cut context at a segment boundary.
_DANGLING_OPENERS = {
    "because", "which", "that", "so", "and", "but", "or",
    "although", "though", "since", "as", "if", "when", "where",
    "who", "whose", "whom", "whether", "while", "whereas",
    "however", "therefore", "thus", "hence", "nevertheless",
}

# Words that signal the speaker is restarting the previous thought mid-stream.
_RESTART_MARKERS = {
    "basically", "i mean", "what i mean", "actually", "so anyway",
    "okay so", "right so", "well", "anyway", "alright", "look",
    "so basically", "i guess", "like i said", "in other words",
}

# Words that, at end of a segment, indicate an incomplete sentence.
_INCOMPLETE_ENDERS = {
    "and", "or", "but", "so", "the", "a", "an", "to", "for", "of",
    "in", "at", "with", "that", "which", "then", "go", "into",
    "is", "was", "were", "are",
}

# Reasons safe to override for any dangling-opener / bridge repair.
# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        sys.stdout.write(json.dumps({"error": "Usage: orchestrator.py <video_path> [whisper_model]"}) + "\n")
        sys.exit(1)

    video_path    = sys.argv[1]
    no_vision     = "--no-vision" in sys.argv
    no_llm        = "--no-llm"     in sys.argv
    save_fixture  = "--save-fixture" in sys.argv
    do_enhance    = "--enhance" in sys.argv
    # Positional args are everything that isn't a flag
    positional = [a for a in sys.argv[2:] if not a.startswith("--")]
    whisper_model      = positional[0] if len(positional) > 0 else "mlx-community/whisper-large-v3-turbo"
    filler_sensitivity = positional[1] if len(positional) > 1 else "balanced"
    api_key       = os.environ.get("GEMINI_API_KEY", "")

    # Redirect stdout → stderr during noisy library init
    real_stdout = sys.stdout
    sys.stdout  = sys.stderr

    try:
        # ── 0. Audio Enhancement (optional) ───────────────────────────────────
        enhanced_audio_path: str | None = None
        transcribe_source = video_path
        if do_enhance:
            print("▶ Audio Enhancement: resemble-enhance denoiser + EQ...", file=sys.stderr)
            base = os.path.splitext(video_path)[0]
            enhanced_audio_path = base + "_enhanced.wav"
            enhance_script = os.path.join(SCRIPT_DIR, "enhance.py")
            try:
                proc = subprocess.run(
                    [PYTHON, enhance_script, video_path, enhanced_audio_path],
                    capture_output=True, text=True, timeout=600,
                )
                if proc.returncode != 0:
                    print(f"  Enhancement failed: {proc.stderr[-500:]}", file=sys.stderr)
                    enhanced_audio_path = None
                else:
                    transcribe_source = enhanced_audio_path
                    print(f"  Enhanced audio → {enhanced_audio_path}", file=sys.stderr)
            except Exception as e:
                print(f"  Enhancement skipped: {e}", file=sys.stderr)
                enhanced_audio_path = None

        # ── 1. Sensor Array ────────────────────────────────────────────────────
        print("▶ Sensor Array: running analyze + transcribe in parallel...", file=sys.stderr)
        try:
            analyze_result, whisper_result = run_sensor_array(video_path, whisper_model, no_vision=no_vision, transcribe_source=transcribe_source)
        except Exception as e:
            sys.stdout = real_stdout
            sys.stdout.write(json.dumps({"error": f"Sensor Array failed: {e}"}) + "\n")
            sys.exit(1)

        if not whisper_result.get("segments"):
            sys.stdout = real_stdout
            sys.stdout.write(json.dumps({"error": "No speech detected in video"}) + "\n")
            sys.exit(1)

        duration = analyze_result.get("duration", whisper_result["segments"][-1]["end"])
        buckets  = analyze_result.get("buckets", [])

        # ── 2. Tighten: holistic word-level edit + critic loop ────────────────
        print("▶ Tighten: holistic word-level edit + critic loop...", file=sys.stderr)
        from tighten import tighten
        if no_llm:
            print("  LLM disabled — using rule-based cuts", file=sys.stderr)
            segments = _rule_based_fallback_segments(whisper_result["segments"])
            summary = ""
            low_confidence = False
            rationale = ""
        else:
            try:
                if api_key or _llm.is_local():
                    segments, summary, low_confidence, rationale = tighten(
                        whisper_result["segments"], api_key
                    )
                else:
                    segments = _rule_based_fallback_segments(whisper_result["segments"])
                    summary = ""
                    low_confidence = False
                    rationale = ""
            except Exception as e:
                print(f"  Tighten failed ({e}), using rule-based fallback", file=sys.stderr)
                segments = _rule_based_fallback_segments(whisper_result["segments"])
                summary = ""
                low_confidence = True
                rationale = ""

        segments = apply_filler_cuts(segments, filler_sensitivity, preserve_manual=True)

        # ── 3. Heuristic Surgeon ───────────────────────────────────────────────
        print("▶ Heuristic Surgeon: zero-crossing word cuts + silence removal...", file=sys.stderr)
        try:
            audio, sr = load_audio_from_video(video_path)
            segments  = refine_word_cuts(
                audio, sr, segments,
                add_silence_cuts=True,
                aggressiveness=0.65,
            )
        except Exception as e:
            print(f"  Surgeon failed ({e}), skipping zero-crossing snap", file=sys.stderr)

        # ── 4. The Rhythmist ───────────────────────────────────────────────────
        print("▶ The Rhythmist: J/L-cuts, ducking, zoom hints...", file=sys.stderr)
        segments = apply_rhythm(segments, buckets)

        # ── 5. Integrity Linter ────────────────────────────────────────────────
        print("▶ Integrity Linter: QA checks...", file=sys.stderr)
        lint_result = lint(segments, duration)
        segments    = lint_result["segments"]
        if not lint_result["passed"]:
            errors = [i for i in lint_result["issues"] if i["severity"] == "error"]
            print(f"  Linter: {len(errors)} error(s) found", file=sys.stderr)

        # ── Build output ───────────────────────────────────────────────────────
        total_sec = whisper_result["segments"][-1]["end"] if whisper_result["segments"] else 0
        kept_sec  = sum(s["endSec"] - s["startSec"] for s in segments if s.get("keep"))

        output = {
            "segments":          segments,
            "summary":           summary,
            "rationale":         rationale,
            "lowConfidence":     low_confidence,
            "totalDuration":     _fmt_ts(total_sec),
            "editedDuration":    _fmt_ts(kept_sec),
            "language":          whisper_result.get("language", "en"),
            "directorConfig":    {},
            "settings":          {"fillerSensitivity": filler_sensitivity},
            "narrativeAnalysis": {},
            "linterPassed":      lint_result["passed"],
            "linterIssues":      lint_result["issues"],
            "enhancedAudioPath": enhanced_audio_path,
        }


    finally:
        sys.stdout = real_stdout

    if save_fixture:
        import datetime, hashlib
        fixtures_dir = os.path.join(SCRIPT_DIR, "..", "fixtures")
        os.makedirs(fixtures_dir, exist_ok=True)
        source_id = hashlib.md5(os.path.basename(video_path).encode()).hexdigest()[:8]
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fixture_path = os.path.join(fixtures_dir, f"{source_id}_{ts}.json")
        fixture = {
            "metadata": {
                "source": os.path.basename(video_path),
                "captured_at": datetime.datetime.now().isoformat(),
                "whisper_model": whisper_model,
                "filler_sensitivity": filler_sensitivity,
                "no_vision": no_vision,
            },
            "plan": output,
        }
        with open(fixture_path, "w") as f:
            json.dump(fixture, f, indent=2)
        print(f"  Fixture saved → {fixture_path}", file=sys.stderr)

    sys.stdout.write(json.dumps(output) + "\n")


if __name__ == "__main__":
    main()
