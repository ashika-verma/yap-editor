#!/usr/bin/env python3
"""
Eval runner for thumbnail visual quality.

Pipeline:
  1. Generate concepts from fixture (or load cached state)
  2. Render each concept as a JPEG using compose() — no face needed
  3. Send all rendered images to Gemini vision for scoring
  4. Save rendered JPEGs + JSON results so you can actually see them

Usage:
  eval_thumbnails.py                                    # all fixtures
  eval_thumbnails.py fixtures/foo.json                  # one fixture
  eval_thumbnails.py fixtures/foo.json --state tmp/s.json  # reuse cached concepts
  eval_thumbnails.py --model gemini-2.5-pro             # stronger judge

Visual metrics (1–5 each):
  visual_impact    — Does it stop the scroll? Color, composition, boldness.
  text_readability — Is the hook text large enough and legible?
  ctr_potential    — Would you click this?

Set-level:
  diversity_score  — Are the 6 designs varied in color, hook, and feel?
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import generate, generate_vision  # noqa: E402
from thumbnail import compose, BG_PRESETS, _top_keywords, _extract_transcript, _fix_concepts  # noqa: E402

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
RESULTS_DIR  = Path(__file__).parent.parent / "eval_results" / "thumbnails"

# ── LLM schemas ──────────────────────────────────────────────────────────────

CONCEPTS_SCHEMA: dict = {
    "type": "object",
    "required": ["concepts"],
    "properties": {
        "concepts": {
            "type": "array",
            "minItems": 6,
            "maxItems": 6,
            "items": {
                "type": "object",
                "required": [
                    "title", "bg_preset", "headline_font", "face_side",
                    "headline_lines", "subtext", "caveat_aside",
                    "badge", "face_timestamp",
                ],
                "properties": {
                    "title":         {"type": "string"},
                    "bg_preset":     {"type": "string", "enum": list(BG_PRESETS)},
                    "headline_font": {"type": "string", "enum": ["anton", "fredoka"]},
                    "face_side":     {"type": "string", "enum": ["left", "center", "right"]},
                    "headline_lines": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 3,
                    },
                    "subtext":       {"type": "string"},
                    "caveat_aside":  {"type": ["string", "null"]},
                    "badge": {
                        "oneOf": [
                            {"type": "null"},
                            {
                                "type": "object",
                                "required": ["text", "position"],
                                "properties": {
                                    "text":     {"type": "string"},
                                    "position": {
                                        "type": "string",
                                        "enum": ["top-left", "top-right", "bottom-left", "bottom-right"],
                                    },
                                },
                            },
                        ]
                    },
                    "face_side":     {"type": "string", "enum": ["left", "center", "right"]},
                    "face_timestamp": {"type": "number"},
                },
            },
        }
    },
}

SINGLE_CONCEPT_SCHEMA: dict = {
    "type": "object",
    "required": ["headline_lines", "subtext", "caveat_aside", "badge"],
    "properties": {
        "headline_lines": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 3,
        },
        "subtext":      {"type": "string"},
        "caveat_aside": {"type": ["string", "null"]},
        "badge": {
            "oneOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "required": ["text", "position"],
                    "properties": {
                        "text":     {"type": "string"},
                        "position": {
                            "type": "string",
                            "enum": ["top-left", "top-right", "bottom-left", "bottom-right"],
                        },
                    },
                },
            ]
        },
    },
}

VISUAL_JUDGE_SCHEMA: dict = {
    "type": "object",
    "required": ["thumbnails", "diversity_score", "best_index", "summary"],
    "properties": {
        "thumbnails": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["visual_impact", "text_readability", "ctr_potential", "notes"],
                "properties": {
                    "visual_impact":    {"type": "integer", "minimum": 1, "maximum": 5},
                    "text_readability": {"type": "integer", "minimum": 1, "maximum": 5},
                    "ctr_potential":    {"type": "integer", "minimum": 1, "maximum": 5},
                    "notes":            {"type": "string"},
                },
            },
        },
        "diversity_score": {"type": "integer", "minimum": 1, "maximum": 5},
        "best_index":      {"type": "integer", "minimum": 0, "maximum": 5},
        "summary":         {"type": "string"},
    },
}

# ── Helpers ───────────────────────────────────────────────────────────────────

HISTORY_FILE = RESULTS_DIR / "scores_history.jsonl"


def _bar(n: float, out_of: float = 5.0, width: int = 10) -> str:
    filled = round(n / out_of * width)
    return "█" * filled + "░" * (width - filled)


def _avg(vals: list[int | float]) -> float:
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def _append_history(entry: dict) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with HISTORY_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _load_last_history(fixture: str) -> dict | None:
    """Return the most recent history entry for this fixture, or None."""
    if not HISTORY_FILE.exists():
        return None
    last = None
    for line in HISTORY_FILE.read_text().splitlines():
        try:
            entry = json.loads(line)
            if entry.get("fixture") == fixture:
                last = entry
        except Exception:
            pass
    return last


def _print_delta(label: str, current: float, previous: float | None) -> str:
    if previous is None:
        return ""
    delta = round(current - previous, 2)
    if delta > 0:
        return f"  (+{delta} vs last)"
    if delta < 0:
        return f"  ({delta} vs last)"
    return "  (no change)"


# ── Core functions ────────────────────────────────────────────────────────────

def generate_concepts(summary: str, transcript: str, duration: float, count: int = 6) -> list[dict]:
    keywords    = _top_keywords(transcript)
    presets_list = ", ".join(BG_PRESETS.keys())
    prompt = f"""You are designing YouTube thumbnails for a tech creator (woman in tech / vlog). Generate exactly {count} concepts.

═══ CONTENT GROUNDING ═══
Read the video content below. Identify the 3 most SURPRISING or DRAMATIC facts (tools, outcomes, contrasts).
Every headline must be grounded in one of them — no generic words.

VIDEO SUMMARY: {summary}
TOP KEYWORDS: {keywords}
TRANSCRIPT: {transcript[:1500]}
═════════════════════════

HOOK WRITING RULES — THIS IS THE MOST IMPORTANT PART:
Each headline_lines entry should read like a complete punchy thought, not isolated words.
Think: what would make someone STOP scrolling? Use dramatic contrast, surprising outcomes, or relatable pain.

THE #1 HOOK FORMULA (contrast/comparison — use for at least 3 of the 6 concepts):
  "[X] took [time]" / "[AI/tool] took [less time]"
  "I used to [pain]" / "now [AI does it]"
  "[Old way]" / "[New way]"
  Examples: "CapCut took hours / AI took seconds", "I edited manually / now agents do it", "filler words everywhere / AI cuts them all"

HOOK VARIETY (use different structures across the 6 concepts — no two should feel the same):
  - Contrast hook: "[before] / [after]" or "[problem] / [solution]"
  - Curiosity hook: "why my vlog edits itself" or "I stopped editing"
  - Claim hook: "I built my own AI editor" or "8 agents edit my vlogs"
  - Question format is allowed: "what if your vlog edited itself?"

BAD hooks: isolated words or weak filler — "EDITING", "TECH", "AI", "VIDEO", "for my channel", "ever again"
BAD: trailing periods or exclamation marks.
BAD: anything not in the transcript.

STYLE:
- Mixed case is ENCOURAGED — "my vlog edits itself" beats "MY VLOG EDITS ITSELF"
- MAX 3 words per line — shorter lines = bigger font = more visual impact
- Strongly prefer 2 lines total
- Think tabloid headline, not tag cloud

DESIGN SYSTEM:
Fonts: "anton" (condensed, heavy — serious/dramatic) | "fredoka" (rounded — relatable/fun)
Colors — use all 6, one per concept:
  yellow (#FFD93D bg, black text), red (#C0392B bg, white), blue (#4ABDE8 bg, dark),
  pink (#E8538F bg, white), charcoal (#1A1A1A bg, yellow), tan (#C9B99A bg, dark)

Per concept fields:
- title: YouTube title ≤70 chars
- bg_preset: one of [{presets_list}]
- headline_font: "anton" or "fredoka"
- face_side: "left"|"center"|"right" — use pattern: right, left, right, left, center, right (center sparingly, max 1)
- headline_lines: 1–3 punchy lines (see HOOK WRITING RULES above)
- subtext: 1 sentence with a concrete detail (tool, number, outcome) not already in the headline
- caveat_aside: null OR funny lowercase aside ≤5 words (use for ≤2 concepts)
- badge: null OR {{"text":"LABEL","position":"top-right"}} — factual (e.g. "8 AGENTS", "NO CUTS", "RUNS LOCAL") — use for 3–4 concepts
- face_timestamp: seconds (5–{duration - 5:.0f})

Make each concept feel like a DIFFERENT angle on the same video."""

    raw      = generate(prompt, schema=CONCEPTS_SCHEMA)
    concepts = json.loads(raw)["concepts"][:count]
    return _fix_concepts(concepts, summary)


def render_concepts(
    concepts: list[dict],
    out_dir: Path,
    face_paths: list[str | None] | None = None,
) -> list[Path | None]:
    """Render each concept. face_paths[i] = path to face frame for concept i (or None)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path | None] = []
    for i, concept in enumerate(concepts):
        out_path = out_dir / f"thumb_{i}.jpg"
        fp = face_paths[i] if (face_paths and i < len(face_paths)) else None
        try:
            compose(
                face_path=fp,
                bg_preset=concept.get("bg_preset", "red"),
                headline_font=concept.get("headline_font", "anton"),
                headline_lines=concept.get("headline_lines", ["WATCH"]),
                subtext=concept.get("subtext", ""),
                caveat_aside=concept.get("caveat_aside"),
                badge=concept.get("badge"),
                face_side=concept.get("face_side", "right"),
                out_path=str(out_path),
                highlight_word=concept.get("highlight_word"),
            )
            paths.append(out_path)
        except Exception as e:
            print(f"  [render] concept {i} failed: {e}", file=sys.stderr)
            paths.append(None)
    return paths


def _resize_for_judge(img_bytes: bytes, w: int = 320, h: int = 180) -> bytes:
    """Downscale to YouTube grid size so the judge sees what viewers see."""
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(img_bytes)).resize((w, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def revise_weak_concepts(
    concepts: list[dict],
    scored: list[dict],
    summary: str,
    threshold: float = 4.0,
) -> tuple[list[dict], list[int]]:
    """
    For each concept whose avg score < threshold, ask the LLM to revise the hook
    based on the judge's notes. Returns (updated_concepts, list_of_revised_indices).
    bg_preset, headline_font, face_side are locked — only hook content changes.
    """
    revised_indices: list[int] = []
    updated = [c.copy() for c in concepts]

    for i, (concept, score) in enumerate(zip(concepts, scored)):
        avg = _avg([
            score.get("visual_impact", 0),
            score.get("text_readability", 0),
            score.get("ctr_potential", 0),
        ])
        if avg >= threshold:
            continue

        notes = score.get("notes", "")
        orig_hook = " / ".join(concept.get("headline_lines", []))
        print(f"    [{i}] '{orig_hook}'  avg={avg}  → revising…", end=" ", flush=True)

        prompt = f"""A YouTube thumbnail was designed with this hook and scored poorly. Revise ONLY the hook.

ORIGINAL:
  headline_lines: {concept.get("headline_lines")}
  bg_preset: {concept.get("bg_preset")}
  face_side: {concept.get("face_side")}
  badge: {concept.get("badge")}
  subtext: {concept.get("subtext")}

JUDGE FEEDBACK (scores: visual={score.get("visual_impact")}/5  readability={score.get("text_readability")}/5  ctr={score.get("ctr_potential")}/5):
  {notes}

VIDEO: {summary}

Fix the headline to directly address the judge's criticism.
Rules:
- MAX 3 words per line, prefer 2 lines total
- Use contrast/comparison: "[before] / [after]" or "[problem] / [solution]"
- Mixed case encouraged — NOT all caps
- No trailing punctuation
- No stopword-only lines: AI, VIDEO, VLOG, EDITING, TECH alone are forbidden
- Keep bg_preset, headline_font, face_side unchanged (those are not in your output)
- You may update badge and subtext to match the new hook

Return a JSON object with: headline_lines, subtext, caveat_aside, badge"""

        try:
            raw     = generate(prompt, schema=SINGLE_CONCEPT_SCHEMA)
            revised = json.loads(raw)
            updated[i] = {**concept, **revised}
            new_hook = " / ".join(revised.get("headline_lines", []))
            print(f"→ '{new_hook}'")
            revised_indices.append(i)
        except Exception as e:
            print(f"failed ({e})")

    return updated, revised_indices


def judge_rendered(
    rendered_paths: list[Path | None],
    concepts: list[dict],
    summary: str,
    reference_image: bytes | None = None,
    model: str | None = None,
) -> dict:
    """
    Score rendered thumbnails via vision LLM.
    Images are downscaled to 320×180 (YouTube grid size) before scoring so the
    judge evaluates at the scale viewers actually see.
    Returns {} on complete failure.
    """
    our_images: list[bytes] = []
    concept_labels: list[str] = []
    for i, path in enumerate(rendered_paths):
        if path is None or not path.exists():
            continue
        our_images.append(_resize_for_judge(path.read_bytes()))
        hook = " / ".join(concepts[i].get("headline_lines", []))
        concept_labels.append(
            f"Thumbnail {len(our_images) - 1}: "
            f"font={concepts[i].get('headline_font','')}  "
            f"preset={concepts[i].get('bg_preset','')}  "
            f"headline='{hook}'  face_side={concepts[i].get('face_side','')}"
        )

    if not our_images:
        return {}

    # Reference image is described in text — don't add to images list because
    # passing 7+ images to a local vision model (Gemma) causes partial scoring.
    n      = len(our_images)
    images = our_images

    ref_style = (
        "\nTarget style (from reference creator — science/psychology/woman-in-tech niche):\n"
        "- Person is a full-bleed cutout on one side of the frame\n"
        "- Headline text is MASSIVE, fills the entire opposite zone — each line is 30–50% of frame height\n"
        "- Hooks are complete punchy thoughts (e.g. 'Men fall in love twice as fast', 'ANOREXIA is the most lethal'), NOT isolated words\n"
        "- Bold solid bg, very high contrast between bg and text\n"
        "- Person's face NEVER obscures the key text words\n"
    ) if reference_image else ""

    prompt = (
        f"You are a YouTube CTR expert. Score ALL {n} thumbnail designs below for this video:\n"
        f"{summary}\n"
        f"{ref_style}\n"
        + "\n".join(concept_labels)
        + f"""

Images are in order: Thumbnail 0, 1, … {n-1}.
YOU MUST return exactly {n} scores in the 'thumbnails' array — one entry per image, in order.

HOW TO SCORE text_readability — be strict, check each of these:
  1. Can you read EVERY word of the headline without squinting? If any word is partly hidden behind the person's head, hair, or body → deduct points.
  2. Imagine the thumbnail displayed at 320×180px (YouTube grid size). Is the text still legible at that scale? Tiny text → score 1–2.
  3. Does the headline fill a substantial portion of its zone (at least 1/3 of frame height per line)? If the font looks small relative to the frame → score 2–3.
  4. Is there enough contrast between the text color and the background? Low contrast → deduct.
  SCORING: 5=every word crystal clear, fills zone, high contrast | 4=readable but one minor issue | 3=readable but noticeably small or slightly obscured | 2=hard to read (small, low-contrast, or key word covered) | 1=unreadable

Score each thumbnail (1–5):
- visual_impact: Does it stop the scroll? Bold color, striking composition, eye-catching?
- text_readability: Apply the strict rubric above — be honest if text is small or covered
- ctr_potential: Would this creator's audience click? (1=scroll past, 5=must click)
- notes: ONE concrete sentence — identify the specific readability failure OR the specific reason it works

Set:
- diversity_score (1–5): Variety of colors, layouts, hooks across all {n} designs
- best_index: 0-based index of the single highest-CTR thumbnail
- summary: 2–3 sentences — the #1 readability issue hurting the set and the exact fix"""
    )

    try:
        raw = generate_vision(prompt, images=images, schema=VISUAL_JUDGE_SCHEMA, model=model)
        return json.loads(raw)
    except Exception as e:
        print(f"\n  [judge] vision scoring failed: {e}", file=sys.stderr)
        return {}


# ── Load fixtures ─────────────────────────────────────────────────────────────

def load_fixtures(targets: list[str]) -> list[tuple[str, dict]]:
    paths = targets if targets else sorted(glob.glob(str(FIXTURES_DIR / "*.json")))
    out = []
    for p in paths:
        try:
            raw = json.load(open(p))
            plan = raw.get("plan", raw)
            out.append((p, plan))
        except Exception as e:
            print(f"  SKIP {p}: {e}", file=sys.stderr)
    return out


# ── Main eval loop ────────────────────────────────────────────────────────────

def run_evals(
    fixtures: list[tuple[str, dict]],
    judge_model: str | None,
    state_path: str | None,
    face_dir: str | None = None,
    reference_image_path: str | None = None,
    refine: bool = False,
    refine_threshold: float = 4.0,
) -> list[dict]:
    results = []

    for path, plan in fixtures:
        name       = Path(path).stem
        summary    = plan.get("summary", "(no summary)")
        segments   = plan.get("segments", [])
        duration   = max((s.get("endSec", 0) for s in segments), default=60.0)
        transcript = _extract_transcript(plan, max_chars=4000)

        print(f"\n{'─' * 60}")
        print(f"  Fixture: {name}")
        print(f"  Summary: {summary[:120]}{'...' if len(summary) > 120 else ''}")

        # ── Generate or load concepts ──────────────────────────────────────
        if state_path and Path(state_path).exists():
            state    = json.loads(Path(state_path).read_text())
            concepts = state["concepts"]
            print(f"  Concepts: loaded from {state_path} (skipping LLM gen)")
        else:
            print("  Generating concepts…", end=" ", flush=True)
            try:
                concepts = generate_concepts(summary, transcript, duration)
                print("done")
            except Exception as e:
                print(f"FAILED: {e}")
                continue

        # ── Resolve face frames ───────────────────────────────────────────
        face_paths: list[str | None] = []
        if face_dir:
            for i in range(len(concepts)):
                fp = Path(face_dir) / f"face_{i}.jpg"
                face_paths.append(str(fp) if fp.exists() else None)
            n_faces = sum(1 for f in face_paths if f)
            print(f"  Face frames: {n_faces}/{len(concepts)} found in {face_dir}")
        else:
            face_paths = [None] * len(concepts)

        # ── Reference image ───────────────────────────────────────────────
        ref_img: bytes | None = None
        if reference_image_path and Path(reference_image_path).exists():
            ref_img = Path(reference_image_path).read_bytes()
            print(f"  Reference image: {reference_image_path}")

        # ── Render ────────────────────────────────────────────────────────
        ts         = datetime.now().strftime("%Y%m%d_%H%M%S")
        render_dir = RESULTS_DIR / f"{name}_{ts}"
        print(f"  Rendering {len(concepts)} thumbnails…", end=" ", flush=True)
        rendered   = render_concepts(concepts, render_dir, face_paths=face_paths)
        n_rendered = sum(1 for p in rendered if p is not None)
        print(f"done ({n_rendered}/{len(concepts)})")

        # ── Visual judge ───────────────────────────────────────────────────
        print("  Judging rendered images…", end=" ", flush=True)
        try:
            judgment = judge_rendered(
                rendered, concepts, summary,
                reference_image=ref_img,
                model=judge_model,
            )
            print("done")
        except Exception as e:
            print(f"FAILED: {e}")
            judgment = {}

        scored      = judgment.get("thumbnails", [])
        diversity   = judgment.get("diversity_score", 0)
        best_idx    = judgment.get("best_index", 0)
        summary_txt = judgment.get("summary", "")

        # ── Print results ──────────────────────────────────────────────────
        print()
        for i, concept in enumerate(concepts):
            score     = scored[i] if i < len(scored) else {}
            mark      = " ★ BEST" if i == best_idx and scored else ""
            img_path  = rendered[i]
            rendered_str = str(img_path) if img_path else "render failed"
            avg_score = _avg([
                score.get("visual_impact", 0),
                score.get("text_readability", 0),
                score.get("ctr_potential", 0),
            ]) if score else 0.0

            print(f"  [{i}]{mark}")
            print(f"      font:    {concept.get('headline_font','')} / preset: {concept.get('bg_preset','')}")
            print(f"      lines:   {' / '.join(concept.get('headline_lines', []))}")
            print(f"      title:   {concept.get('title','')}")
            if score:
                print(f"      scores:  visual={score.get('visual_impact','?')} "
                      f"readability={score.get('text_readability','?')} "
                      f"ctr={score.get('ctr_potential','?')}  "
                      f"avg={avg_score}  {_bar(avg_score)}")
                print(f"      notes:   {score.get('notes', '')}")
            print(f"      image:   {rendered_str}")

        if scored:
            avgs = {
                "visual_impact":    _avg([s.get("visual_impact", 0)    for s in scored]),
                "text_readability": _avg([s.get("text_readability", 0) for s in scored]),
                "ctr_potential":    _avg([s.get("ctr_potential", 0)    for s in scored]),
            }
            overall_avg = _avg(list(avgs.values()))

            prev = _load_last_history(name)
            prev_avgs = prev.get("averages", {}) if prev else {}

            print()
            print(f"  Set diversity:    {diversity}/5  {_bar(diversity)}")
            print(f"  Avg visual:       {avgs['visual_impact']}/5  {_bar(avgs['visual_impact'])}"
                  f"{_print_delta('visual', avgs['visual_impact'], prev_avgs.get('visual_impact'))}")
            print(f"  Avg readability:  {avgs['text_readability']}/5  {_bar(avgs['text_readability'])}"
                  f"{_print_delta('readability', avgs['text_readability'], prev_avgs.get('text_readability'))}")
            print(f"  Avg CTR:          {avgs['ctr_potential']}/5  {_bar(avgs['ctr_potential'])}"
                  f"{_print_delta('ctr', avgs['ctr_potential'], prev_avgs.get('ctr_potential'))}")
            print(f"  Overall avg:      {overall_avg}/5  {_bar(overall_avg)}"
                  f"{_print_delta('overall', overall_avg, prev.get('overall_avg') if prev else None)}")
            print(f"  Summary: {summary_txt}")
        else:
            avgs        = {}
            overall_avg = 0.0

        # ── Optional refinement pass ───────────────────────────────────────
        if refine and scored:
            print(f"\n  Refining weak concepts (threshold {refine_threshold})…")
            refined_concepts, revised_idx = revise_weak_concepts(
                concepts, scored, summary, threshold=refine_threshold
            )
            if revised_idx:
                # Re-render only revised thumbnails
                print(f"  Re-rendering {len(revised_idx)} revised concept(s)…", end=" ", flush=True)
                refined_rendered = list(rendered)
                for i in revised_idx:
                    fp = face_paths[i] if face_paths and i < len(face_paths) else None
                    out_path = render_dir / f"thumb_{i}_refined.jpg"
                    try:
                        compose(
                            face_path=fp,
                            bg_preset=refined_concepts[i].get("bg_preset", "red"),
                            headline_font=refined_concepts[i].get("headline_font", "anton"),
                            headline_lines=refined_concepts[i].get("headline_lines", ["WATCH"]),
                            subtext=refined_concepts[i].get("subtext", ""),
                            caveat_aside=refined_concepts[i].get("caveat_aside"),
                            badge=refined_concepts[i].get("badge"),
                            face_side=refined_concepts[i].get("face_side", "right"),
                            out_path=str(out_path),
                            highlight_word=refined_concepts[i].get("highlight_word"),
                        )
                        refined_rendered[i] = out_path
                    except Exception as e:
                        print(f"\n    [render] concept {i} failed: {e}", file=sys.stderr)
                print("done")

                # Re-judge full set with refined thumbnails
                print("  Re-judging…", end=" ", flush=True)
                try:
                    refined_judgment = judge_rendered(
                        refined_rendered, refined_concepts, summary,
                        reference_image=ref_img, model=judge_model,
                    )
                    print("done")
                except Exception as e:
                    print(f"FAILED: {e}")
                    refined_judgment = {}

                refined_scored = refined_judgment.get("thumbnails", [])
                if refined_scored:
                    print("\n  Before → After (refined concepts):")
                    for i in revised_idx:
                        old_s = scored[i] if i < len(scored) else {}
                        new_s = refined_scored[i] if i < len(refined_scored) else {}
                        old_avg = _avg([old_s.get("visual_impact", 0), old_s.get("text_readability", 0), old_s.get("ctr_potential", 0)])
                        new_avg = _avg([new_s.get("visual_impact", 0), new_s.get("text_readability", 0), new_s.get("ctr_potential", 0)])
                        delta = round(new_avg - old_avg, 2)
                        sign  = f"+{delta}" if delta >= 0 else str(delta)
                        old_hook = " / ".join(concepts[i].get("headline_lines", []))
                        new_hook = " / ".join(refined_concepts[i].get("headline_lines", []))
                        print(f"    [{i}] '{old_hook}' → '{new_hook}'")
                        print(f"         visual {old_s.get('visual_impact','?')}→{new_s.get('visual_impact','?')}  "
                              f"readability {old_s.get('text_readability','?')}→{new_s.get('text_readability','?')}  "
                              f"ctr {old_s.get('ctr_potential','?')}→{new_s.get('ctr_potential','?')}  "
                              f"avg {old_avg}→{new_avg} ({sign})")
                        print(f"         {new_s.get('notes','')}")
                        print(f"         image: {refined_rendered[i]}")

                    # Update state to refined for history
                    concepts   = refined_concepts
                    rendered   = refined_rendered
                    judgment   = refined_judgment
                    scored     = refined_scored
                    avgs = {
                        "visual_impact":    _avg([s.get("visual_impact", 0)    for s in scored]),
                        "text_readability": _avg([s.get("text_readability", 0) for s in scored]),
                        "ctr_potential":    _avg([s.get("ctr_potential", 0)    for s in scored]),
                    }
                    overall_avg = _avg(list(avgs.values()))
                    print(f"\n  Post-refinement overall avg: {overall_avg}/5  {_bar(overall_avg)}")
            else:
                print("  No concepts below threshold — nothing to refine.")

        print(f"\n  Rendered images → {render_dir}/")

        result = {
            "fixture":      name,
            "timestamp":    datetime.now().isoformat(),
            "concepts":     concepts,
            "rendered_dir": str(render_dir),
            "judgment":     judgment,
            "averages":     avgs,
            "overall_avg":  overall_avg,
        }
        results.append(result)

        # Append compact entry to history log
        if avgs:
            history_entry = {
                "fixture":      name,
                "timestamp":    result["timestamp"],
                "rendered_dir": str(render_dir),
                "averages":     avgs,
                "overall_avg":  overall_avg,
                "diversity":    diversity,
                "best_index":   best_idx,
                "hooks":        [" / ".join(c.get("headline_lines", [])) for c in concepts],
            }
            _append_history(history_entry)

        out_path = render_dir / "eval_result.json"
        out_path.write_text(json.dumps(result, indent=2))
        print(f"  JSON → {out_path}")

    return results


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render thumbnails and eval visual quality with Gemini vision.",
        epilog=(
            "Examples:\n"
            "  eval_thumbnails.py                              # all fixtures\n"
            "  eval_thumbnails.py fixtures/foo.json            # one fixture\n"
            "  eval_thumbnails.py fixtures/foo.json --state tmp/state.json\n"
            "  eval_thumbnails.py --model gemini-2.5-pro       # stronger judge"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("fixtures", nargs="*", help="Fixture paths (default: all fixtures/)")
    parser.add_argument("--model", default=None, help="Override judge model (e.g. gemini-2.5-pro)")
    parser.add_argument("--state", default=None, metavar="PATH",
                        help="Load concepts from a thumbnail.py --save-state file (skips LLM gen)")
    parser.add_argument("--face-dir", default=None, metavar="DIR",
                        help="Directory containing face_0.jpg … face_N.jpg for realistic rendering")
    parser.add_argument("--reference-image", default=None, metavar="PATH",
                        help="Screenshot of reference creator's channel — used as visual benchmark")
    parser.add_argument("--refine", action="store_true",
                        help="After judging, revise weak concepts using judge feedback and re-judge")
    parser.add_argument("--refine-threshold", type=float, default=4.0, metavar="N",
                        help="Concepts with avg score below this are revised (default: 4.0)")
    args = parser.parse_args()

    fixtures = load_fixtures(args.fixtures)
    if not fixtures:
        print("No fixtures found. Run orchestrator.py with --save-fixture first.", file=sys.stderr)
        sys.exit(1)

    print(f"\nEvaluating {len(fixtures)} fixture(s) — rendering + vision judge…")
    results = run_evals(
        fixtures,
        judge_model=args.model,
        state_path=args.state,
        face_dir=args.face_dir,
        reference_image_path=args.reference_image,
        refine=args.refine,
        refine_threshold=args.refine_threshold,
    )

    if not results:
        print("\nNo results — all fixtures failed.", file=sys.stderr)
        sys.exit(1)

    overall = _avg([r["overall_avg"] for r in results if r["overall_avg"]])
    print(f"\n{'═' * 60}")
    print(f"  Fixtures evaluated: {len(results)}")
    if overall:
        print(f"  Grand avg CTR:      {overall}/5  {_bar(overall)}")
    print(f"  Results saved to:   {RESULTS_DIR}/")


if __name__ == "__main__":
    main()
