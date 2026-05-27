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

# ── Accent colors (one pop color per bg preset for highlight words) ───────────

ACCENT_COLORS: dict[str, str] = {
    "yellow":   "#E8538F",  # hot pink on yellow
    "red":      "#FFD93D",  # yellow on red
    "blue":     "#FFD93D",  # yellow on blue
    "pink":     "#FFD93D",  # yellow on pink
    "charcoal": "#FFFFFF",  # white accent on charcoal (yellow headline, white pop)
    "tan":      "#C0392B",  # deep red on tan
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
                    "face_timestamp":  {"type": "number"},
                    "highlight_word":  {"type": ["string", "null"]},
                },
            },
        }
    },
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


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
                line = line.replace("BLOG", "VLOG").replace("Blog", "Vlog").replace("blog", "vlog")
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


def _best_layout(
    draw,
    text: str,
    box_w: int,
    box_h: int,
    font_name: str,
    min_lines: int = 1,
    max_lines: int = 4,
) -> tuple[int, list[str]]:
    """
    Find the line-break arrangement that maximises rendered font size inside box_w × box_h.

    Tries every possible word split from min_lines to min(word_count, max_lines):
      - 1 line:  trivially the full text
      - 2 lines: all (n-1) split points
      - 3 lines: all C(n-1,2) split combos  (capped at n<=12 words; balanced otherwise)
      - 4 lines: balanced split only

    For each arrangement, binary-searches for the largest font where every line fits
    box_w wide and (box_h // n_lines) tall.  Returns (font_size, lines).
    """
    words = text.split()
    n = len(words)
    best_size = 0
    best_lines: list[str] = [text]

    def _score(lines: list[str]) -> int:
        nl = len(lines)
        max_h_per = box_h // nl
        lo, hi, best = 20, 700, 20
        while lo <= hi:
            mid = (lo + hi) // 2
            font = _load_font(font_name, mid)
            ok = all(
                draw.textbbox((0, 0), ln, font=font)[2] <= box_w
                and (draw.textbbox((0, 0), ln, font=font)[3]
                     - draw.textbbox((0, 0), ln, font=font)[1]) <= max_h_per
                for ln in lines
            )
            if ok:
                best = mid; lo = mid + 1
            else:
                hi = mid - 1
        return best

    for nl in range(min_lines, min(n, max_lines) + 1):
        if nl == 1:
            candidates = [[text]]
        elif nl == 2:
            candidates = [
                [" ".join(words[:i]), " ".join(words[i:])]
                for i in range(1, n)
            ]
        elif nl == 3:
            if n <= 12:
                candidates = [
                    [" ".join(words[:i]), " ".join(words[i:j]), " ".join(words[j:])]
                    for i in range(1, n - 1)
                    for j in range(i + 1, n)
                ]
            else:
                c = n // 3
                candidates = [[
                    " ".join(words[:c]),
                    " ".join(words[c:2 * c]),
                    " ".join(words[2 * c:]),
                ]]
        else:
            c = n // nl
            candidates = [[
                " ".join(words[i * c: (i + 1) * c if i < nl - 1 else n])
                for i in range(nl)
            ]]

        for lines in candidates:
            sz = _score(lines)
            if sz > best_size:
                best_size, best_lines = sz, lines

    return best_size, best_lines


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


def _focus_score(img_path: str) -> float:
    """
    Clickbait-frame score: open eyes + general expressiveness.

    Open eyes have bright sclera (whites) — the brightest spots in the face
    zone.  Closed/squinting eyes look like uniform skin tone.  We count very
    bright pixels in the eye strip (center 60% width, top 20–45% of height)
    and combine with a Laplacian sharpness score for the same region so we
    also avoid blurry motion frames.
    """
    try:
        from PIL import Image as _PI
        import numpy as np
        img = _PI.open(img_path).convert("RGB")
        iw, ih = img.size

        # Eye strip: horizontal center 60%, rows 20–45% of frame height
        l  = int(iw * 0.20);  r  = int(iw * 0.80)
        yt = int(ih * 0.20);  yb = int(ih * 0.45)
        eye_rgb = img.crop((l, yt, r, yb))

        # Brightness channel (convert to grayscale)
        eye_l = np.array(eye_rgb.convert("L"), dtype=float)

        # Bright-pixel density: pixels brighter than 180 (sclera is ~220-255)
        bright = float(np.sum(eye_l > 180)) / eye_l.size

        # Laplacian sharpness of the same strip (penalises motion blur)
        lap = eye_l[:-2,1:-1] + eye_l[2:,1:-1] + eye_l[1:-1,:-2] + eye_l[1:-1,2:] - 4*eye_l[1:-1,1:-1]
        sharpness = float(lap.var())

        return bright * 1000 + sharpness * 0.01
    except Exception:
        return 0.0


def _pick_best_frames(video_path: str, out_dir: Path, n: int, duration: float) -> dict[int, str]:
    """
    Global video scan: sample 40 frames spread across the full video, score each
    on face-region expressiveness, then greedily assign the top-scoring distinct
    frames to each of the n concepts.

    'Distinct' means at least 20 s apart so no two concepts share the same shot.
    Returns {concept_index: frame_path} for however many could be filled.
    """
    import os, shutil
    import numpy as np

    N_SAMPLES = 40
    MIN_GAP   = 20.0   # seconds between selected frames

    # Sample evenly, skip first/last 3 s
    times = list(np.linspace(3.0, duration - 3.0, N_SAMPLES))

    tmp_dir = out_dir / "_face_scan"
    tmp_dir.mkdir(exist_ok=True)

    # Extract & score all candidates
    scored: list[tuple[float, float, str]] = []   # (score, time, path)
    for idx, t in enumerate(times):
        p = str(tmp_dir / f"s{idx:03d}.jpg")
        if extract_frame(video_path, t, p):
            score = _focus_score(p)
            scored.append((score, t, p))

    # Sort best-first
    scored.sort(key=lambda x: -x[0])

    # Greedy pick: take the next best frame that is ≥ MIN_GAP from all already chosen
    chosen_times: list[float] = []
    chosen_paths: list[str]   = []
    for score, t, p in scored:
        if all(abs(t - ct) >= MIN_GAP for ct in chosen_times):
            chosen_times.append(t)
            chosen_paths.append(p)
            if len(chosen_paths) == n:
                break

    # Copy winners to face_i.jpg; clean up temp dir
    # Build a time→score lookup for logging
    time_to_score = {t: s for s, t, _ in scored}
    result: dict[int, str] = {}
    for i, src in enumerate(chosen_paths):
        dst = str(out_dir / f"face_{i}.jpg")
        shutil.copy(src, dst)
        result[i] = dst
        t = chosen_times[i]
        print(f"[thumbnail] face {i} @ {t:.1f}s (score={time_to_score.get(t, 0):.1f})", file=sys.stderr)

    try:
        shutil.rmtree(tmp_dir)
    except Exception:
        pass

    return result


def _cover_resize(img, target_w: int, target_h: int):
    """Scale to cover target dimensions while preserving aspect ratio, then center-crop."""
    src_w, src_h = img.size
    scale  = max(target_w / src_w, target_h / src_h)
    new_w  = int(src_w * scale)
    new_h  = int(src_h * scale)
    img    = img.resize((new_w, new_h), 1)  # 1 = LANCZOS
    left   = (new_w - target_w) // 2
    top    = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


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


def _detect_face(img_bytes: bytes, cache_key: str | None = None) -> dict:
    """
    Ask vision LLM to locate the face in the image.
    Returns bbox as 0–1 fractions. Falls back to _DEFAULT_FACE on any failure.
    Uses Gemini; falls back to local Gemma on rate limit (via generate_vision).
    Results are disk-cached by cache_key to avoid repeated LLM calls on re-renders.
    """
    import tempfile
    if cache_key is None:
        import hashlib
        cache_key = hashlib.md5(img_bytes).hexdigest()
    cache_path = Path(tempfile.gettempdir()) / f"thumb_face_bbox_{cache_key}.json"
    if cache_path.exists():
        try:
            bbox = json.loads(cache_path.read_text())
            print("[thumbnail] face bbox (cached)", end=" ", flush=True, file=sys.stderr)
            return bbox
        except Exception:
            pass

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
            try:
                cache_path.write_text(json.dumps(bbox))
            except Exception:
                pass
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
    face_side: str,
    out_path: str,
    face_bbox: dict | None = None,
    highlight_word: str | None = None,
    layout: str = "gap",        # "gap" = face-in-gap | "split" = side-split
) -> None:
    """Dispatch to the requested layout renderer."""
    if layout == "split":
        fn = compose_split
    elif layout == "editorial":
        fn = compose_editorial
    else:
        fn = compose_html
    fn(
        face_path=face_path, bg_preset=bg_preset, headline_font=headline_font,
        headline_lines=headline_lines, subtext=subtext, caveat_aside=caveat_aside,
        badge=badge, face_side=face_side, out_path=out_path,
        highlight_word=highlight_word,
    )


def compose_split(
    face_path: str | None,
    bg_preset: str,
    headline_font: str,
    headline_lines: list[str],
    subtext: str,
    caveat_aside: str | None,
    badge: dict | None,
    face_side: str,
    out_path: str,
    face_bbox: dict | None = None,
    highlight_word: str | None = None,
) -> None:
    """
    Full-bleed photo split layout.

    Original face frame fills the canvas edge-to-edge.  A directional colour tint
    (solid on the text side, fading to transparent toward the face side) gives the
    preset colour identity without hiding the person.  No rembg — natural hair edges.
    Text renders on top with a subtle shadow for legibility.
    """
    import base64 as _b64, io as _io, html as _html, re, tempfile, os
    from PIL import Image as _PIL, ImageDraw as _IDraw

    preset  = BG_PRESETS.get(bg_preset, BG_PRESETS["red"])
    bg_rgb  = preset["bg"];   hl_rgb = preset["headline"]
    acc_hex = ACCENT_COLORS.get(bg_preset, "#FFD93D")
    bg_hex  = _rgb_to_hex(bg_rgb);  hl_hex = _rgb_to_hex(hl_rgb)

    PAD_X    = 44
    GUTTER   = 24
    hl_start = 20
    hl_end   = H - 50
    avail_h  = hl_end - hl_start

    # Text zone gets 56% of canvas; the other 44% is the face/photo zone.
    TEXT_FRAC  = 0.56
    FADE_SPAN  = 20   # gradient fade width in canvas-% units
    if face_side == "right":
        open_x0, open_x1 = PAD_X, int(W * TEXT_FRAC)
    elif face_side == "left":
        open_x0, open_x1 = int(W * (1 - TEXT_FRAC)), W - PAD_X
    else:
        open_x0, open_x1 = PAD_X, W - PAD_X
    zone_w = max(200, open_x1 - open_x0 - GUTTER)

    # ── Full-res original frame as background ─────────────────────────────────
    bg_img_b64 = ""
    if face_path and Path(face_path).exists():
        try:
            _orig = _cover_resize(_PIL.open(face_path).convert("RGB"), W, H)
            _buf  = _io.BytesIO(); _orig.save(_buf, format="JPEG", quality=90)
            bg_img_b64 = _b64.b64encode(_buf.getvalue()).decode()
        except Exception:
            pass

    # ── Directional gradient tint ─────────────────────────────────────────────
    r, g, b     = bg_rgb
    tint_solid  = int(TEXT_FRAC * 100)
    tint_fade   = min(tint_solid + FADE_SPAN, 100)
    if face_side == "right":
        # Text on left → tint flows left-to-right, fading out rightward
        tint_grad = (
            f"linear-gradient(to right,"
            f" rgba({r},{g},{b},0.82) 0%,"
            f" rgba({r},{g},{b},0.82) {tint_solid}%,"
            f" rgba({r},{g},{b},0) {tint_fade}%)"
        )
    elif face_side == "left":
        # Text on right → tint flows right-to-left, fading out leftward
        tint_grad = (
            f"linear-gradient(to left,"
            f" rgba({r},{g},{b},0.82) 0%,"
            f" rgba({r},{g},{b},0.82) {tint_solid}%,"
            f" rgba({r},{g},{b},0) {tint_fade}%)"
        )
    else:
        tint_grad = f"rgba({r},{g},{b},0.75)"

    # ── Font sizing ───────────────────────────────────────────────────────────
    headline_raw = (headline_lines[0].upper() if headline_font == "anton"
                    else headline_lines[0])
    subtext_raw  = headline_lines[1] if len(headline_lines) >= 2 else ""

    _dummy    = _PIL.new("RGB", (W, H));  _draw_tmp = _IDraw.Draw(_dummy)

    hero_size, hero_sublines = _best_layout(
        _draw_tmp, headline_raw, zone_w, int(avail_h * 0.68), headline_font,
        min_lines=1, max_lines=3,
    )
    hero_size = int(hero_size * 0.93)

    sub_size = 0
    if subtext_raw:
        _sub_f   = _fit_font(_draw_tmp, subtext_raw, zone_w, int(avail_h * 0.18), "subtext")
        sub_size = min(int(_sub_f.size * 0.93), max(26, int(hero_size * 0.42)))

    # ── HTML assembly ─────────────────────────────────────────────────────────
    def _markup(text: str) -> str:
        esc = _html.escape(text)
        if not highlight_word: return esc
        return re.sub(re.escape(_html.escape(highlight_word)),
                      lambda m: f'<span class="hw">{m.group()}</span>',
                      esc, count=1, flags=re.IGNORECASE)

    if face_side == "right":    txt_align = "left";  flex_align = "flex-start"
    elif face_side == "left":   txt_align = "right"; flex_align = "flex-end"
    else:                       txt_align = "center"; flex_align = "center"

    hero_divs = "".join(
        f'<div class="hl" style="text-align:{txt_align};width:100%;">{_markup(sl)}</div>'
        for sl in hero_sublines)
    sub_div = (
        f'<div class="sub" style="text-align:{txt_align};width:100%;margin-top:12px;">'
        f'{_html.escape(subtext_raw)}</div>'
        if sub_size else ""
    )

    # Text container positioned within the text zone
    txt_left  = open_x0
    txt_right = W - open_x1
    items_html = (
        f'<div style="position:absolute;top:{hl_start}px;bottom:{H-hl_end}px;'
        f'left:{txt_left}px;right:{txt_right}px;'
        f'display:flex;flex-direction:column;justify-content:center;align-items:{flex_align};'
        f'gap:4px;z-index:2;">{hero_divs}{sub_div}</div>\n')

    bg_img_tag = (
        f'<img style="position:absolute;top:0;left:0;width:{W}px;height:{H}px;'
        f'object-fit:cover;z-index:0;" src="data:image/jpeg;base64,{bg_img_b64}">'
        if bg_img_b64 else ""
    )
    tint_tag = (
        f'<div style="position:absolute;top:0;left:0;right:0;bottom:0;'
        f'background:{tint_grad};z-index:1;"></div>\n'
    )

    fd = Path(__file__).parent / "fonts"
    anton_uri   = (fd / "Anton-Regular.ttf").as_uri()
    fredoka_uri = (fd / "FredokaOne-Regular.ttf").as_uri()
    dmsans_p    = fd / "DMSans-Medium.ttf"
    dmsans_face = (f"@font-face{{font-family:'DMSans';src:url('{dmsans_p.as_uri()}');}}"
                   if dmsans_p.exists() else "")
    sub_ff = "'DMSans',sans-serif" if dmsans_p.exists() else "'Arial',sans-serif"
    hl_ff  = "'Anton'" if headline_font == "anton" else "'FredokaOne'"

    # Text-shadow direction faces the photo zone so letters pop off the gradient edge
    shadow_dir = "right" if face_side == "right" else "left"
    shadow_x   = 3 if shadow_dir == "right" else -3

    doc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
@font-face{{font-family:'Anton';src:url('{anton_uri}');font-display:block;}}
@font-face{{font-family:'FredokaOne';src:url('{fredoka_uri}');font-display:block;}}
{dmsans_face}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{width:{W}px;height:{H}px;overflow:hidden;position:relative;background:{bg_hex};}}
.hl{{font-family:{hl_ff},sans-serif;font-size:{hero_size}px;line-height:1.05;
     color:{hl_hex};white-space:nowrap;
     text-shadow:{shadow_x}px 2px 14px rgba(0,0,0,0.45);}}
.hw{{color:{acc_hex};}}
.sub{{font-family:{sub_ff};font-size:{sub_size}px;font-weight:600;line-height:1.3;
      color:{hl_hex};white-space:nowrap;opacity:0.88;
      text-shadow:0 1px 8px rgba(0,0,0,0.40);}}
</style></head><body>
{bg_img_tag}{tint_tag}{items_html}
</body></html>"""

    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8")
    tmp.write(doc);  tmp.close()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page    = browser.new_page(viewport={"width": W, "height": H})
            page.goto(f"file://{tmp.name}", wait_until="load")
            page.evaluate("() => document.fonts.ready")
            page.screenshot(path=out_path, type="jpeg", quality=93,
                            clip={"x": 0, "y": 0, "width": W, "height": H})
            browser.close()
    finally:
        os.unlink(tmp.name)


def compose_editorial(
    face_path: str | None,
    bg_preset: str,
    headline_font: str,
    headline_lines: list[str],
    subtext: str,
    caveat_aside: str | None,
    badge: dict | None,
    face_side: str,
    out_path: str,
    face_bbox: dict | None = None,
    highlight_word: str | None = None,
) -> None:
    """
    Vogue-style editorial layout.

    Full-bleed centered portrait — no rembg, no tint wash, no cutout.
    The photo is color-graded (contrast + saturation boost, then a very light
    multiply of the preset color).  A bottom scrim creates a reading shelf for
    the headline.  Text lives entirely below the face, never fighting it.
    Person is always assumed to be centered (works for talking-head footage).
    """
    import base64 as _b64, io as _io, html as _html, re, tempfile, os
    from PIL import Image as _PIL, ImageDraw as _IDraw, ImageEnhance as _IEnh, ImageChops as _IChops

    preset  = BG_PRESETS.get(bg_preset, BG_PRESETS["red"])
    bg_rgb  = preset["bg"]
    hl_rgb  = preset["headline"]
    acc_hex = ACCENT_COLORS.get(bg_preset, "#FFD93D")
    bg_hex  = _rgb_to_hex(bg_rgb)
    hl_hex  = _rgb_to_hex(hl_rgb)

    PAD_X   = 44
    PAD_BOT = 32

    # ── Color-graded photo — fitted portrait, not full-bleed ─────────────────
    # Scale photo to 82 % of canvas so the preset color bleeds around all edges.
    # Color-grade only the photo; the background border stays the pure preset.
    PHOTO_SCALE = 0.82
    bg_img_b64  = ""
    if face_path and Path(face_path).exists():
        try:
            raw  = _PIL.open(face_path).convert("RGB")
            rw, rh = raw.size
            ph   = int(H * PHOTO_SCALE)
            pw   = int(W * PHOTO_SCALE)
            scale = min(pw / rw, ph / rh)
            nw, nh = int(rw * scale), int(rh * scale)
            raw  = raw.resize((nw, nh), 1)                # 1 = LANCZOS
            raw  = _IEnh.Contrast(raw).enhance(1.22)
            raw  = _IEnh.Color(raw).enhance(1.28)
            tint   = _PIL.new("RGB", (nw, nh), bg_rgb)
            graded = _IChops.multiply(raw, tint)
            raw    = _PIL.blend(raw, graded, 0.20)
            canvas = _PIL.new("RGB", (W, H), bg_rgb)
            ox = (W - nw) // 2
            oy = (H - nh) // 2
            canvas.paste(raw, (ox, oy))
            _buf = _io.BytesIO(); canvas.save(_buf, format="JPEG", quality=90)
            bg_img_b64 = _b64.b64encode(_buf.getvalue()).decode()
        except Exception:
            pass

    # ── Text zone: lower third ────────────────────────────────────────────────
    # Headline starts at 58 % from top; subtext sits between headline and bottom.
    HL_TOP  = int(H * 0.58)
    HL_BOT  = H - PAD_BOT
    zone_w  = W - 2 * PAD_X
    zone_h  = HL_BOT - HL_TOP   # budget for headline block

    # ── Font sizing ───────────────────────────────────────────────────────────
    headline_raw = (headline_lines[0].upper() if headline_font == "anton"
                    else headline_lines[0])
    subtext_raw  = headline_lines[1] if len(headline_lines) >= 2 else ""

    _dummy    = _PIL.new("RGB", (W, H)); _draw_tmp = _IDraw.Draw(_dummy)

    hero_size, hero_sublines = _best_layout(
        _draw_tmp, headline_raw, zone_w, int(zone_h * 0.80), headline_font,
        min_lines=1, max_lines=2,
    )
    hero_size = int(hero_size * 0.93)

    sub_size = 0
    if subtext_raw:
        _sub_f   = _fit_font(_draw_tmp, subtext_raw, zone_w, int(zone_h * 0.22), "subtext")
        sub_size = min(int(_sub_f.size * 0.93), max(24, int(hero_size * 0.36)))

    # ── Badge / kicker (top-left, editorial category label) ──────────────────
    kicker_text = ""
    if badge and badge.get("text"):
        kicker_text = badge["text"].upper()

    # ── Markup ────────────────────────────────────────────────────────────────
    def _markup(text: str) -> str:
        esc = _html.escape(text)
        if not highlight_word: return esc
        return re.sub(re.escape(_html.escape(highlight_word)),
                      lambda m: f'<span class="hw">{m.group()}</span>',
                      esc, count=1, flags=re.IGNORECASE)

    hero_divs = "".join(
        f'<div class="hl">{_markup(sl)}</div>'
        for sl in hero_sublines)
    sub_div = (
        f'<div class="sub">{_html.escape(subtext_raw)}</div>'
        if sub_size else ""
    )
    kicker_div = (
        f'<div class="kicker">{_html.escape(kicker_text)}</div>'
        if kicker_text else ""
    )

    # Bottom-anchored text block
    items_html = (
        f'<div style="position:absolute;bottom:{PAD_BOT}px;'
        f'left:{PAD_X}px;right:{PAD_X}px;'
        f'display:flex;flex-direction:column;align-items:flex-start;'
        f'gap:6px;z-index:2;">'
        f'{hero_divs}{sub_div}</div>\n'
    )
    kicker_html = (
        f'<div style="position:absolute;top:28px;left:{PAD_X}px;z-index:2;">'
        f'{kicker_div}</div>\n'
        if kicker_div else ""
    )

    # ── Scrim: preset color at bottom (text shelf), transparent at top ──────────
    r, g, b = bg_rgb
    scrim_html = (
        f'<div style="position:absolute;top:0;left:0;right:0;bottom:0;'
        f'background:linear-gradient(to top,'
        f'rgba({r},{g},{b},0.97) 0%,'
        f'rgba({r},{g},{b},0.94) 16%,'
        f'rgba({r},{g},{b},0.55) 38%,'
        f'rgba({r},{g},{b},0) 58%);'
        f'z-index:1;"></div>\n'
    )

    bg_img_tag = (
        f'<img style="position:absolute;top:0;left:0;width:{W}px;height:{H}px;'
        f'object-fit:cover;z-index:0;" src="data:image/jpeg;base64,{bg_img_b64}">'
        if bg_img_b64 else ""
    )

    fd = Path(__file__).parent / "fonts"
    anton_uri   = (fd / "Anton-Regular.ttf").as_uri()
    fredoka_uri = (fd / "FredokaOne-Regular.ttf").as_uri()
    dmsans_p    = fd / "DMSans-Medium.ttf"
    dmsans_face = (f"@font-face{{font-family:'DMSans';src:url('{dmsans_p.as_uri()}');}}"
                   if dmsans_p.exists() else "")
    sub_ff = "'DMSans',sans-serif" if dmsans_p.exists() else "'Arial',sans-serif"
    hl_ff  = "'Anton'" if headline_font == "anton" else "'FredokaOne'"
    # Use preset headline color so yellow/tan get dark text
    sub_r, sub_g, sub_b = hl_rgb

    doc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
@font-face{{font-family:'Anton';src:url('{anton_uri}');font-display:block;}}
@font-face{{font-family:'FredokaOne';src:url('{fredoka_uri}');font-display:block;}}
{dmsans_face}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{width:{W}px;height:{H}px;overflow:hidden;position:relative;background:{bg_hex};}}
.hl{{font-family:{hl_ff},sans-serif;font-size:{hero_size}px;line-height:1.05;
     color:{hl_hex};white-space:nowrap;
     text-shadow:0 2px 20px rgba({r},{g},{b},0.4);}}
.hw{{color:{acc_hex};}}
.sub{{font-family:{sub_ff};font-size:{sub_size}px;font-weight:600;line-height:1.3;
      color:rgba({sub_r},{sub_g},{sub_b},0.75);white-space:nowrap;}}
.kicker{{font-family:{sub_ff};font-size:16px;font-weight:700;letter-spacing:0.14em;
         text-transform:uppercase;color:rgba({sub_r},{sub_g},{sub_b},0.72);}}
</style></head><body>
{bg_img_tag}{scrim_html}{kicker_html}{items_html}
</body></html>"""

    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8")
    tmp.write(doc); tmp.close()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page    = browser.new_page(viewport={"width": W, "height": H})
            page.goto(f"file://{tmp.name}", wait_until="load")
            page.evaluate("() => document.fonts.ready")
            page.screenshot(path=out_path, type="jpeg", quality=93,
                            clip={"x": 0, "y": 0, "width": W, "height": H})
            browser.close()
    finally:
        os.unlink(tmp.name)


def compose_html(
    face_path: str | None,
    bg_preset: str,
    headline_font: str,
    headline_lines: list[str],
    subtext: str,
    caveat_aside: str | None,
    badge: dict | None,
    face_side: str,
    out_path: str,
    face_bbox: dict | None = None,
    highlight_word: str | None = None,
) -> None:
    """
    Face-in-gap layout: two massive headline lines bracket the face.

    headline_lines[0] sits above the face, headline_lines[1] below.
    Person is narrow (38% W) so text peeks out on both sides.
    Font is sized to fill canvas width, then capped so both lines + face fit vertically.
    """
    import base64 as _b64, io as _io, html as _html, re, tempfile, os
    from PIL import Image as _PIL, ImageDraw as _IDraw
    import numpy as _np

    preset  = BG_PRESETS.get(bg_preset, BG_PRESETS["red"])
    bg_rgb  = preset["bg"]; hl_rgb = preset["headline"]
    acc_hex = ACCENT_COLORS.get(bg_preset, "#FFD93D")
    bg_hex  = _rgb_to_hex(bg_rgb); hl_hex = _rgb_to_hex(hl_rgb)

    PAD_X        = 12               # narrow side padding → text fills most of canvas
    GAP          = 18               # breathing room between text edge and face
    TOP_MARGIN   = 14
    BOT_MARGIN   = 20
    MAX_PERSON_W = int(W * 0.38)    # narrower person → text visible on both sides
    person_h     = int(H * 1.05)

    # ── Person cutout (rembg + center crop) ────────────────────────────────────
    cutout_b64 = ""; cutout_pil = None; person_w = MAX_PERSON_W
    if face_path and Path(face_path).exists():
        raw = _remove_bg(face_path)
        if raw is not None:
            target_w = int(raw.width * (person_h / raw.height))
            resized  = raw.resize((target_w, person_h), _PIL.LANCZOS)
            if target_w > MAX_PERSON_W:
                crop_x = (target_w - MAX_PERSON_W) // 2
                cutout_pil = resized.crop((crop_x, 0, crop_x + MAX_PERSON_W, person_h))
            else:
                cutout_pil = resized
            person_w = cutout_pil.width
            buf = _io.BytesIO(); cutout_pil.save(buf, format="PNG")
            cutout_b64 = _b64.b64encode(buf.getvalue()).decode()

    # ── Alpha scan: head top + chin in resized image space ────────────────────
    head_top_row = 0
    chin_row     = int(person_h * 0.28)   # fallback
    if cutout_pil is not None:
        try:
            _rows = _np.where(_np.array(cutout_pil)[:, :, 3].max(axis=1) > 30)[0]
            if len(_rows) > 20:
                head_top_row = int(_rows[0])
                body_btm_row = int(_rows[-1])
                chin_row     = head_top_row + int((body_btm_row - head_top_row) * 0.30)
        except Exception:
            pass
    face_height = max(60, chin_row - head_top_row)

    # ── Best 2-line split: try every word boundary, pick the split that fits ─────
    # the available height (above + below the face) at the largest font size.
    zone_w    = W - 2 * PAD_X
    avail_txt = H - TOP_MARGIN - BOT_MARGIN - 2 * GAP - face_height
    # box_h for _best_layout covers both lines; max_h_per = avail_txt // 2
    full_text = " ".join(
        ln.upper() if headline_font == "anton" else ln
        for ln in headline_lines
    )
    _dummy    = _PIL.new("RGB", (W, H)); _draw_tmp = _IDraw.Draw(_dummy)
    hero_size, gap_lines = _best_layout(
        _draw_tmp, full_text, zone_w, max(100, avail_txt), headline_font,
        min_lines=2, max_lines=2,
    )
    hero_size  = int(hero_size * 0.93)
    line_above = gap_lines[0]
    line_below = gap_lines[1] if len(gap_lines) >= 2 else None

    # Actual rendered text height (same for both lines — same font & size)
    _font  = _load_font(headline_font, hero_size)
    _bb    = _draw_tmp.textbbox((0, 0), line_above, font=_font)
    line_h = _bb[3] - _bb[1]

    # ── Vertical geometry — center {line1, gap, line2} block in canvas ──────────
    total_h   = line_h + 2 * GAP + face_height + (line_h if line_below else 0)
    line1_top = max(8, (H - total_h) // 2)
    line1_btm = line1_top + line_h
    line2_top = line1_btm + 2 * GAP + face_height
    line2_btm = (line2_top + line_h) if line_below else line2_top

    # If block overflows bottom, shift up (font is already capped so this is rare)
    if line2_btm > H - 8:
        shift     = line2_btm - (H - 8)
        line1_top = max(4, line1_top - shift)
        line1_btm = line1_top + line_h
        line2_top = line1_btm + 2 * GAP + face_height
        line2_btm = (line2_top + line_h) if line_below else line2_top

    # Person: align head_top_row with gap top (line1_btm + GAP)
    person_top = max(0, (line1_btm + GAP) - head_top_row)
    px_center  = (W - person_w) // 2

    # ── Accent markup ──────────────────────────────────────────────────────────
    def _markup(text: str) -> str:
        esc = _html.escape(text)
        if not highlight_word: return esc
        return re.sub(re.escape(_html.escape(highlight_word)),
                      lambda m: f'<span class="hw">{m.group()}</span>',
                      esc, count=1, flags=re.IGNORECASE)

    # ── HTML ──────────────────────────────────────────────────────────────────
    # Both .hl divs at z-index:1; person at z-index:2 composites over them.
    # Text extends edge-to-edge beyond the person width so both lines are readable.
    items_html = (
        f'<div class="hl" style="position:absolute;top:{line1_top}px;'
        f'left:{PAD_X}px;right:{PAD_X}px;">{_markup(line_above)}</div>\n'
    )
    if line_below:
        items_html += (
            f'<div class="hl" style="position:absolute;top:{line2_top}px;'
            f'left:{PAD_X}px;right:{PAD_X}px;">{_markup(line_below)}</div>\n'
        )

    person_tag = (
        f'<img class="person" style="left:{px_center}px;top:{person_top}px;"'
        f' src="data:image/png;base64,{cutout_b64}">'
        if cutout_b64 else ""
    )

    # ── Font URIs (local TTF files — fully offline) ─────────────────────────────
    fd          = Path(__file__).parent / "fonts"
    anton_uri   = (fd / "Anton-Regular.ttf").as_uri()
    fredoka_uri = (fd / "FredokaOne-Regular.ttf").as_uri()
    dmsans_p    = fd / "DMSans-Medium.ttf"
    dmsans_face = (f"@font-face{{font-family:'DMSans';src:url('{dmsans_p.as_uri()}');}}"
                   if dmsans_p.exists() else "")
    hl_ff = "'Anton'" if headline_font == "anton" else "'FredokaOne'"

    doc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
@font-face{{font-family:'Anton';src:url('{anton_uri}');font-display:block;}}
@font-face{{font-family:'FredokaOne';src:url('{fredoka_uri}');font-display:block;}}
{dmsans_face}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{width:{W}px;height:{H}px;overflow:hidden;position:relative;background:{bg_hex};}}
.hl{{
  font-family:{hl_ff},sans-serif;
  font-size:{hero_size}px;
  line-height:1.0;
  color:{hl_hex};
  text-align:center;
  white-space:nowrap;
  z-index:1;
}}
.hw{{color:{acc_hex};}}
.person{{
  position:absolute;
  height:{person_h}px;
  width:auto;
  z-index:2;
  filter:drop-shadow(2px 4px 12px rgba(0,0,0,0.40));
}}
</style></head>
<body>
{items_html}{person_tag}
</body></html>"""

    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8")
    tmp.write(doc); tmp.close()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page    = browser.new_page(viewport={"width": W, "height": H})
            page.goto(f"file://{tmp.name}", wait_until="load")
            page.evaluate("() => document.fonts.ready")
            page.screenshot(path=out_path, type="jpeg", quality=93,
                           clip={"x": 0, "y": 0, "width": W, "height": H})
            browser.close()
    finally:
        os.unlink(tmp.name)


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
    parser.add_argument("--layout", default="gap", choices=["gap", "split", "editorial"])
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
    # Global scan: 40 candidates across the full video → pick best N distinct.
    print(f"[thumbnail] scanning {duration:.0f}s video for best {len(concepts)} frames…", file=sys.stderr)
    face_frames = _pick_best_frames(args.video_path, out_dir, len(concepts), duration)

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
                highlight_word=concept.get("highlight_word"),
                layout=args.layout,
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
