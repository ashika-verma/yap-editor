#!/usr/bin/env python3
"""
Self-improving thumbnail agent.

Loop:
  1. Pre-detect face bboxes once (cached for all iterations).
  2. Read thumbnail.py source + last judge scores.
  3. Ask LLM to propose ONE targeted rendering edit to thumbnail.py.
  4. Apply the patch, re-render (no face re-detection), re-judge at 320×180.
  5. Keep if avg improved; otherwise restore from in-memory snapshot.
  6. Repeat up to MAX_ITER times or until avg ≥ TARGET_SCORE or 3 consecutive no-improvements.
  7. Single git commit at the end with all accepted changes.

Safety:
  - Only edits scripts/thumbnail.py — eval_thumbnails.py is read-only.
  - Uses in-memory source snapshots for rollback (no risky git reset).
  - Hard cap: MAX_ITER iterations.

Usage:
  export $(grep -v '^#' .env.local | xargs)
  .venv/bin/python3 scripts/thumbnail_agent.py \\
      fixtures/55f23d95_20260516_102956.json \\
      --state tmp/thumb_state_v2.json \\
      --face-dir tmp \\
      --reference-image scripts/reference_style.png
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from llm import generate, generate_vision  # noqa: E402
from thumbnail import _detect_face, FACE_BBOX_SCHEMA  # noqa: E402
from eval_thumbnails import judge_rendered, _avg, _bar  # noqa: E402

THUMBNAIL_PY = Path(__file__).parent / "thumbnail.py"
RESULTS_DIR  = Path(__file__).parent.parent / "eval_results" / "thumbnails"

MAX_ITER     = 10
TARGET_SCORE = 4.7
NO_IMPROVE_PATIENCE = 3

# ── Schema for agent's proposed edit ─────────────────────────────────────────

EDIT_SCHEMA: dict = {
    "type": "object",
    "required": ["reasoning", "old_str", "new_str", "expected_improvement"],
    "properties": {
        "reasoning": {"type": "string"},
        "old_str":   {"type": "string"},
        "new_str":   {"type": "string"},
        "expected_improvement": {"type": "string"},
    },
}

# ── Source patch helpers ──────────────────────────────────────────────────────

def _read_source() -> str:
    return THUMBNAIL_PY.read_text()


def _apply_patch(source: str, old_str: str, new_str: str) -> str | None:
    """Return updated source, or None if old_str not found."""
    if old_str not in source:
        return None
    return source.replace(old_str, new_str, 1)


def _write_source(src: str) -> None:
    THUMBNAIL_PY.write_text(src)


# ── Face bbox pre-detection ───────────────────────────────────────────────────

def predetect_bboxes(face_paths: list[str | None]) -> list[dict | None]:
    """Detect face bboxes once. Cached in memory for all render iterations."""
    from PIL import Image
    from thumbnail import _DEFAULT_FACE

    bboxes: list[dict | None] = []
    for i, fp in enumerate(face_paths):
        if fp is None or not Path(fp).exists():
            bboxes.append(None)
            continue
        try:
            from rembg import remove, new_session
            import numpy as np

            img     = Image.open(fp).convert("RGBA")
            session = new_session("u2net_human_seg")
            result  = remove(img, session=session)
            arr     = np.array(result)
            alpha   = arr[:, :, 3]
            alpha   = np.where(alpha > 30, 255, 0).astype(np.uint8)
            arr[:, :, 3] = alpha
            cutout  = Image.fromarray(arr)

            W, H          = 1280, 720
            BLEED         = int(W * 0.18)
            PERSON_H_FRAC = 0.97
            target_h = int(H * PERSON_H_FRAC)
            target_w = int(cutout.width * (target_h / cutout.height))
            cutout   = cutout.resize((target_w, target_h), Image.LANCZOS)
            person_w = target_w
            py       = H - target_h
            px       = W - person_w + BLEED  # right side for detection

            detect_bg = Image.new("RGB", (W, H), (128, 128, 128))
            detect_bg.paste(cutout.convert("RGB"), (px, py), cutout.getchannel("A"))
            buf = io.BytesIO()
            detect_bg.save(buf, format="JPEG", quality=75)
            print(f"  [bbox] face {i}…", end=" ", flush=True)
            bbox = _detect_face(buf.getvalue())
            print(f"top={bbox['face_top']:.2f} btm={bbox['face_bottom']:.2f}")
            bboxes.append(bbox)
        except Exception as e:
            print(f"  [bbox] face {i} failed ({e}), using default", file=sys.stderr)
            from thumbnail import _DEFAULT_FACE
            bboxes.append(_DEFAULT_FACE.copy())
    return bboxes


# ── Render pass ───────────────────────────────────────────────────────────────

def render_all(
    concepts: list[dict],
    out_dir: Path,
    face_paths: list[str | None],
    bboxes: list[dict | None],
) -> list[Path | None]:
    """Reload thumbnail module so edits take effect, then render all concepts."""
    import importlib, thumbnail as _thumb_mod
    importlib.reload(_thumb_mod)

    out_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[Path | None] = []
    for i, concept in enumerate(concepts):
        out_path = out_dir / f"thumb_{i}.jpg"
        fp   = face_paths[i] if i < len(face_paths) else None
        bbox = bboxes[i]     if i < len(bboxes)     else None
        try:
            _thumb_mod.compose(
                face_path=fp,
                bg_preset=concept.get("bg_preset", "red"),
                headline_font=concept.get("headline_font", "anton"),
                headline_lines=concept.get("headline_lines", ["WATCH"]),
                subtext=concept.get("subtext", ""),
                caveat_aside=concept.get("caveat_aside"),
                badge=concept.get("badge"),
                face_side=concept.get("face_side", "right"),
                out_path=str(out_path),
                face_bbox=bbox,
            )
            rendered.append(out_path)
        except Exception as e:
            print(f"  [render] concept {i} failed: {e}", file=sys.stderr)
            rendered.append(None)
    return rendered


# ── Scores helper ─────────────────────────────────────────────────────────────

def _concept_avg(score: dict) -> float:
    return _avg([
        score.get("visual_impact", 0),
        score.get("text_readability", 0),
        score.get("ctr_potential", 0),
    ])


def _set_avg(scored: list[dict]) -> float:
    return _avg([_concept_avg(s) for s in scored]) if scored else 0.0


# ── Agent prompt ──────────────────────────────────────────────────────────────

def _build_agent_prompt(
    source: str,
    scored: list[dict],
    concepts: list[dict],
    judge_summary: str,
    iteration: int,
    cap: int,
    edit_history: list[dict],
) -> str:
    score_lines = []
    for i, (s, c) in enumerate(zip(scored, concepts)):
        hook = " / ".join(c.get("headline_lines", []))
        avg  = _concept_avg(s)
        score_lines.append(
            f"  [{i}] '{hook}' ({c.get('bg_preset')}, face_{c.get('face_side')}): "
            f"visual={s.get('visual_impact')}/5 read={s.get('text_readability')}/5 "
            f"ctr={s.get('ctr_potential')}/5 avg={avg}\n"
            f"      → {s.get('notes', '')}"
        )

    history_txt = ""
    if edit_history:
        history_txt = "\n\nEDITS ALREADY TRIED THIS SESSION (do NOT repeat):\n"
        for h in edit_history:
            outcome = f"+{h['delta']}" if h["delta"] >= 0 else str(h["delta"])
            history_txt += f"  {'✓' if h['kept'] else '✗'} ({outcome}) {h['reasoning'][:120]}\n"

    return f"""You are a thumbnail rendering engineer. Edit thumbnail.py to improve visual quality scores.

CURRENT SCORES — iteration {iteration}/{cap}, set avg = {_set_avg(scored):.2f}/5:
{chr(10).join(score_lines)}

JUDGE SUMMARY: {judge_summary}

TARGET: push set avg above {TARGET_SCORE}/5. Close the gap on the weakest metric first.

━━━ IMPORTANT: CONCEPTS ARE FROZEN ━━━
The 6 concepts (hooks, bg_preset, font, face_side) are loaded from a cached state file.
Any edit to the concept-generation prompt or _fix_concepts() has ZERO effect on this run.
Focus exclusively on the RENDERING pipeline.

TUNABLE LEVERS — what's worth changing:
  Layout:   PAD_X, PAD_Y, GAP (line gap), BOTTOM_MARGIN, BLEED, hl_start, hl_end
  Person:   PERSON_H_FRAC, px offset formula (the ± BLEED expressions)
  Color:    BG_PRESETS values — bg, headline, or subtext RGB tuples per preset
  Text:     _fit_font_group() lo/hi bounds; stroke width/color via draw.text(stroke_*)
            (Pillow supports stroke_width=N, stroke_fill=(r,g,b) in draw.text)
  Shadow:   shadow opacity (currently 110), GaussianBlur radius (currently 18), offset (10,10)
  Subtext:  sm_size (currently 32), SM_MAX_W, y-position
  Badge:    font size, padding, border radius, colors

OFF LIMITS — do not touch:
  - _detect_face() or generate_vision() / generate() calls
  - File I/O, subprocess, imports
  - compose() signature (do not add/remove params)
  - Any concept-generation code (generate_concepts, CONCEPTS_SCHEMA, _fix_concepts, main())
  - eval_thumbnails.py (you cannot see or modify the judge)
{history_txt}

RULES FOR YOUR EDIT:
  1. Propose exactly ONE change — highest-leverage single edit.
  2. old_str must appear VERBATIM in the source below, exactly once.
  3. new_str must be valid Python.
  4. Do not add explanatory comments.
  5. Prefer small targeted changes over rewrites.

thumbnail.py source:
```python
{source}
```

Return JSON: reasoning, old_str, new_str, expected_improvement."""


# ── Main agent loop ───────────────────────────────────────────────────────────

def run_agent(
    concepts: list[dict],
    face_paths: list[str | None],
    bboxes: list[dict | None],
    summary: str,
    ref_img: bytes | None,
    judge_model: str | None,
    run_dir: Path,
    cap: int = MAX_ITER,
) -> None:
    from datetime import datetime

    iteration    = 0
    no_improve   = 0
    edit_history: list[dict] = []
    best_source  = _read_source()

    # ── Initial baseline ──────────────────────────────────────────────────────
    print(f"\n── Baseline render ───────────────────────────────────────")
    init_dir = run_dir / "iter_0"
    rendered = render_all(concepts, init_dir, face_paths, bboxes)
    print("  Judging…", end=" ", flush=True)
    judgment    = judge_rendered(rendered, concepts, summary, reference_image=ref_img, model=judge_model)
    print("done")
    scored      = judgment.get("thumbnails", [])
    judge_sum   = judgment.get("summary", "")
    best_avg    = _set_avg(scored)
    print(f"  Baseline avg: {best_avg:.2f}/5  {_bar(best_avg)}")
    print(f"  Summary: {judge_sum}")

    if best_avg >= TARGET_SCORE:
        print(f"\n  Already at target ({TARGET_SCORE}). Done.")
        return

    # ── Iteration loop ────────────────────────────────────────────────────────
    while iteration < cap and no_improve < NO_IMPROVE_PATIENCE:
        iteration += 1
        print(f"\n── Iteration {iteration}/{cap}  (no_improve={no_improve}/{NO_IMPROVE_PATIENCE}) ──")

        # Ask LLM for a patch
        print("  Proposing edit…", end=" ", flush=True)
        try:
            prompt = _build_agent_prompt(
                source=_read_source(),
                scored=scored,
                concepts=concepts,
                judge_summary=judge_sum,
                iteration=iteration,
                cap=cap,
                edit_history=edit_history,
            )
            raw  = generate(prompt, schema=EDIT_SCHEMA)
            edit = json.loads(raw)
        except Exception as e:
            print(f"FAILED ({e})")
            no_improve += 1
            continue
        print("done")

        reasoning = edit.get("reasoning", "")
        old_str   = edit.get("old_str", "")
        new_str   = edit.get("new_str", "")
        expected  = edit.get("expected_improvement", "")
        print(f"  Edit: {reasoning[:160]}")
        print(f"  Targets: {expected}")

        # Apply patch to source (in memory first)
        current_src = _read_source()
        patched_src = _apply_patch(current_src, old_str, new_str)
        if patched_src is None:
            print("  ✗ old_str not found in source — skipping.")
            no_improve += 1
            edit_history.append({"reasoning": reasoning, "delta": 0.0, "kept": False})
            continue

        _write_source(patched_src)

        # Re-render + re-judge
        iter_dir = run_dir / f"iter_{iteration}"
        rendered = render_all(concepts, iter_dir, face_paths, bboxes)
        print("  Judging…", end=" ", flush=True)
        try:
            new_judgment = judge_rendered(rendered, concepts, summary, reference_image=ref_img, model=judge_model)
            print("done")
        except Exception as e:
            print(f"FAILED ({e}) — reverting.")
            _write_source(best_source)
            no_improve += 1
            edit_history.append({"reasoning": reasoning, "delta": 0.0, "kept": False})
            continue

        new_scored = new_judgment.get("thumbnails", [])
        new_avg    = _set_avg(new_scored)
        delta      = round(new_avg - best_avg, 2)
        sign       = f"+{delta}" if delta >= 0 else str(delta)
        print(f"  Score: {best_avg:.2f} → {new_avg:.2f} ({sign})")

        if new_avg > best_avg:
            print("  ✓ Kept.")
            best_avg    = new_avg
            best_source = patched_src
            scored      = new_scored
            judge_sum   = new_judgment.get("summary", judge_sum)
            no_improve  = 0
            edit_history.append({"reasoning": reasoning, "delta": delta, "kept": True})
        else:
            print("  ✗ Reverted.")
            _write_source(best_source)
            no_improve += 1
            edit_history.append({"reasoning": reasoning, "delta": delta, "kept": False})

        if best_avg >= TARGET_SCORE:
            print(f"\n  Reached target {TARGET_SCORE}. Stopping.")
            break

    # ── Final summary ─────────────────────────────────────────────────────────
    print(f"\n{'═' * 60}")
    print(f"  Agent done — {iteration} iteration(s), {sum(1 for e in edit_history if e['kept'])} accepted edits.")
    print(f"  Final avg: {best_avg:.2f}/5  {_bar(best_avg)}")
    kept = [e for e in edit_history if e["kept"]]
    if kept:
        print("  Accepted edits:")
        for e in kept:
            print(f"    +{e['delta']}  {e['reasoning'][:100]}")

    # Single commit for all accepted changes
    accepted_count = len(kept)
    if accepted_count > 0:
        try:
            subprocess.run(["git", "add", str(THUMBNAIL_PY)], check=True, capture_output=True)
            msg = f"thumbnail-agent: {accepted_count} accepted edit(s), avg {best_avg:.2f}/5"
            subprocess.run(["git", "commit", "-m", msg], check=True, capture_output=True)
            print(f"  Committed: {msg}")
        except Exception as e:
            print(f"  [git] commit failed: {e}", file=sys.stderr)
    else:
        print("  No edits accepted — thumbnail.py unchanged.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Self-improving thumbnail agent.")
    parser.add_argument("fixture", help="Fixture JSON path")
    parser.add_argument("--state", default=None, metavar="PATH",
                        help="Cached concepts state file (skips LLM concept gen)")
    parser.add_argument("--face-dir", default=None, metavar="DIR",
                        help="Directory containing face_0.jpg … face_N.jpg")
    parser.add_argument("--reference-image", default=None, metavar="PATH",
                        help="Reference creator screenshot for eval benchmark")
    parser.add_argument("--model", default=None, help="Judge model override")
    parser.add_argument("--max-iter", type=int, default=MAX_ITER,
                        help=f"Max iterations (default: {MAX_ITER})")
    args = parser.parse_args()

    raw      = json.loads(Path(args.fixture).read_text())
    plan     = raw.get("plan", raw)
    summary  = plan.get("summary", "(no summary)")

    if args.state and Path(args.state).exists():
        concepts = json.loads(Path(args.state).read_text())["concepts"]
        print(f"Concepts: {len(concepts)} loaded from {args.state}")
    else:
        print("No --state file provided. Run eval_thumbnails.py first.", file=sys.stderr)
        sys.exit(1)

    face_paths: list[str | None] = []
    if args.face_dir:
        for i in range(len(concepts)):
            fp = Path(args.face_dir) / f"face_{i}.jpg"
            face_paths.append(str(fp) if fp.exists() else None)
        print(f"Face frames: {sum(1 for f in face_paths if f)}/{len(concepts)} found")
    else:
        face_paths = [None] * len(concepts)

    ref_img: bytes | None = None
    if args.reference_image and Path(args.reference_image).exists():
        ref_img = Path(args.reference_image).read_bytes()

    print(f"\nPre-detecting face bboxes (one-time, cached for all iterations)…")
    bboxes = predetect_bboxes(face_paths)

    from datetime import datetime
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RESULTS_DIR / f"agent_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nAgent will edit scripts/thumbnail.py — up to {args.max_iter} iterations, target {TARGET_SCORE}/5.")
    print(f"Rollback: in-memory snapshots (no git reset). Single commit at end if improved.")
    ans = input("Proceed? [y/N] ").strip().lower()
    if ans != "y":
        print("Aborted.")
        sys.exit(0)

    run_agent(
        concepts=concepts,
        face_paths=face_paths,
        bboxes=bboxes,
        summary=summary,
        ref_img=ref_img,
        judge_model=args.model,
        run_dir=run_dir,
        cap=args.max_iter,
    )


if __name__ == "__main__":
    main()
