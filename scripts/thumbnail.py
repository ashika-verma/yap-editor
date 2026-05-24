#!/usr/bin/env python3
"""
Thumbnail generator — science/psychology creator design system.

Design language:
  - Flat saturated background (6 presets, no gradients)
  - Anton (serious/shocking) or Fredoka One (funny/relatable) headline, full-width, stacked
  - Headline rendered behind person — overlaps body, not face
  - Tahoma Bold subtext below, sentence case, smaller
  - Optional Caveat brush script aside (lowercase, punchline)
  - Optional small badge box (Anton, tiny)
  - Full-bleed centered rembg person in front

Pipeline:
  1. LLM → concepts (preset, fonts, text, badge, face_timestamp)
  2. ffmpeg → face frame at chosen timestamp
  3. rembg → person cutout
  4. Pillow → bg + headline (behind) + person (front) + subtext + extras

State caching:
  First run:  thumbnail.py video.mp4 --plan-json plan.json --save-state tmp/state.json
  Replay:     thumbnail.py video.mp4 --plan-json plan.json --load-state tmp/state.json
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from llm import generate, generate_vision  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────

W, H = 1280, 720

FONTS_DIR = Path(__file__).parent / "fonts"

_FONT_PATHS = {
    "anton":   [FONTS_DIR / "Anton-Regular.ttf"],
    "fredoka": [FONTS_DIR / "FredokaOne-Regular.ttf"],
    "caveat":  [FONTS_DIR / "Caveat-Regular.ttf"],
    "subtext": [
        FONTS_DIR / "DMSans-Medium.ttf",
        Path("/System/Library/Fonts/Supplemental/Tahoma Bold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ],
}

# ── Color presets ─────────────────────────────────────────────────────────────

BG_PRESETS: dict[str, dict] = {
    "yellow":  {"bg": (255, 217, 61),  "headline": (26, 26, 26),   "subtext": (26, 26, 26)},
    "red":     {"bg": (192, 57, 43),   "headline": (255, 255, 255), "subtext": (255, 255, 255)},
    "blue":    {"bg": (74, 189, 232),  "headline": (26, 26, 26),   "subtext": (26, 26, 26)},
    "pink":    {"bg": (232, 83, 143),  "headline": (255, 255, 255), "subtext": (255, 255, 255)},
    "charcoal":{"bg": (26, 26, 26),    "headline": (255, 217, 61),  "subtext": (255, 255, 255)},
    "tan":     {"bg": (201, 185, 154), "headline": (26, 26, 26),   "subtext": (26, 26, 26)},
}

# ── LLM schemas ───────────────────────────────────────────────────────────────

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
                                        "enum": ["top-left","top-right","bottom-left","bottom-right"],
                                    },
                                },
                            },
                        ]
                    },
                    "face_timestamp": {"type": "number"},
                },
            },
        }
    },
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _extract_transcript(plan: dict, max_chars: int = 4000) -> str:
    words: list[str] = []
    for seg in plan.get("segments", []):
        for w in seg.get("words", []):
            word = w.get("word", "").strip()
            if word:
                words.append(word)
    return " ".join(words)[:max_chars]


def _top_keywords(transcript: str, n: int = 20) -> str:
    stop = {
        "the","a","an","and","or","but","in","on","at","to","for","of","with",
        "is","are","was","were","be","been","have","has","had","do","does","did",
        "i","you","he","she","it","we","they","that","this","these","those",
        "so","just","like","very","my","your","our","their","about","what","how",
        "not","no","yeah","okay","um","uh","actually","really","kind","thing",
        "know","think","get","got","go","make","because","if","when","then","also",
        "there","here","some","all","out","up","can","will","its","from","by",
    }
    freq: dict[str, int] = {}
    for w in transcript.lower().split():
        w = w.strip(".,!?;:\"'()[]")
        if len(w) > 3 and w not in stop:
            freq[w] = freq.get(w, 0) + 1
    return ", ".join(sorted(freq, key=lambda k: -freq[k])[:n])


def _fix_concepts(concepts: list[dict], summary: str) -> list[dict]:
    """
    Post-generation corrections:
    - vlog/blog swap when video is a vlog
    - Strip trailing punctuation (periods, exclamation marks look bad in display type)
    - Truncate runaway lines (>22 chars)
    - Drop single-word content-free lines
    """
    summary_lower = summary.lower()
    is_vlog = "vlog" in summary_lower

    _STOPWORD_LINES = {"WANT", "TODAY", "TECH", "GOOD", "WORK", "THING", "STUFF", "NOW", "AI", "VIDEO"}

    for concept in concepts:
        if is_vlog and "blog" in concept.get("title", "").lower():
            concept["title"] = concept["title"].replace("Blog", "Vlog").replace("blog", "vlog")

        lines = concept.get("headline_lines", [])
        fixed = []
        for line in lines:
            line = line.strip().rstrip(".,!?…")   # strip trailing punctuation
            if is_vlog:
                line = line.replace("BLOG", "VLOG").replace("Blog", "Vlog")
            if len(line) > 22:
                line = line[:22].rstrip()
            upper = line.upper()
            if upper in _STOPWORD_LINES and len(lines) > 1:
                continue
            fixed.append(line)
        concept["headline_lines"] = fixed or lines

    return concepts


def _load_font(style: str, size: int):
    from PIL import ImageFont
    for p in _FONT_PATHS.get(style, _FONT_PATHS["subtext"]):
        try:
            return ImageFont.truetype(str(p), size=size)
        except Exception:
            pass
    return ImageFont.load_default(size=size)


def _fit_font(draw, text: str, max_w: int, max_h: int, style: str):
    """Binary-search largest size fitting both max_w and max_h."""
    lo, hi, best = 20, 700, 20
    while lo <= hi:
        mid = (lo + hi) // 2
        font = _load_font(style, mid)
        bb = draw.textbbox((0, 0), text, font=font)
        if (bb[2] - bb[0]) <= max_w and (bb[3] - bb[1]) <= max_h:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return _load_font(style, best)


def _fit_font_group(draw, lines: list[str], max_w: int, max_h_per_line: int, style: str):
    """Largest font size where EVERY line fits max_w × max_h_per_line.
    Keeps all lines at the same size so the headline looks typographically consistent.
    """
    lo, hi, best = 20, 700, 20
    while lo <= hi:
        mid = (lo + hi) // 2
        font = _load_font(style, mid)
        if all(
            (draw.textbbox((0, 0), ln, font=font)[2] - draw.textbbox((0, 0), ln, font=font)[0]) <= max_w
            and (draw.textbbox((0, 0), ln, font=font)[3] - draw.textbbox((0, 0), ln, font=font)[1]) <= max_h_per_line
            for ln in lines
        ):
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return _load_font(style, best)


# ── Face handling ─────────────────────────────────────────────────────────────

def extract_frame(video_path: str, timestamp: float, out_path: str) -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-ss", str(timestamp), "-i", video_path,
             "-vframes", "1", "-q:v", "2", out_path, "-y"],
            check=True, capture_output=True,
        )
        return Path(out_path).exists()
    except subprocess.CalledProcessError as e:
        print(f"[ffmpeg] {e.stderr.decode()[:200]}", file=sys.stderr)
        return False


def _remove_bg(img_path: str):
    try:
        from rembg import remove, new_session
        from PIL import Image
        import numpy as np

        img = Image.open(img_path).convert("RGBA")
        # u2net_human_seg is tuned for people and produces cleaner edges
        session = new_session("u2net_human_seg")
        result  = remove(img, session=session)

        # Threshold the alpha to a hard mask — avoids semi-transparent bleed-through
        arr   = np.array(result)
        alpha = arr[:, :, 3]
        alpha = np.where(alpha > 30, 255, 0).astype(np.uint8)
        arr[:, :, 3] = alpha
        return Image.fromarray(arr)
    except Exception as e:
        print(f"[rembg] {type(e).__name__}: {e}", file=sys.stderr)
        return None


# ── Face detection ────────────────────────────────────────────────────────────

FACE_BBOX_SCHEMA: dict = {
    "type": "object",
    "required": ["face_top", "face_bottom", "face_left", "face_right"],
    "properties": {
        "face_top":    {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "face_bottom": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "face_left":   {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "face_right":  {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
}

_DEFAULT_FACE = {"face_top": 0.08, "face_bottom": 0.50, "face_left": 0.30, "face_right": 0.70}


def _detect_face(img_bytes: bytes) -> dict:
    """
    Ask vision LLM to locate the face in the image.
    Returns bbox as 0–1 fractions. Falls back to _DEFAULT_FACE on any failure.
    Uses Gemini; falls back to local Gemma on rate limit (via generate_vision).
    """
    prompt = (
        "This image shows a person composited on a solid or gray background. "
        "Locate the face/head region ONLY (not body, hair below chin, or background). "
        "Return the bounding box as decimal fractions of image dimensions: "
        "face_top and face_bottom are Y-axis (0.0=top, 1.0=bottom), "
        "face_left and face_right are X-axis (0.0=left, 1.0=right)."
    )
    try:
        raw  = generate_vision(prompt, images=[img_bytes], schema=FACE_BBOX_SCHEMA)
        bbox = json.loads(raw)
        if (bbox["face_bottom"] > bbox["face_top"] + 0.05 and
                bbox["face_right"] > bbox["face_left"] + 0.05):
            return bbox
        print("[thumbnail] face bbox looks wrong, using defaults", file=sys.stderr)
    except Exception as e:
        print(f"[thumbnail] face detect failed ({type(e).__name__}), using defaults", file=sys.stderr)
    return _DEFAULT_FACE.copy()


def _headline_zone(face: dict, n_lines: int) -> tuple[int, int]:
    """
    Pick the best vertical band for a full-width headline that avoids the face.

    Tries three zones in priority order:
      1. Above hairline  — clean, no overlap
      2. Body zone       — below chin, person composited on top (classic Shan Boody look)
      3. Straddling      — centered on face_top, lines appear above + below face
    """
    PAD_Y      = 18
    face_top   = int(face["face_top"]    * H)
    face_btm   = int(face["face_bottom"] * H)

    above_px  = face_top - PAD_Y
    body_end  = int(H * 0.84)
    body_px   = body_end - face_btm

    MIN_ABOVE = int(H * 0.22)
    MIN_BODY  = int(H * 0.28)

    if above_px >= MIN_ABOVE:
        return (PAD_Y, face_top - 8)
    elif body_px >= MIN_BODY:
        # Overlap body slightly (chin − 6%) so text feels "behind" the person
        return (face_btm - int(H * 0.06), body_end)
    else:
        # Straddling: center block just above face_top so lines bracket the face
        span = int(H * 0.60)
        mid  = face_top + int((face_btm - face_top) * 0.25)
        return (mid - span // 2, mid + span // 2)


# ── Composition ───────────────────────────────────────────────────────────────

def compose(
    face_path: str | None,
    bg_preset: str,
    headline_font: str,
    headline_lines: list[str],
    subtext: str,
    caveat_aside: str | None,
    badge: dict | None,
    face_side: str,    # "left" | "center" | "right" — controls person position
    out_path: str,
    face_bbox: dict | None = None,  # pre-computed bbox; skips LLM detection when provided
) -> None:
    """
    Two-pass render:
      Pass 1 — composite person on neutral bg → vision LLM detects face bbox
              (skipped when face_bbox is provided — use to cache across iterations)
      Pass 2 — render full-width headline in the clear zone → person on top
    Falls back to rule-based defaults if face detection fails.
    """
    from PIL import Image, ImageDraw, ImageFilter

    preset  = BG_PRESETS.get(bg_preset, BG_PRESETS["red"])
    bg_rgb  = preset["bg"]
    hl_rgb  = preset["headline"]
    sub_rgb = preset["subtext"]

    PAD_X         = 36
    BLEED         = int(W * 0.18)
    PERSON_H_FRAC = 0.97

    # ── Step 1: scale cutout ───────────────────────────────────────────────────
    cutout   = None
    person_w = int(W * 0.55)
    if face_path and Path(face_path).exists():
        raw = _remove_bg(face_path)
        if raw is not None:
            target_h = int(H * PERSON_H_FRAC)
            target_w = int(raw.width * (target_h / raw.height))
            cutout   = raw.resize((target_w, target_h), Image.LANCZOS)
            person_w = target_w

    target_h = int(H * PERSON_H_FRAC)
    py = H - target_h

    if face_side == "left":
        px = -BLEED
    elif face_side == "right":
        px = W - person_w + BLEED
    else:
        px = (W - person_w) // 2

    # ── Step 2: detect face position (person at final x/y on neutral bg) ──────
    if face_bbox is None:
        face_bbox = _DEFAULT_FACE.copy()
        if cutout is not None:
            detect_bg = Image.new("RGB", (W, H), (128, 128, 128))
            detect_bg.paste(cutout.convert("RGB"), (px, py), cutout.getchannel("A"))
            buf = io.BytesIO()
            detect_bg.save(buf, format="JPEG", quality=75)
            print("[thumbnail] detecting face…", end=" ", flush=True, file=sys.stderr)
            face_bbox = _detect_face(buf.getvalue())
            print(f"  top={face_bbox['face_top']:.2f} btm={face_bbox['face_bottom']:.2f}", file=sys.stderr)

    # ── Steps 3–4: render bg canvas + headline ───────────────────────────────────
    # All layouts use the full frame height. Left/right restrict the horizontal zone
    # to the side opposite the person; center uses full width. A single uniform font
    # size is computed so every line renders at the same scale (no mismatched sizes).
    # For center face the text block is anchored so line-1 bottom touches the
    # hairline and the last line top touches the chin — person composited on top
    # reveals text above head and below chin, creating the "around the face" look.
    n_lines       = max(1, len(headline_lines))
    GAP           = 12
    BOTTOM_MARGIN = 90  # reserved for subtext
    hl_start      = 28
    hl_end        = H - BOTTOM_MARGIN
    avail_h       = hl_end - hl_start
    lines_upper   = list(headline_lines)  # preserve LLM-chosen case

    canvas = Image.new("RGBA", (W, H), (*bg_rgb, 255))
    draw   = ImageDraw.Draw(canvas)

    # Full frame width for all layouts — person composited on top covers the
    # face area naturally (same effect the user likes in center thumbnails).
    # For left/right we shift zone_cx toward the clear side so text is readable.
    zone_x0    = PAD_X
    zone_x1    = W - PAD_X
    max_w_line = zone_x1 - zone_x0

    if face_side == "right":
        # Person on right: shift text center toward the clear left zone
        zone_cx = max(0, px) // 2
    elif face_side == "left":
        # Person on left: shift text center toward the clear right zone
        person_right = max(0, px + person_w)
        zone_cx = (person_right + W) // 2
    else:
        zone_cx = W // 2
    max_h_line = (avail_h - GAP * (n_lines - 1)) // n_lines

    # One font size to rule them all — tightest line across the set wins
    font = _fit_font_group(draw, lines_upper, max_w_line, max_h_line, headline_font)
    rl   = [(ln, draw.textbbox((0, 0), ln, font=font)) for ln in lines_upper]
    line_heights = [bb[3] - bb[1] for _, bb in rl]
    total_h = sum(line_heights) + GAP * (n_lines - 1)

    if face_side == "center" and n_lines >= 2:
        # Anchor: first line bottom → hairline, last line top → chin.
        # If the font is larger than the above zone the line starts at hl_start
        # and overlaps the face area (person on top covers it — that's the look).
        face_top_px = int(face_bbox["face_top"]    * H)
        face_btm_px = int(face_bbox["face_bottom"] * H)

        y_first = max(hl_start, face_top_px - 8 - line_heights[0])
        y_last  = min(hl_end - line_heights[-1], face_btm_px + 8)

        if n_lines == 2:
            y_positions = [y_first, y_last]
        else:  # 3
            mid_h  = line_heights[1]
            y_mid  = max(y_first + line_heights[0] + GAP,
                         min(y_last - mid_h - GAP,
                             (y_first + line_heights[0] + y_last) // 2 - mid_h // 2))
            y_positions = [y_first, y_mid, y_last]
    else:
        # Left / right / single-line center: center the block in the zone
        y0 = hl_start + (avail_h - total_h) // 2
        y_positions = []
        cur = y0
        for h in line_heights:
            y_positions.append(cur)
            cur += h + GAP

    for (ln, bb), y_top in zip(rl, y_positions):
        word_w = bb[2] - bb[0]
        dx     = zone_cx - word_w // 2 - bb[0]
        dx     = max(zone_x0 - bb[0], min(zone_x1 - bb[2], dx))
        draw.text((dx, y_top - bb[1]), ln, font=font, fill=hl_rgb)


    # ── Step 5: composite person on top of headline ────────────────────────────
    if cutout is not None:
        shadow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        shadow = Image.new("RGBA", cutout.size, (0, 0, 0, 110))
        shadow.putalpha(cutout.getchannel("A"))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=18))
        shadow_layer.paste(shadow, (px + 10, py + 10), shadow)
        canvas = Image.alpha_composite(canvas, shadow_layer)
        canvas.paste(cutout, (px, py), cutout)
        draw = ImageDraw.Draw(canvas)

    # ── Step 6: subtext — bottom strip ────────────────────────────────────────
    if subtext:
        sm_size  = 32
        sm_font  = _load_font("subtext", sm_size)
        SM_MAX_W = W - PAD_X * 2

        words_sm = subtext.split()
        sm_lines: list[str] = []
        cur = ""
        for ww in words_sm:
            test = (cur + " " + ww).strip()
            if draw.textlength(test, font=sm_font) <= SM_MAX_W:
                cur = test
            else:
                if cur:
                    sm_lines.append(cur)
                cur = ww
        if cur:
            sm_lines.append(cur)

        LINE_H_SM  = sm_size + 6
        total_sm_h = len(sm_lines) * LINE_H_SM
        ty = H - 20 - total_sm_h
        for sl in sm_lines:
            bb_sm  = draw.textbbox((0, 0), sl, font=sm_font)
            draw.text(
                (PAD_X - bb_sm[0], ty - bb_sm[1]), sl,
                font=sm_font, fill=sub_rgb,
                stroke_width=2, stroke_fill=bg_rgb,
            )
            ty += LINE_H_SM

    # ── Step 7: caveat aside ───────────────────────────────────────────────────
    if caveat_aside:
        cv_font = _load_font("caveat", 44)
        aside   = caveat_aside.lower()
        while True:
            bb_cv = draw.textbbox((0, 0), aside, font=cv_font)
            if (bb_cv[2] - bb_cv[0]) <= W - PAD_X * 2 or len(aside) < 5:
                break
            aside = aside.rsplit(" ", 1)[0]
        bb_cv = draw.textbbox((0, 0), aside, font=cv_font)
        cv_x  = W - PAD_X - (bb_cv[2] - bb_cv[0]) - bb_cv[0]
        cv_x  = max(PAD_X, cv_x)
        cv_y  = H - 90 - (bb_cv[3] - bb_cv[1])
        draw.text(
            (cv_x, cv_y - bb_cv[1]), aside,
            font=cv_font, fill=sub_rgb,
            stroke_width=2, stroke_fill=bg_rgb,
        )

    # ── Step 8: badge ──────────────────────────────────────────────────────────
    if badge and badge.get("text"):
        badge_text = badge["text"].upper()
        # Always pin badge to person side so it never overlaps the text zone
        if face_side == "right":
            pos = "top-right"
        elif face_side == "left":
            pos = "top-left"
        else:
            # Center face: text occupies top+bottom; push badge to a bottom corner
            raw_pos = badge.get("position", "top-right")
            pos = "bottom-" + raw_pos.split("-")[1] if raw_pos.startswith("top-") else raw_pos
        b_font     = _load_font("anton", 20)
        bb_b       = draw.textbbox((0, 0), badge_text, font=b_font)
        b_w    = bb_b[2] - bb_b[0] + 20
        b_h    = bb_b[3] - bb_b[1] + 10
        MARGIN = 18
        if pos == "top-left":
            bx, by = MARGIN, MARGIN
        elif pos == "top-right":
            bx, by = W - MARGIN - b_w, MARGIN
        elif pos == "bottom-left":
            bx, by = MARGIN, H - MARGIN - b_h
        else:
            bx, by = W - MARGIN - b_w, H - MARGIN - b_h

        draw.rectangle([bx, by, bx + b_w, by + b_h], fill=hl_rgb)
        draw.text(
            (bx + 10 - bb_b[0], by + 5 - bb_b[1]),
            badge_text, font=b_font, fill=bg_rgb,
        )

    canvas.convert("RGB").save(out_path, quality=93)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate YouTube thumbnails — science/psychology creator system.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("video_path")
    parser.add_argument("--plan-json", required=True)
    parser.add_argument("--out-dir", default="tmp")
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--save-state", metavar="PATH")
    parser.add_argument("--load-state", metavar="PATH")
    args = parser.parse_args()

    plan       = json.loads(Path(args.plan_json).read_text())
    plan       = plan.get("plan", plan)
    summary    = plan.get("summary", "")
    segments   = plan.get("segments", [])
    duration   = max((s.get("endSec", 0) for s in segments), default=60.0)
    transcript = _extract_transcript(plan, max_chars=4000)
    keywords   = _top_keywords(transcript)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)

    # ── Step 1: concepts ──────────────────────────────────────────────────────
    if args.load_state:
        state    = json.loads(Path(args.load_state).read_text())
        concepts = state["concepts"][: args.count]
        print("[thumbnail] loaded concepts from state", file=sys.stderr)
    else:
        presets_list = ", ".join(BG_PRESETS.keys())
        prompt = f"""You are designing YouTube thumbnails for a tech creator (woman in tech / vlog). Generate exactly {args.count} concepts.

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
- face_timestamp: seconds ({5:.0f}–{duration - 5:.0f})

Make each concept feel like a DIFFERENT angle on the same video."""

        try:
            raw      = generate(prompt, schema=CONCEPTS_SCHEMA)
            concepts = json.loads(raw)["concepts"][: args.count]
            concepts = _fix_concepts(concepts, summary)
        except Exception as e:
            print(f"[thumbnail] LLM failed: {e}", file=sys.stderr)
            sys.exit(1)

    # ── Step 2: save state ────────────────────────────────────────────────────
    if args.save_state:
        Path(args.save_state).write_text(json.dumps({"concepts": concepts}, indent=2))
        print(f"[thumbnail] state → {args.save_state}", file=sys.stderr)

    # ── Step 3: face frames ───────────────────────────────────────────────────
    face_frames: dict[int, str] = {}
    for i, concept in enumerate(concepts):
        frame_path = out_dir / f"face_{i}.jpg"
        if frame_path.exists():
            face_frames[i] = str(frame_path)
        else:
            ts = float(concept.get("face_timestamp", duration * 0.3))
            ts = max(2.0, min(ts, duration - 2.0))
            if extract_frame(args.video_path, ts, str(frame_path)):
                face_frames[i] = str(frame_path)

    # ── Step 4: compose ───────────────────────────────────────────────────────
    results: list[dict] = []
    for i, concept in enumerate(concepts):
        try:
            out_img = str(out_dir / f"thumbnail_{i}.jpg")
            compose(
                face_path=face_frames.get(i),
                bg_preset=concept.get("bg_preset", "red"),
                headline_font=concept.get("headline_font", "anton"),
                headline_lines=concept.get("headline_lines", ["WATCH"]),
                subtext=concept.get("subtext", ""),
                caveat_aside=concept.get("caveat_aside"),
                badge=concept.get("badge"),
                face_side=concept.get("face_side", "right"),
                out_path=out_img,
            )
            img_b64 = base64.b64encode(Path(out_img).read_bytes()).decode()
            results.append({
                "title":     concept.get("title", ""),
                "textHook":  " / ".join(concept.get("headline_lines", [])),
                "imageData": f"data:image/jpeg;base64,{img_b64}",
            })
            print(f"[thumbnail] {i + 1}/{len(concepts)} done", file=sys.stderr)
        except Exception as e:
            print(f"[thumbnail] concept {i} failed: {e}", file=sys.stderr)

    print(json.dumps({"thumbnails": results}))


if __name__ == "__main__":
    main()
