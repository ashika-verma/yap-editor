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
    fn = compose_split if layout == "split" else compose_html
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
    HTML/CSS split layout — person bleeds off one edge, text owns the opposite zone.
    Hero auto-split into ≤2-word sub-lines so each line is as large as possible.
    Directional flex alignment anchors text to the open side.
    """
    import base64 as _b64, io as _io, html as _html, re, tempfile, os
    from PIL import Image as _PIL, ImageDraw as _IDraw

    preset  = BG_PRESETS.get(bg_preset, BG_PRESETS["red"])
    bg_rgb  = preset["bg"];   hl_rgb = preset["headline"]
    acc_hex = ACCENT_COLORS.get(bg_preset, "#FFD93D")
    bg_hex  = _rgb_to_hex(bg_rgb);  hl_hex = _rgb_to_hex(hl_rgb)

    PAD_X        = 36
    BLEED        = int(W * 0.18)
    MAX_PERSON_W = int(W * 0.55)
    person_h     = int(H * 0.97)

    cutout_b64 = ""
    person_w   = MAX_PERSON_W
    if face_path and Path(face_path).exists():
        raw = _remove_bg(face_path)
        if raw is not None:
            target_w = int(raw.width * (person_h / raw.height))
            resized  = raw.resize((target_w, person_h), _PIL.LANCZOS)
            if target_w > MAX_PERSON_W:
                if face_side == "left":    crop_x = 0
                elif face_side == "right": crop_x = target_w - MAX_PERSON_W
                else:                      crop_x = (target_w - MAX_PERSON_W) // 2
                cutout = resized.crop((crop_x, 0, crop_x + MAX_PERSON_W, person_h))
            else:
                cutout = resized
            person_w = cutout.width
            buf = _io.BytesIO();  cutout.save(buf, format="PNG")
            cutout_b64 = _b64.b64encode(buf.getvalue()).decode()

    if face_side == "left":    px = -BLEED
    elif face_side == "right": px = W - person_w + BLEED
    else:                      px = (W - person_w) // 2

    GUTTER   = 20
    hl_start = 20
    hl_end   = H - 50
    avail_h  = hl_end - hl_start

    if face_side == "left":
        open_x0 = min(W - PAD_X, max(PAD_X, px + person_w + GUTTER))
        open_x1 = W - PAD_X
    elif face_side == "right":
        open_x0 = PAD_X
        open_x1 = max(PAD_X, min(W - PAD_X, px - GUTTER))
    else:
        open_x0 = PAD_X;  open_x1 = W - PAD_X

    zone_x0 = PAD_X;  zone_x1 = W - PAD_X
    zone_w  = max(200, open_x1 - open_x0)

    n_lines     = max(1, len(headline_lines))
    lines_upper = ([ln.upper() for ln in headline_lines]
                   if headline_font == "anton" else list(headline_lines))
    _dummy    = _PIL.new("RGB", (W, H));  _draw_tmp = _IDraw.Draw(_dummy)

    def _auto_split(text: str, max_words: int = 2) -> list[str]:
        words = text.split()
        if len(words) <= max_words: return [text]
        mid = (len(words) + 1) // 2
        return [" ".join(words[:mid]), " ".join(words[mid:])]

    hero_raw      = lines_upper[0]
    hero_sublines = _auto_split(hero_raw)
    _hero_f       = _fit_font_group(_draw_tmp, hero_sublines, zone_w, int(avail_h * 0.40), headline_font)
    hero_size     = int(_hero_f.size * 0.93)

    support_display = "";  support_size = 0
    if n_lines >= 2:
        support_display = " ".join(headline_lines[1:])
        _sup_f       = _fit_font(_draw_tmp, support_display, zone_w, int(avail_h * 0.18), "subtext")
        support_size = min(int(_sup_f.size * 0.93), max(28, int(hero_size * 0.40)))

    def _markup(text: str) -> str:
        esc = _html.escape(text)
        if not highlight_word: return esc
        return re.sub(re.escape(_html.escape(highlight_word)),
                      lambda m: f'<span class="hw">{m.group()}</span>',
                      esc, count=1, flags=re.IGNORECASE)

    if face_side == "right":    txt_align = "left";   flex_align = "flex-start"
    elif face_side == "left":   txt_align = "right";  flex_align = "flex-end"
    else:                       txt_align = "center";  flex_align = "center"

    hero_divs = "".join(
        f'<div class="hl" style="text-align:{txt_align};width:100%;">{_markup(sl)}</div>'
        for sl in hero_sublines)
    sup_inner = (f'<div class="sup" style="text-align:{txt_align};width:100%;">'
                 f'{_html.escape(support_display)}</div>'
                 if support_size and support_display else "")
    items_html = (
        f'<div style="position:absolute;top:{hl_start}px;bottom:{H-hl_end}px;'
        f'left:{zone_x0}px;right:{W-zone_x1}px;'
        f'display:flex;flex-direction:column;justify-content:center;align-items:{flex_align};'
        f'gap:6px;z-index:1;">{hero_divs}{sup_inner}</div>\n')

    person_tag = (f'<img class="person" style="left:{px}px;" src="data:image/png;base64,{cutout_b64}">'
                  if cutout_b64 else "")

    fd = Path(__file__).parent / "fonts"
    anton_uri   = (fd / "Anton-Regular.ttf").as_uri()
    fredoka_uri = (fd / "FredokaOne-Regular.ttf").as_uri()
    caveat_uri  = (fd / "Caveat-Regular.ttf").as_uri()
    dmsans_p    = fd / "DMSans-Medium.ttf"
    dmsans_face = (f"@font-face{{font-family:'DMSans';src:url('{dmsans_p.as_uri()}');}}"
                   if dmsans_p.exists() else "")
    sub_ff = "'DMSans',sans-serif" if dmsans_p.exists() else "'Arial',sans-serif"
    hl_ff  = "'Anton'" if headline_font == "anton" else "'FredokaOne'"

    doc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
@font-face{{font-family:'Anton';src:url('{anton_uri}');font-display:block;}}
@font-face{{font-family:'FredokaOne';src:url('{fredoka_uri}');font-display:block;}}
{dmsans_face}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{width:{W}px;height:{H}px;overflow:hidden;position:relative;background:{bg_hex};}}
.hl{{font-family:{hl_ff},sans-serif;font-size:{hero_size}px;line-height:1.02;
     color:{hl_hex};white-space:nowrap;}}
.hw{{color:{acc_hex};}}
.sup{{font-family:{sub_ff};font-size:{support_size}px;font-style:italic;font-weight:600;
      line-height:1.15;color:{hl_hex};white-space:nowrap;opacity:0.85;}}
.person{{position:absolute;bottom:0;height:{person_h}px;width:auto;z-index:2;
         filter:drop-shadow(2px 4px 10px rgba(0,0,0,0.35));}}
</style></head><body>
{items_html}{person_tag}
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
    HTML/CSS + Playwright renderer — reference-style split layout.

    Layout: person owns one side (bleed off edge), text owns the other side.
    Text block is flexbox-vertically-centred in its zone, as large as it fits.
    For center face: text brackets top + bottom (person fills the middle).

    Visual: paint-order stroke fill (outlined text like reference), no glow.
    Accent word gets a contrasting fill colour, same stroke.
    """
    import base64 as _b64, io as _io, html as _html, re, tempfile, os
    from PIL import Image as _PIL, ImageDraw as _IDraw

    preset  = BG_PRESETS.get(bg_preset, BG_PRESETS["red"])
    bg_rgb  = preset["bg"];   hl_rgb = preset["headline"];   sub_rgb = preset["subtext"]
    acc_hex = ACCENT_COLORS.get(bg_preset, "#FFD93D")
    bg_hex  = _rgb_to_hex(bg_rgb)
    hl_hex  = _rgb_to_hex(hl_rgb)
    sub_hex = _rgb_to_hex(sub_rgb)

    PAD_X        = 36
    MAX_PERSON_W = int(W * 0.46)   # narrower so side-text is readable behind body
    person_h     = int(H * 1.05)   # taller so feet go off-screen naturally

    # ── Person cutout (rembg + center crop) ────────────────────────────────────
    cutout_b64  = ""
    cutout_pil  = None
    person_w    = MAX_PERSON_W
    if face_path and Path(face_path).exists():
        raw = _remove_bg(face_path)
        if raw is not None:
            target_w = int(raw.width * (person_h / raw.height))
            resized  = raw.resize((target_w, person_h), _PIL.LANCZOS)
            if target_w > MAX_PERSON_W:
                crop_x = (target_w - MAX_PERSON_W) // 2   # center crop for face-in-gap
                cutout_pil = resized.crop((crop_x, 0, crop_x + MAX_PERSON_W, person_h))
            else:
                cutout_pil = resized
            person_w = cutout_pil.width
            buf = _io.BytesIO()
            cutout_pil.save(buf, format="PNG")
            cutout_b64 = _b64.b64encode(buf.getvalue()).decode()

    # ── Face Y bounds via numpy alpha scan (no LLM) ─────────────────────────────
    # Scan the rembg alpha channel to find the top of the hair (head_top_row)
    # and estimate the chin (28% of visible body height below the hair).
    # All rows are in CUTOUT pixel space; convert to canvas space below.
    import numpy as _np

    head_top_row = 0
    chin_row     = int(person_h * 0.30)   # fallback

    if cutout_pil is not None:
        try:
            _arr   = _np.array(cutout_pil)
            _alpha = _arr[:, :, 3]
            _rows  = _np.where(_alpha.max(axis=1) > 30)[0]
            if len(_rows) > 20:
                head_top_row = int(_rows[0])
                body_btm_row = int(_rows[-1])
                chin_row     = head_top_row + int((body_btm_row - head_top_row) * 0.38)
        except Exception:
            pass

    # Shift person DOWN so the top of the hair lands at TARGET_HEAD_Y.
    # Optimal value balances top_avail (TARGET_HEAD_Y - GAP - TOP_MARGIN) with
    # bot_avail ((H - BOT_MARGIN) - chin_canvas - GAP). Given chin ≈ 38% of 756px
    # below hair top, the equal-zone optimum is ≈ 28% of frame height.
    TARGET_HEAD_Y = int(H * 0.28)          # hair top at 28% from frame top
    person_top    = max(0, TARGET_HEAD_Y - head_top_row)

    # Canvas-space face coordinates
    face_top_canvas = person_top + head_top_row      # top of hair
    face_btm_canvas = person_top + chin_row           # bottom of chin

    # ── Gap geometry ─────────────────────────────────────────────────────────────
    GAP          = 28    # breathing room between text and face edge (pixels)
    TOP_MARGIN   = 14
    BOT_MARGIN   = 48

    line1_btm = face_top_canvas - GAP            # bottom edge of above-head text
    line2_top = face_btm_canvas + GAP            # top edge of below-chin text

    line1_btm = max(TOP_MARGIN + 30, line1_btm)
    line2_top = min(H - BOT_MARGIN - 30, line2_top)

    # ── Font sizing — same size for both gap lines ───────────────────────────────
    n_lines     = max(1, len(headline_lines))
    zone_w      = W - 2 * PAD_X             # full frame width for text
    lines_upper = ([ln.upper() for ln in headline_lines]
                   if headline_font == "anton" else list(headline_lines))

    _dummy    = _PIL.new("RGB", (W, H))
    _draw_tmp = _IDraw.Draw(_dummy)

    # Split hero: above-face gets the ceiling half so single-word phrases
    # (e.g. "I") don't end up alone above — at least 2 words go above when possible.
    hero_raw  = lines_upper[0]
    _words    = hero_raw.split()
    _mid      = max(1, (len(_words) + 1) // 2)   # round UP → above gets ≥ half
    line_above = " ".join(_words[:_mid])
    line_below = " ".join(_words[_mid:]) if len(_words) > 1 else hero_raw

    top_avail = max(20, line1_btm - TOP_MARGIN)
    bot_avail = max(20, (H - BOT_MARGIN) - line2_top)
    avail_h   = min(top_avail, bot_avail)

    _hero_f   = _fit_font_group(_draw_tmp, [line_above, line_below], zone_w, avail_h, headline_font)
    hero_size = int(_hero_f.size * 0.93)

    # Support text drops below line_below; size whatever height remains
    support_display = ""
    support_size    = 0
    if n_lines >= 2:
        support_display = " ".join(headline_lines[1:])
        sup_top         = line2_top + int(hero_size * 1.05) + 8
        sup_avail_h     = max(10, (H - BOT_MARGIN) - sup_top)
        if sup_avail_h > 18:
            _sup_f       = _fit_font(_draw_tmp, support_display, zone_w, sup_avail_h, "subtext")
            support_size = min(int(_sup_f.size * 0.93), max(22, int(hero_size * 0.32)))

    # ── Accent word markup ──────────────────────────────────────────────────────
    def _markup(text: str) -> str:
        esc = _html.escape(text)
        if not highlight_word:
            return esc
        return re.sub(
            re.escape(_html.escape(highlight_word)),
            lambda m: f'<span class="hw">{m.group()}</span>',
            esc, count=1, flags=re.IGNORECASE,
        )

    # ── Layout: line_above bottom-pinned to gap top, line_below top-pinned to gap bottom
    # Both text layers sit at z-index:1 so the person (z-index:2) composites on top —
    # the face/hair clips into the whitespace between the two lines.
    sup_top = line2_top + int(hero_size * 1.05) + 8
    sup_html = ""
    if support_size and support_display and sup_top < H - BOT_MARGIN:
        sup_html = (
            f'<div class="sup" style="position:absolute;'
            f'top:{sup_top}px;left:{PAD_X}px;right:{PAD_X}px;">'
            f'{_html.escape(support_display)}</div>'
        )

    items_html = (
        f'<div class="hl" style="position:absolute;'
        f'bottom:{H - line1_btm}px;left:{PAD_X}px;right:{PAD_X}px;">'
        f'{_markup(line_above)}</div>\n'
        f'<div class="hl" style="position:absolute;'
        f'top:{line2_top}px;left:{PAD_X}px;right:{PAD_X}px;">'
        f'{_markup(line_below)}</div>\n'
        f'{sup_html}\n'
    )

    # ── Extras ─────────────────────────────────────────────────────────────────
    px_center   = (W - person_w) // 2
    person_tag  = (f'<img class="person"'
                   f' style="left:{px_center}px;top:{person_top}px;"'
                   f' src="data:image/png;base64,{cutout_b64}">'
                   if cutout_b64 else "")
    subtext_tag = (f'<div class="sub">{_html.escape(subtext)}</div>'
                   if subtext else "")
    caveat_tag  = (f'<div class="cav">{_html.escape(caveat_aside.lower())}</div>'
                   if caveat_aside else "")

    badge_tag = ""
    if badge and badge.get("text"):
        bpos_map = {
            "top-left":     "top:18px;left:18px;",
            "top-right":    "top:18px;right:18px;",
            "bottom-left":  "bottom:70px;left:18px;",
            "bottom-right": "bottom:70px;right:18px;",
        }
        bpos = bpos_map.get(badge.get("position", "top-right"), "top:18px;right:18px;")
        badge_tag = (
            f'<div class="bdg" style="{bpos}background:{hl_hex};color:{bg_hex};">'
            f'{_html.escape(badge["text"].upper())}</div>'
        )

    # ── Font URIs (local TTF files — fully offline) ─────────────────────────────
    fd          = Path(__file__).parent / "fonts"
    anton_uri   = (fd / "Anton-Regular.ttf").as_uri()
    fredoka_uri = (fd / "FredokaOne-Regular.ttf").as_uri()
    caveat_uri  = (fd / "Caveat-Regular.ttf").as_uri()
    dmsans_p    = fd / "DMSans-Medium.ttf"
    dmsans_face = (f"@font-face{{font-family:'DMSans';src:url('{dmsans_p.as_uri()}');}}"
                   if dmsans_p.exists() else "")
    sub_ff = "'DMSans',sans-serif" if dmsans_p.exists() else "'Arial',sans-serif"
    hl_ff  = "'Anton'" if headline_font == "anton" else "'FredokaOne'"

    # ── HTML document ──────────────────────────────────────────────────────────
    doc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
@font-face{{font-family:'Anton';src:url('{anton_uri}');font-display:block;}}
@font-face{{font-family:'FredokaOne';src:url('{fredoka_uri}');font-display:block;}}
@font-face{{font-family:'Caveat';src:url('{caveat_uri}');font-display:block;}}
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
.sup{{
  font-family:{sub_ff};
  font-size:{support_size}px;
  font-style:italic;
  font-weight:600;
  line-height:1.15;
  color:{hl_hex};
  text-align:center;
  white-space:nowrap;
  opacity:0.85;
  z-index:1;
}}
.person{{
  position:absolute;
  height:{person_h}px;
  width:auto;
  z-index:2;
  filter:drop-shadow(2px 4px 12px rgba(0,0,0,0.40));
}}
.sub{{display:none;}}
.cav{{display:none;}}
.bdg{{display:none;}}
</style></head>
<body>
{items_html}{person_tag}
{subtext_tag}{caveat_tag}{badge_tag}
</body></html>"""

    # ── Playwright render ──────────────────────────────────────────────────────
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
