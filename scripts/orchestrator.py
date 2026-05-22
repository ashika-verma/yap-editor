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
from continuity import apply_continuity_guard
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


def run_sensor_array(video_path: str, whisper_model: str, no_vision: bool = False) -> tuple[dict, dict]:
    """Run analyze.py and transcribe.py in parallel. Returns (analyze_result, whisper_result)."""
    analyze_args = [video_path] + (["--no-vision"] if no_vision else [])
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_analyze    = pool.submit(_run_script, "analyze.py",   analyze_args)
        f_transcribe = pool.submit(_run_script, "transcribe.py", [video_path, whisper_model])
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


def run_director(
    api_key: str,
    analyze_result: dict,
    whisper_result: dict,
    duration: float,
) -> dict:
    """Ask Gemini to configure the downstream agents based on sensor data."""
    buckets = analyze_result.get("buckets", [])
    n = len(buckets)
    silence_pct = sum(1 for b in buckets if b.get("silence")) / max(n, 1)
    avg_motion  = sum(b.get("motion_score", 0) for b in buckets) / max(n, 1)
    avg_rms     = sum(b.get("audio_rms", 0)    for b in buckets) / max(n, 1)
    n_segs      = len(whisper_result.get("segments", []))
    first_words = " ".join(
        s["text"] for s in whisper_result["segments"][:5]
    ) if whisper_result.get("segments") else ""

    prompt = f"""You are configuring a video editing pipeline. Analyse these sensor metrics and set agent parameters.

Video stats:
- Duration: {duration:.1f}s ({n} one-second buckets)
- Segments detected: {n_segs}
- Average motion score: {avg_motion:.2f}
- Average audio RMS: {avg_rms:.2f}
- Silent buckets: {silence_pct*100:.1f}%
- First 10 segments (text preview): "{first_words[:500]}"

Configure:
- content_type: what kind of video is this? (vlog | tutorial | interview | talking_head | other)
- surgeon.aggressiveness: 0.0–1.0 (aggressive silence/filler removal)
- surgeon.add_silence_cuts: should dead air be cut automatically?
- narrative.cut_target_pct: what fraction of segments should be dropped?
  IMPORTANT: Personal vlogs, rambling confessionals, and unscripted talking-head content
  typically require 40–65% cuts to reach a tight edit. Tutorials/interviews: 20–35%.
- rhythmist.j_cut_threshold: energy delta above which to add a J-cut (0.2–0.5)
- rhythmist.l_cut_threshold: energy drop below which to add an L-cut (0.2–0.5)
- rhythmist.ducking_enabled: should B-roll audio be ducked?

Return only the JSON config."""

    try:
        text = _gemini_generate(api_key, prompt, schema=DIRECTOR_SCHEMA)
        cfg  = _parse_json(text)
        # Merge with defaults (in case Gemini omits any key)
        for key, default in _DIRECTOR_DEFAULTS.items():
            if key not in cfg:
                cfg[key] = default
            elif isinstance(default, dict):
                for k2, v2 in default.items():
                    cfg[key].setdefault(k2, v2)
        return cfg
    except Exception as e:
        print(f"Director LLM failed ({e}), using defaults", file=sys.stderr)
        return dict(_DIRECTOR_DEFAULTS)


# ── Step 3: Narrative Architect (two-pass Gemini) ────────────────────────────
#
# Pass 1 — Structural Analysis:
#   Reads the whole transcript and maps: anchor story, tangent ranges,
#   repetition groups, circular sections.
#
# Pass 2 — Per-segment Decisions:
#   Uses the Pass 1 map to make informed keep/drop calls with a richer
#   dropReason vocabulary: filler | repetition | tangent | circular |
#   superseded | false_start | ramble
#
# dropReason taxonomy:
#   filler      — um/uh/like/you know
#   repetition  — consecutive repeat of the same word/phrase
#   tangent     — speaker goes off-topic and never resolves it
#   circular    — speaker circles the same idea without progressing
#   superseded  — semantic duplicate; a better version of this exists elsewhere
#   false_start — incomplete sentence / thought abandoned mid-utterance
#   ramble      — unfocused padding that carries no message

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "coreStory": {
            "type": "string",
            "description": "The single most concrete, interesting story or argument in the transcript.",
        },
        "narrativeArc": {
            "type": "string",
            "description": "1-2 sentence description of the overall structure and what should survive the edit.",
        },
        "tangents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label":      {"type": "string"},
                    "segIndices": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["label", "segIndices"],
            },
            "description": "Sections that go off-topic and don't resolve.",
        },
        "repetitionGroups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic":            {"type": "string"},
                    "bestIndex":        {"type": "integer"},
                    "duplicateIndices": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["topic", "bestIndex", "duplicateIndices"],
            },
            "description": "Groups of segments that say the same thing. bestIndex is the one to KEEP.",
        },
        "circularSections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label":      {"type": "string"},
                    "segIndices": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["label", "segIndices"],
            },
            "description": "Sections where the speaker circles the same idea without progressing.",
        },
    },
    "required": ["coreStory", "narrativeArc", "tangents", "repetitionGroups", "circularSections"],
}

QUOTA_SCHEMA = {
    "type": "object",
    "properties": {
        "cuts": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Indices of kept segments to cut, ordered most to least dispensable",
        },
    },
    "required": ["cuts"],
}

NARRATIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index":      {"type": "integer"},
                    "keep":       {"type": "boolean"},
                    "dropReason": {
                        "type": "string",
                        "description": (
                            "filler | repetition | tangent | circular | "
                            "superseded | false_start | ramble — empty string if kept"
                        ),
                    },
                    "jobRisk": {"type": "string"},
                },
                "required": ["index", "keep", "dropReason", "jobRisk"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["segments", "summary"],
}


def _fmt_ts(sec: float) -> str:
    m = int(sec // 60)
    s = sec % 60
    return f"{m}:{s:04.1f}"


def _has_filler(text: str) -> bool:
    import re
    lower = text.lower()
    return any(
        re.search(r"\b" + f.replace(" ", r"\s+") + r"\b", lower)
        for f in FILLER_WORDS
    )


def _build_transcript_lines(segments: list[dict], buckets: list[dict]) -> list[str]:
    """Format segments as numbered lines with sensor signals for Gemini."""
    def _agg(start: float, end: float) -> dict:
        overlap = [b for b in buckets if b["t"] + 1.0 > start and b["t"] < end]
        if not overlap:
            return {"motion": 0.0, "rms": 0.0, "tag": None}
        return {
            "motion": max(b.get("motion_score", 0) for b in overlap),
            "rms":    sum(b.get("audio_rms", 0) for b in overlap) / len(overlap),
            "tag":    next((b["visual_tag"] for b in overlap if b.get("visual_tag")), None),
        }

    lines = []
    for i, seg in enumerate(segments):
        sig    = _agg(seg["start"], seg["end"])
        line   = f"[{i}] {_fmt_ts(seg['start'])}–{_fmt_ts(seg['end'])}: {seg['text']}"
        extras = [f"motion={sig['motion']:.2f}", f"rms={sig['rms']:.2f}"]
        if sig["tag"]:
            extras.append(f'vision: "{sig["tag"]}"')
        line += f" | {', '.join(extras)}"
        lines.append(line)
    return lines


def run_narrative_architect(
    api_key: str,
    whisper_result: dict,
    analyze_result: dict,
    cfg: dict,
) -> dict:
    """
    Two-pass Gemini pipeline with structural pre-cuts.

    Pass 1 identifies tangents, repetitions, and circular sections — these are
    committed as hard pre-cuts regardless of Pass 2.  Pass 2 then only decides
    on the remaining undecided segments, ensuring structural findings can't be
    overridden by a conservative quota-fill.
    """
    import traceback

    buckets   = analyze_result.get("buckets", [])
    cut_pct   = cfg.get("narrative", {}).get("cut_target_pct", 0.55)
    segments  = whisper_result.get("segments", [])
    lines     = _build_transcript_lines(segments, buckets)
    transcript_text = "\n".join(lines)
    n_segs    = len(segments)
    min_drops = max(1, int(cut_pct * n_segs))

    if not api_key and not _llm.is_local():
        print("  No GEMINI_API_KEY and no local LLM found — rule-based fallback", file=sys.stderr)
        return _rule_based_fallback(segments)

    # ── Pre-pass: deterministic consecutive-duplicate removal ────────────────
    # Catches "captioning / captioning / captioning pipeline" style stutters
    # before the LLM ever sees them. Two consecutive segments are near-identical
    # if their texts share >80% of words.
    analysis: dict = {}
    pre_cut: dict  = {}  # {seg_index: dropReason} — applied unconditionally

    def _word_overlap(a: str, b: str) -> float:
        wa, wb = set(a.lower().split()), set(b.lower().split())
        if not wa or not wb:
            return 0.0
        return len(wa & wb) / min(len(wa), len(wb))

    for i in range(len(segments) - 1):
        j = i + 1
        if j in pre_cut:
            continue
        t_i = segments[i]["text"].strip()
        t_j = segments[j]["text"].strip()
        # Very short segments (≤3 words) that are identical → keep first, drop rest
        if len(t_i.split()) <= 3 and t_i.lower() == t_j.lower():
            pre_cut[j] = "consecutive_duplicate"
        # Longer segments with high word overlap
        elif len(t_i.split()) > 3 and _word_overlap(t_i, t_j) > 0.8:
            pre_cut[j] = "consecutive_duplicate"

    if pre_cut:
        print(f"  Pre-pass: {len(pre_cut)} consecutive duplicate(s) pre-cut", file=sys.stderr)

    # ── Pass 1: Pattern-hunt analysis (free JSON for better reasoning) ───────

    try:
        analysis_prompt = f"""You are a ruthless YouTube video editor. Scan this raw vlog transcript for
specific problem patterns. Your job is to identify what MUST be cut — even if
it has some content — because it makes the video less watchable.

Output ONLY valid JSON with this shape (no preamble, no markdown):
{{
  "coreStory": "one sentence: the most concrete interesting story in this video",
  "narrativeArc": "1-2 sentences: what the tight cut should say start-to-finish",
  "tangents": [{{"label": "short name", "segIndices": [0, 1, 2]}}],
  "repetitionGroups": [{{"topic": "what's repeated", "bestIndex": 5, "duplicateIndices": [3, 4]}}],
  "circularSections": [{{"label": "short name", "segIndices": [10, 11, 12]}}]
}}

=== PATTERN 1: MEANDER-AND-BAIL (→ goes in "tangents") ===

A block where the speaker starts a topic, spends MULTIPLE segments hedging/qualifying
it, and then exits without landing the point. The exit is typically: "anyway",
"but whatever", "nevermind", "I don't know", or a hard pivot to a new topic.

CRITICAL: The speaker does NOT have to be completely off-topic. A meander-and-bail
is defined by its SHAPE, not its content — 3+ qualifying segments followed by an exit.
Even if the speaker has a real point, if they couldn't articulate it clearly and just
bailed, THAT BLOCK IS A CUT.

Pattern looks like:
  "I think [topic]... I mean... no but... well actually... I don't know... anyway [pivot]"
  ↑ include this ↑─────────────────────────────────────────────────↑ through this ↑

List EVERY segment in the meander-and-bail block — entry, middle, and exit.

=== PATTERN 2: EXACT PHRASE REPETITIONS (→ goes in "repetitionGroups") ===

Find any sentence or clause that appears TWICE within 5 segments of itself.
This includes: word-for-word repeats, near-identical restating, and the speaker
saying something, apparently forgetting, then saying it again.

Example: "[5] which is also insane." and "[6] which is also insane." → repetitionGroup,
         bestIndex=5 (or 6, whichever sounds more energetic), duplicateIndices=[the other].

Also catch opening back-and-forth in the first 15 segments:
  "[0] Once a week? [1] I'm not sure. [2] Once a week, once a day." → bestIndex=0,
  duplicateIndices=[1,2] — keep the first clear statement, cut the hedging.

=== PATTERN 3: CIRCULAR SECTIONS (→ goes in "circularSections") ===

The speaker returns to the same topic at different points in the video and each
time says roughly the same thing without adding new information.

---

Expected for a {n_segs}-segment personal vlog: 2–4 meander-bails, 2–5 phrase
repetitions, 0–2 circular sections. Finding ZERO of any category almost certainly
means you missed something. Re-read with fresh eyes if a category is empty.

Precision matters more than recall here. If you are unsure whether a segment
belongs in a tangent or circular block, DO NOT include it. Over-inclusion sweeps
away bridge segments that connect the tangent back to the main story, causing
incoherent jumps in the final edit.

Special rule for tangent blocks: the LAST segment of a meander-and-bail often
pivots back toward the main story. Only include it if it is ALSO part of the
meander — if it starts transitioning to new content, stop the block one segment
earlier.

Transcript ({n_segs} segments):
{transcript_text}"""

        # Use Pro for deeper reasoning on structural analysis; fallback to Flash.
        # In local mode: pass model=None so _detect_model auto-selects the loaded model,
        # and pass schema to force json_schema mode (thinking models put output in
        # reasoning_content when using text mode, leaving content empty).
        raw = None
        pass1_schema = ANALYSIS_SCHEMA if _llm.is_local() else None
        if _llm.is_local():
            candidates = [None]  # auto-detect from LM Studio
        else:
            candidates = ["gemini-2.5-pro", "gemini-2.5-flash"]
        for model_name in candidates:
            try:
                raw = _gemini_generate(api_key, analysis_prompt, schema=pass1_schema, model=model_name)
                label = model_name or os.environ.get("LLM_MODEL", "local")
                print(f"  Pass 1 using {label}", file=sys.stderr)
                break
            except Exception as me:
                print(f"  {model_name or 'local'} unavailable ({me}), trying next", file=sys.stderr)
        if raw is None:
            raise RuntimeError("All models failed for Pass 1")
        try:
            analysis = _parse_json(raw)
        except Exception as pe:
            print(f"  Pass 1 JSON parse FAILED: {pe}", file=sys.stderr)
            print(f"  Raw response (first 500 chars): {raw[:500]}", file=sys.stderr)
            raise

        # Log exactly what was found so failures are diagnosable
        for t in analysis.get("tangents", []):
            print(f"  TANGENT '{t.get('label')}': {t.get('segIndices')}", file=sys.stderr)
        for r in analysis.get("repetitionGroups", []):
            print(f"  REPETITION '{r.get('topic')}': keep={r.get('bestIndex')}, "
                  f"drop={r.get('duplicateIndices')}", file=sys.stderr)
        for c in analysis.get("circularSections", []):
            print(f"  CIRCULAR '{c.get('label')}': {c.get('segIndices')}", file=sys.stderr)

        # Commit structural findings as hard pre-cuts
        for t in analysis.get("tangents", []):
            for idx in t.get("segIndices", []):
                if 0 <= idx < n_segs:
                    pre_cut[idx] = "tangent"

        for c in analysis.get("circularSections", []):
            for idx in c.get("segIndices", []):
                if 0 <= idx < n_segs:
                    pre_cut.setdefault(idx, "circular")

        for r in analysis.get("repetitionGroups", []):
            best = r.get("bestIndex", -1)
            for idx in r.get("duplicateIndices", []):
                if 0 <= idx < n_segs and idx != best:
                    pre_cut.setdefault(idx, "superseded")

        print(
            f"  Pass 1 OK — {len(analysis.get('tangents',[]))} tangents, "
            f"{len(analysis.get('repetitionGroups',[]))} rep groups, "
            f"{len(analysis.get('circularSections',[]))} circular "
            f"→ {len(pre_cut)} segments pre-cut",
            file=sys.stderr,
        )

    except Exception as e:
        print(f"  Pass 1 FAILED [{type(e).__name__}]: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

    # ── Pass 2: Fine-grained decisions on the remaining segments ──────────────
    undecided     = [i for i in range(n_segs) if i not in pre_cut]
    remaining_min = max(0, min_drops - len(pre_cut))

    context_block = ""
    if analysis:
        tangent_lines = "\n".join(
            f"  TANGENT '{t['label']}': segs {t['segIndices']} — already cut"
            for t in analysis.get("tangents", [])
        ) or "  none"
        rep_lines = "\n".join(
            f"  REPETITION '{r['topic']}': kept [{r['bestIndex']}], dropped {r['duplicateIndices']} — already cut"
            for r in analysis.get("repetitionGroups", [])
        ) or "  none"
        circ_lines = "\n".join(
            f"  CIRCULAR '{c['label']}': segs {c['segIndices']} — already cut"
            for c in analysis.get("circularSections", [])
        ) or "  none"
        context_block = f"""Core story: {analysis.get('coreStory', '')}

Already pre-cut (do NOT include these in your response):
{tangent_lines}
{rep_lines}
{circ_lines}
"""

    # Build join-context: for each undecided segment, show what the nearest kept
    # neighbours look like so Gemini can judge whether cutting it creates a bad join.
    # "Nearest kept neighbour" = closest undecided seg that the structural pass didn't cut.
    pre_kept  = set(undecided)  # at this point all undecided may be kept — refined below
    join_lines: list[str] = []
    for idx in undecided:
        # Nearest non-pre-cut seg before / after this one
        prev_j = next((j for j in range(idx - 1, -1, -1) if j not in pre_cut), None)
        next_j = next((j for j in range(idx + 1, n_segs) if j not in pre_cut), None)
        prev_words = segments[prev_j]["text"].split()[-10:] if prev_j is not None else []
        next_words = segments[next_j]["text"].split()[:10]  if next_j is not None else []
        join_preview = (
            ("..." + " ".join(prev_words) if prev_words else "(start)")
            + "  ‹CUT›  "
            + (" ".join(next_words) + "..." if next_words else "(end)")
        )
        join_lines.append(
            f"[{idx}] {lines[idx]}\n"
            f"      JOIN IF CUT: \"{join_preview}\""
        )
    join_text = "\n\n".join(join_lines)

    # Local models (Gemma etc.) consistently under-cut with the default KEEP bias.
    # The JOIN IF CUT preview already provides coherence protection, so for local mode
    # we flip to a CUT bias and rely on the downstream coherence repair pass as backstop.
    if _llm.is_local():
        quota_guidance = (
            f"Target: drop at least {remaining_min} of these {len(undecided)} remaining segments. "
            "Bias toward cutting. "
            "The JOIN IF CUT preview is your coherence guard — if the joined sentence reads naturally, CUT. "
            "The downstream coherence repair pass auto-restores any grammatically broken joins."
        )
    else:
        quota_guidance = (
            f"Target: drop roughly {remaining_min} of these {len(undecided)} remaining segments. "
            "When in doubt, KEEP — a kept segment can always be removed later; "
            "a cut that breaks coherence is hard to undo."
        )

    decision_prompt = f"""You are a video editor balancing conciseness with coherence.
Structural tangents and repetitions have already been cut. Now decide the {len(undecided)} REMAINING segments.

{context_block}
{quota_guidance}

COHERENCE IS THE HIGHEST PRIORITY. Each segment below shows a "JOIN IF CUT" preview —
the last 10 words of the previous kept segment followed by the first 10 words of the next
kept segment, with ‹CUT› marking where this segment would be removed. Read that preview
literally: does the joined sentence make grammatical sense? Does the topic flow naturally?
If the answer is NO — broken sentence, missing object, abrupt topic switch — mark it KEEP
even if the segment itself is weak. A bad join is worse than a slightly redundant segment.

Cut only for these clear reasons:
1. Exact or near-exact repetition of something in an earlier kept segment → "repetition"
2. Sentence trails off / thought never lands → "false_start"
3. Pure filler with zero informational content (um/uh/you-know alone) → "filler"
4. Adds no new information AND the prev→next join is clean without it → "ramble"

For EVERY segment, set jobRisk to a warning phrase if it contains negative comments about
the speaker's employer, job, manager, colleagues, or the finance industry. Empty string if safe.

Write a 2-sentence summary of the FULL video after all cuts are applied.

Segments to decide — each shown with its nearest kept neighbours for join-quality assessment:
{join_text}"""

    try:
        # Local models (small context / weaker reasoning) process in batches
        # of 15 segments so the task stays within the model's capability.
        BATCH = 15 if _llm.is_local() else len(undecided)
        p2: dict[int, dict] = {}
        summary_text = ""

        for batch_start in range(0, len(undecided), BATCH):
            batch = undecided[batch_start: batch_start + BATCH]
            batch_join = "\n\n".join(join_lines[batch_start: batch_start + BATCH])
            batch_remaining = max(0, remaining_min - len(p2) - sum(1 for k, v in p2.items() if not v.get("keep")))

            batch_prompt = decision_prompt.replace(
                f"Now decide the {len(undecided)} REMAINING segments.",
                f"Now decide the {len(batch)} segments in this batch ({batch_start+1}–{batch_start+len(batch)} of {len(undecided)})."
            ).replace(join_text, batch_join)
            # Adjust target for this batch (text differs between local and non-local prompts)
            if _llm.is_local():
                batch_prompt = batch_prompt.replace(
                    f"drop at least {remaining_min} of these {len(undecided)} remaining segments",
                    f"drop at least {batch_remaining} of these {len(batch)} segments in this batch",
                )
            else:
                batch_prompt = batch_prompt.replace(
                    f"drop roughly {remaining_min} of these {len(undecided)} remaining segments",
                    f"drop roughly {batch_remaining} of these {len(batch)} segments in this batch",
                )

            batch_schema = dict(NARRATIVE_SCHEMA)
            if batch_start + BATCH < len(undecided):
                # Intermediate batch: summary not needed yet
                batch_schema = {k: v for k, v in NARRATIVE_SCHEMA.items() if k != "required"}
                batch_schema["required"] = ["segments"]

            batch_text = _gemini_generate(api_key, batch_prompt, schema=batch_schema)
            batch_result = _parse_json(batch_text)

            for s in batch_result.get("segments", []):
                p2[s["index"]] = s
            if batch_result.get("summary"):
                summary_text = batch_result["summary"]

        result = {"segments": list(p2.values()), "summary": summary_text}

        # Merge: pre-cuts take priority, then Pass 2, then default keep
        final_segs = []
        for i in range(n_segs):
            if i in pre_cut:
                final_segs.append({"index": i, "keep": False, "dropReason": pre_cut[i], "jobRisk": ""})
            elif i in p2:
                final_segs.append(p2[i])
            else:
                final_segs.append({"index": i, "keep": True, "dropReason": "", "jobRisk": ""})

        total_drops = sum(1 for s in final_segs if not s["keep"])
        print(
            f"  Pass 2 OK — {total_drops}/{n_segs} total dropped ({total_drops/max(n_segs,1)*100:.0f}%)",
            file=sys.stderr,
        )

        # ── Quota enforcement: prune weakest kept segs if cut rate still lags target
        # Runs BEFORE opener protection so the LLM never sees the restored opener as a cut target.
        QUOTA_GAP_THRESHOLD = 0.10  # trigger when actual cut% is > 10pp below target
        actual_cut_pct = total_drops / max(n_segs, 1)
        gap = cut_pct - actual_cut_pct
        if gap >= QUOTA_GAP_THRESHOLD:
            additional = max(1, int(gap * n_segs))
            # Guard: never cut more than 15% of what's still kept (small nudge, not a hammer)
            additional = min(additional, max(1, int(len([s for s in final_segs if s["keep"]]) * 0.15)))
            print(
                f"  Quota gap {gap:.0%} (target {cut_pct:.0%}, actual {actual_cut_pct:.0%})"
                f" — enforcing {additional} more cut(s)",
                file=sys.stderr,
            )
            final_segs = _run_quota_enforcement(api_key, final_segs, lines, additional)

        # ── Opener protection + dangling-opener repair ───────────────────────────
        # Runs AFTER quota enforcement — it's the final authority on the first segment.
        final_segs = _protect_opener(final_segs, n_segs, segments)

        result["segments"]         = final_segs
        result["narrativeAnalysis"] = analysis
        return result

    except Exception as e:
        print(f"  Pass 2 FAILED [{type(e).__name__}]: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

        # Apply pre-cuts on top of the fallback even if Pass 2 failed
        fallback = _rule_based_fallback(segments)
        for seg in fallback["segments"]:
            if seg["index"] in pre_cut:
                seg["keep"] = False
                seg["dropReason"] = pre_cut[seg["index"]]
        fallback["narrativeAnalysis"] = analysis
        return fallback


def _rule_based_fallback(segments: list[dict]) -> dict:
    """Fallback when Gemini is unavailable — drops obvious junk heuristically."""
    import re
    ABANDON = {"anyway", "i don't know", "i'm not sure", "so yeah", "nevermind"}

    fallback_segs = []
    for i, s in enumerate(segments):
        text  = s["text"].strip()
        words = text.split()
        lower = text.lower()

        filler_hit  = _has_filler(text)
        is_fragment = len(words) < 4
        is_filler_segment = filler_hit and len(words) < 8
        is_abandon  = any(ph in lower for ph in ABANDON) and len(words) < 10

        drop   = is_filler_segment or is_fragment or is_abandon
        reason = "filler" if is_filler_segment else ("false_start" if is_fragment else ("ramble" if is_abandon else ""))

        fallback_segs.append({"index": i, "keep": not drop, "dropReason": reason, "jobRisk": ""})

    dropped = sum(1 for s in fallback_segs if not s["keep"])
    return {
        "segments": fallback_segs,
        "summary": f"Rule-based cut: {dropped}/{len(segments)} dropped. No Gemini API key — review and toggle manually.",
        "narrativeAnalysis": {},
    }


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
_OPENER_RESTORABLE_REASONS = {"ramble", "false_start", "filler", "consecutive_duplicate", ""}
# For segment 0 specifically, also restore "repetition"/"superseded".
_OPENER_RESTORABLE_REASONS_SEG0 = _OPENER_RESTORABLE_REASONS | {"repetition", "superseded"}


def _protect_opener(final_segs: list[dict], n_segs: int, whisper_segs: list[dict]) -> list[dict]:
    """
    Four deterministic coherence passes applied after Pass 2.

    1. Opener protection — restore segment [0] if dropped for a soft reason.
    2. Dangling-opener repair — restore one bridge before the first kept segment
       when it starts with a subordinating connector.
    3. Mid-video dangling join repair — same bridge logic for every subsequent
       transition where a kept segment starts with a connector word.
    4. Fragment cleanup — drop any kept segment that is ≤ 2 words (stutter/
       duplicate that slipped through), and drop the final kept segment if it
       is < 6 words and appears to be a trailing fragment.
    5. Adjacent restart dedup — when two consecutive kept segments overlap ≥ 30%
       in vocabulary AND the second starts with a restart marker, drop the
       incomplete first segment (the speaker restarted mid-thought).

    `whisper_segs` provides the source text; final_segs has no text at this stage.
    """
    if not final_segs:
        return final_segs

    def _seg_words(idx: int) -> list[str]:
        text = whisper_segs[idx]["text"] if idx < len(whisper_segs) else ""
        return text.strip().split()

    # ── 1. Opener protection ─────────────────────────────────────────────────
    seg0 = final_segs[0]
    if not seg0.get("keep") and seg0.get("dropReason", "") in _OPENER_RESTORABLE_REASONS_SEG0:
        if len(_seg_words(0)) >= 5:
            final_segs[0]["keep"] = True
            final_segs[0]["dropReason"] = ""
            print(f"  Opener protection: restored segment [0] (was: {seg0.get('dropReason','?')})", file=sys.stderr)

    # ── 2. Dangling-opener repair (first kept segment only) ──────────────────
    first_kept_idx = next((i for i, s in enumerate(final_segs) if s.get("keep")), None)
    if first_kept_idx is not None and first_kept_idx > 0:
        first_line_words = _seg_words(first_kept_idx)
        first_word = first_line_words[0].rstrip(".,!?;").lower() if first_line_words else ""
        if first_word in _DANGLING_OPENERS:
            for j in range(first_kept_idx - 1, -1, -1):
                candidate = final_segs[j]
                if (not candidate.get("keep")
                        and candidate.get("dropReason", "") in _OPENER_RESTORABLE_REASONS
                        and len(_seg_words(j)) >= 4):
                    final_segs[j]["keep"] = True
                    final_segs[j]["dropReason"] = ""
                    print(
                        f"  Dangling opener repair: restored [{j}] before "
                        f"first-kept [{first_kept_idx}] (starts '{first_word}')",
                        file=sys.stderr,
                    )
                    break

    # ── 3. Mid-video dangling join repair ────────────────────────────────────
    # For every kept segment (not just the first) that starts with a connector
    # word and is preceded by dropped segments, restore the nearest dropped
    # segment with a soft reason to provide the antecedent.
    for i in range(1, len(final_segs)):
        if not final_segs[i].get("keep"):
            continue
        if final_segs[i - 1].get("keep"):
            continue  # no gap — no repair needed
        curr_words = _seg_words(i)
        if not curr_words:
            continue
        fw = curr_words[0].rstrip(".,!?;").lower()
        if fw not in _DANGLING_OPENERS:
            continue
        for j in range(i - 1, -1, -1):
            cand = final_segs[j]
            if cand.get("keep"):
                break
            if (cand.get("dropReason", "") in _OPENER_RESTORABLE_REASONS
                    and len(_seg_words(j)) >= 4):
                cand["keep"] = True
                cand["dropReason"] = ""
                print(
                    f"  Mid-video dangling repair: restored [{j}] before [{i}] (starts '{fw}')",
                    file=sys.stderr,
                )
                break

    # ── 4. Fragment cleanup ──────────────────────────────────────────────────
    # Drop kept segments that are very short (stutter fragments or lone words).
    n_kept = sum(1 for s in final_segs if s.get("keep"))
    for i, s in enumerate(final_segs):
        if not s.get("keep"):
            continue
        words = _seg_words(i)
        if len(words) <= 2 and n_kept > 1:
            s["keep"] = False
            s["dropReason"] = "fragment"
            n_kept -= 1
            print(f"  Fragment cleanup: dropped [{i}] {words!r}", file=sys.stderr)

    # Drop the trailing kept segment if it is a short fragment (< 6 words).
    kept_indices = [i for i, s in enumerate(final_segs) if s.get("keep")]
    if kept_indices:
        last_i = kept_indices[-1]
        last_words = _seg_words(last_i)
        if len(last_words) < 6 and len(kept_indices) > 1:
            final_segs[last_i]["keep"] = False
            final_segs[last_i]["dropReason"] = "fragment"
            print(f"  Trailing fragment cleanup: dropped [{last_i}] {last_words!r}", file=sys.stderr)

    # ── 5. Adjacent restart dedup ────────────────────────────────────────────
    # When two consecutive kept segments share ≥ 30% vocabulary AND the second
    # starts with a restart marker, the speaker restarted mid-thought. Drop the
    # incomplete first segment.
    kept_positions = [i for i, s in enumerate(final_segs) if s.get("keep")]
    for pos in range(1, len(kept_positions)):
        prev_i = kept_positions[pos - 1]
        curr_i = kept_positions[pos]
        if curr_i != prev_i + 1:
            continue  # non-adjacent — skip
        prev_words = _seg_words(prev_i)
        curr_words = _seg_words(curr_i)
        if not prev_words or not curr_words:
            continue
        curr_text_lower = " ".join(curr_words).lower()
        is_restart = any(curr_text_lower.startswith(m) for m in _RESTART_MARKERS)
        if not is_restart:
            continue
        prev_set = {w.lower() for w in prev_words}
        curr_set = {w.lower() for w in curr_words}
        overlap = len(prev_set & curr_set) / max(len(prev_set | curr_set), 1)
        if overlap >= 0.30:
            last_prev_word = prev_words[-1].lower().rstrip(".,!?;")
            if last_prev_word in _INCOMPLETE_ENDERS:
                final_segs[prev_i]["keep"] = False
                final_segs[prev_i]["dropReason"] = "false_start"
                print(
                    f"  Restart dedup: dropped incomplete [{prev_i}], kept restart [{curr_i}]",
                    file=sys.stderr,
                )
            else:
                final_segs[curr_i]["keep"] = False
                final_segs[curr_i]["dropReason"] = "false_start"
                print(
                    f"  Restart dedup: dropped restart [{curr_i}], kept complete [{prev_i}]",
                    file=sys.stderr,
                )

    return final_segs


def _run_quota_enforcement(
    api_key: str,
    final_segs: list[dict],
    lines: list[str],
    n_cuts: int,
) -> list[dict]:
    """
    Targeted post-pass: given a list of kept segments, ask the LLM to identify
    the N weakest and cut them. Only called when actual cut rate lags target by
    ≥ QUOTA_GAP_THRESHOLD.
    """
    kept_indices = [i for i, s in enumerate(final_segs) if s.get("keep")]
    if not kept_indices or n_cuts <= 0:
        return final_segs

    kept_lines = "\n".join(f"[{i}] {lines[i]}" for i in kept_indices)

    prompt = f"""You are a video editor. This edit needs {n_cuts} more segment(s) removed to hit the conciseness target.

Below are the {len(kept_indices)} currently-kept segments. Return exactly the {n_cuts} index/indices that contribute the LEAST.

Good cut candidates: orphaned single-word fragments, rambling wind-up phrases, weak/generic sign-offs,
near-empty filler-only sentences, segments that repeat a point already made earlier.
Bad cut candidates: topic introductions, segments carrying unique information, key transitions.

Kept segments:
{kept_lines}"""

    try:
        raw = _llm.generate(prompt, schema=QUOTA_SCHEMA, api_key=api_key)
        result = _parse_json(raw)
        cuts = [i for i in result.get("cuts", []) if isinstance(i, int)][:n_cuts]

        actually_cut = 0
        for idx in cuts:
            if 0 <= idx < len(final_segs) and final_segs[idx].get("keep"):
                final_segs[idx]["keep"] = False
                final_segs[idx]["dropReason"] = "ramble"
                final_segs[idx]["decisionSource"] = "quota_enforcement"
                actually_cut += 1
        print(f"    → quota enforcement cut {actually_cut} more segment(s)", file=sys.stderr)
    except Exception as e:
        print(f"  Quota enforcement failed ({e}), skipping", file=sys.stderr)

    return final_segs


# Words that essentially never end a complete thought in spontaneous speech.
# If a kept segment's last word is in this set, the next dropped segment
# is almost certainly a grammatical continuation and should be restored.
_CONTINUATION_LAST_WORDS = {
    # articles
    "the", "a", "an",
    # prepositions
    "of", "in", "to", "from", "with", "by", "at", "for", "on", "into",
    "about", "through", "between", "during", "after", "before",
    # conjunctions / relative pronouns used mid-sentence
    "and", "but", "or", "so", "that", "which", "who", "whose", "where",
    # auxiliaries / modals without main verb
    "gonna", "going", "be", "been", "being",
    "is", "are", "was", "were", "have", "has", "had",
    "will", "would", "can", "could", "should", "may", "might",
    "do", "does", "did", "don't", "doesn't", "didn't", "won't",
    # negation (clearly incomplete)
    "not", "never",
    # intensifiers that always precede a word
    "very", "really", "so", "just", "also", "both",
}


def _repair_broken_joins(segments: list[dict]) -> list[dict]:
    """
    Deterministic post-pass: restore dropped segments that sit between two kept
    segments where the preceding kept segment ends on a clear continuation word.
    Runs after all LLM decisions so it never fights the narrative architect —
    it only repairs joins that are grammatically broken.
    """
    repaired = 0
    n = len(segments)

    for i in range(n):
        if not segments[i].get("keep"):
            continue
        last_word = segments[i]["text"].strip().split()[-1].lower().rstrip(".,!?") if segments[i]["text"].strip() else ""
        if last_word not in _CONTINUATION_LAST_WORDS:
            continue
        # Segment i ends on a continuation word — restore the very next dropped seg
        j = i + 1
        if j < n and not segments[j].get("keep") and segments[j].get("dropReason") != "manual":
            segments[j]["keep"] = True
            segments[j]["dropReason"] = ""
            segments[j]["decisionSource"] = "coherence_repair"
            repaired += 1

    if repaired:
        print(f"  Coherence repair: restored {repaired} segment(s) after continuation-word joins", file=sys.stderr)

    return segments


# ── Step 4–7: Surgeon → Continuity → Rhythmist → Linter ─────────────────────

def _aggregate_buckets(buckets: list[dict], start: float, end: float) -> dict:
    overlap = [b for b in buckets if b["t"] + 1.0 > start and b["t"] < end]
    if not overlap:
        return {"motionScore": 0, "audioRms": 0, "energyScore": 0, "visualTag": None}
    motion = max(b.get("motion_score", 0) for b in overlap)
    rms    = sum(b.get("audio_rms", 0) for b in overlap) / len(overlap)
    pitch  = max(b.get("pitch_delta", 0) for b in overlap)
    energy = min(0.5 * motion + 0.4 * rms + 0.1 * pitch, 1.0)
    tag    = next((b["visual_tag"] for b in overlap if b.get("visual_tag")), None)
    return {"motionScore": motion, "audioRms": rms, "energyScore": energy, "visualTag": tag}


def build_segments(
    whisper_result: dict,
    narrative_result: dict,
    analyze_result: dict,
) -> list[dict]:
    """Merge Whisper + Narrative Architect + sensor signals into Segment list."""
    buckets   = analyze_result.get("buckets", [])
    decisions = {d["index"]: d for d in narrative_result.get("segments", [])}
    out = []
    for i, seg in enumerate(whisper_result.get("segments", [])):
        dec    = decisions.get(i, {})
        keep   = dec.get("keep", True)
        reason = dec.get("dropReason", "")
        risk   = dec.get("jobRisk", "")
        sigs   = _aggregate_buckets(buckets, seg["start"], seg["end"])
        out.append({
            "start":       _fmt_ts(seg["start"]),
            "end":         _fmt_ts(seg["end"]),
            "startSec":    seg["start"],
            "endSec":      seg["end"],
            "text":        seg["text"],
            "words":       seg.get("words", []),
            "keep":        keep,
            "decisionSource": "pipeline",
            "dropReason":  reason,
            "jobRisk":     risk,
            "wordCuts":    [],
            "motionScore": round(sigs["motionScore"], 4),
            "audioRms":    round(sigs["audioRms"], 4),
            "energyScore": round(sigs["energyScore"], 4),
            "visualTag":   sigs["visualTag"],
        })
    return out


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        sys.stdout.write(json.dumps({"error": "Usage: orchestrator.py <video_path> [whisper_model]"}) + "\n")
        sys.exit(1)

    video_path    = sys.argv[1]
    no_vision     = "--no-vision" in sys.argv
    no_segment    = "--no-segment" in sys.argv
    save_fixture  = "--save-fixture" in sys.argv
    # Positional args are everything that isn't a flag
    positional = [a for a in sys.argv[2:] if not a.startswith("--")]
    whisper_model      = positional[0] if len(positional) > 0 else "mlx-community/whisper-large-v3-turbo"
    filler_sensitivity = positional[1] if len(positional) > 1 else "balanced"
    api_key       = os.environ.get("GEMINI_API_KEY", "")

    # Redirect stdout → stderr during noisy library init
    real_stdout = sys.stdout
    sys.stdout  = sys.stderr

    try:
        # ── 1. Sensor Array ────────────────────────────────────────────────────
        print("▶ Sensor Array: running analyze + transcribe in parallel...", file=sys.stderr)
        try:
            analyze_result, whisper_result = run_sensor_array(video_path, whisper_model, no_vision=no_vision)
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

        # ── 1b. Semantic Segmenter ─────────────────────────────────────────────
        if not no_segment and (api_key or _llm.is_local()):
            print("▶ Semantic Segmenter: merging Whisper fragments into thought units...", file=sys.stderr)
            try:
                from segment import segment as _run_segmenter
                orig_n = len(whisper_result["segments"])
                whisper_result["segments"] = _run_segmenter(whisper_result["segments"], api_key)
                new_n = len(whisper_result["segments"])
                print(f"  {orig_n} Whisper → {new_n} semantic segments", file=sys.stderr)
            except Exception as e:
                print(f"  Segmenter failed ({e}), using raw Whisper segments", file=sys.stderr)

        # ── 2. Director ────────────────────────────────────────────────────────
        print("▶ Director: configuring pipeline...", file=sys.stderr)
        if api_key or _llm.is_local():
            director_cfg = run_director(api_key, analyze_result, whisper_result, duration)
        else:
            director_cfg = dict(_DIRECTOR_DEFAULTS)
            print("  (no API key — using defaults)", file=sys.stderr)

        print(f"  content_type={director_cfg['content_type']}, "
              f"surgeon.aggressiveness={director_cfg['surgeon']['aggressiveness']:.2f}, "
              f"cut_target={director_cfg['narrative']['cut_target_pct']:.0%}", file=sys.stderr)

        # ── 3. Narrative Architect ─────────────────────────────────────────────
        print("▶ Narrative Architect: keep/drop decisions...", file=sys.stderr)
        narrative_result = run_narrative_architect(api_key, whisper_result, analyze_result, director_cfg)

        segments = build_segments(whisper_result, narrative_result, analyze_result)
        segments = _repair_broken_joins(segments)
        segments = apply_filler_cuts(segments, filler_sensitivity, preserve_manual=True)

        # ── 4. Heuristic Surgeon ───────────────────────────────────────────────
        print("▶ Heuristic Surgeon: zero-crossing word cuts + silence removal...", file=sys.stderr)
        try:
            audio, sr = load_audio_from_video(video_path)
            surg_cfg  = director_cfg.get("surgeon", {})
            segments  = refine_word_cuts(
                audio, sr, segments,
                add_silence_cuts=surg_cfg.get("add_silence_cuts", True),
                aggressiveness=surg_cfg.get("aggressiveness", 0.65),
            )
        except Exception as e:
            print(f"  Surgeon failed ({e}), skipping zero-crossing snap", file=sys.stderr)

        # ── 5. Continuity Guard ────────────────────────────────────────────────
        print("▶ Continuity Guard: repairing abrupt transcript joins...", file=sys.stderr)
        segments, continuity_issues = apply_continuity_guard(segments)
        if continuity_issues:
            print(f"  restored/adjusted {len(continuity_issues)} continuity issue(s)", file=sys.stderr)

        # ── 6. The Rhythmist ───────────────────────────────────────────────────
        print("▶ The Rhythmist: J/L-cuts, ducking, zoom hints...", file=sys.stderr)
        rhy_cfg  = director_cfg.get("rhythmist", {})
        segments = apply_rhythm(
            segments, buckets,
            j_cut_threshold=rhy_cfg.get("j_cut_threshold", 0.30),
            l_cut_threshold=rhy_cfg.get("l_cut_threshold", 0.30),
            ducking_enabled=rhy_cfg.get("ducking_enabled", True),
        )

        # ── 7. Integrity Linter ────────────────────────────────────────────────
        print("▶ Integrity Linter: QA checks...", file=sys.stderr)
        lint_result = lint(segments, duration)
        segments    = lint_result["segments"]
        if not lint_result["passed"]:
            errors = [i for i in lint_result["issues"] if i["severity"] == "error"]
            print(f"  Linter: {len(errors)} error(s) found", file=sys.stderr)

        # ── Build output ───────────────────────────────────────────────────────
        total_sec  = whisper_result["segments"][-1]["end"] if whisper_result["segments"] else 0
        kept_sec   = sum(s["endSec"] - s["startSec"] for s in segments if s.get("keep"))
        summary    = narrative_result.get("summary", "")

        output = {
            "segments":         segments,
            "summary":          summary,
            "totalDuration":    _fmt_ts(total_sec),
            "editedDuration":   _fmt_ts(kept_sec),
            "language":         whisper_result.get("language", "en"),
            "directorConfig":   director_cfg,
            "settings":         {"fillerSensitivity": filler_sensitivity},
            "narrativeAnalysis": narrative_result.get("narrativeAnalysis", {}),
            "linterPassed":     lint_result["passed"],
            "linterIssues":     continuity_issues + lint_result["issues"],
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
