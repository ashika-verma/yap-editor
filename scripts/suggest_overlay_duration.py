#!/usr/bin/env python3
"""
suggest_overlay_duration.py

Reads JSON from stdin:
  { "sourceAttachSec": float, "segments": [...], "imagePath": str }

Asks the LLM to estimate how long a graphic overlay should stay on screen.
Context is sent in OUTPUT timestamps (post-cut) so the LLM reasons in actual
playback time, not source time — avoiding the "24s because of gaps" problem.

Writes JSON to stdout: {"durationSec": float, "reasoning": str, "source": str}
"""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

_FALLBACK = {"durationSec": 4.0, "reasoning": "", "source": "fallback"}
_MIN_SPAN = 0.15  # match export route


def _word_is_cut(word: dict, word_cuts: list) -> bool:
    ws = float(word.get("start", 0))
    we = float(word.get("end", ws))
    return any(
        ws >= float(wc.get("startSec", 0)) and we <= float(wc.get("endSec", 0))
        for wc in word_cuts
    )


def _build_output_spans(segments: list) -> list[tuple[float, float, float]]:
    """Return (src_start, src_end, out_start) for every kept span after word cuts."""
    spans: list[tuple[float, float, float]] = []
    out_cursor = 0.0
    for seg in sorted(segments, key=lambda s: float(s.get("startSec", 0))):
        if not seg.get("keep", True):
            continue
        seg_start  = float(seg.get("startSec", 0))
        seg_end    = float(seg.get("endSec", 0))
        word_cuts  = sorted(seg.get("wordCuts") or [], key=lambda w: float(w.get("startSec", 0)))
        cursor = seg_start
        for wc in word_cuts:
            wc_s = float(wc.get("startSec", 0))
            wc_e = float(wc.get("endSec", 0))
            if wc_s > cursor and wc_s - cursor >= _MIN_SPAN:
                spans.append((cursor, wc_s, out_cursor))
                out_cursor += wc_s - cursor
            cursor = max(cursor, wc_e)
        if seg_end - cursor >= _MIN_SPAN:
            spans.append((cursor, seg_end, out_cursor))
            out_cursor += seg_end - cursor
    return spans


def _src_to_out(src_t: float, spans: list[tuple[float, float, float]]) -> float:
    for src_s, src_e, out_s in spans:
        if src_t < src_s:
            return out_s          # in a cut gap — snap to next span start
        if src_t <= src_e:
            return out_s + (src_t - src_s)
    if spans:
        src_s, src_e, out_s = spans[-1]
        return out_s + (src_e - src_s)
    return 0.0


def _out_to_src(out_t: float, spans: list[tuple[float, float, float]]) -> float:
    """Reverse map: output time → source time."""
    for src_s, src_e, out_s in spans:
        span_dur = src_e - src_s
        if out_t <= out_s + span_dur:
            return src_s + (out_t - out_s)
    if spans:
        src_s, src_e, out_s = spans[-1]
        return src_e
    return 0.0


def _build_context(segments: list, attach_sec: float,
                   spans: list[tuple[float, float, float]]) -> tuple[str, float]:
    """Build transcript in OUTPUT time. Returns (context_str, attach_out_sec)."""
    attach_out = _src_to_out(attach_sec, spans)
    ctx_out_start = attach_out - 5.0
    ctx_out_end   = attach_out + 90.0
    lines: list[str] = []

    for seg in sorted(segments, key=lambda s: float(s.get("startSec", 0))):
        if not seg.get("keep", True):
            continue
        seg_start = float(seg.get("startSec", 0))
        seg_end   = float(seg.get("endSec", 0))
        word_cuts = seg.get("wordCuts") or []
        words     = seg.get("words") or []

        if words:
            for w in words:
                if _word_is_cut(w, word_cuts):
                    continue
                src_t  = float(w.get("start", seg_start))
                out_t  = _src_to_out(src_t, spans)
                if out_t < ctx_out_start or out_t > ctx_out_end:
                    continue
                m, s = divmod(out_t, 60)
                lines.append(f"[{int(m)}:{s:05.2f}] {w['word']}")
        else:
            out_t = _src_to_out(seg_start, spans)
            if out_t < ctx_out_start or out_t > ctx_out_end:
                continue
            m, s = divmod(out_t, 60)
            lines.append(f"[{int(m)}:{s:05.2f}] {seg.get('text', '').strip()}")

    return "\n".join(lines), attach_out


def main() -> None:
    data        = json.load(sys.stdin)
    attach_sec  = float(data["sourceAttachSec"])
    segments    = data["segments"]
    image_path  = data.get("imagePath", "")

    spans = _build_output_spans(segments)
    context, attach_out = _build_context(segments, attach_sec, spans)

    if not context.strip():
        print(json.dumps({**_FALLBACK, "error": "no transcript context"}))
        return

    m_a, s_a = divmod(attach_out, 60)
    attach_str = f"{int(m_a)}:{s_a:05.2f}"

    has_image  = bool(image_path and os.path.exists(image_path))
    image_note = "The graphic image is attached — use it to understand what concept is being illustrated.\n" if has_image else ""

    prompt = f"""You are helping with video editing. A speaker placed a graphic at playback timestamp {attach_str}.
{image_note}
Transcript in OUTPUT (post-cut) timestamps — gaps and dropped words already removed:
{context}

▶ GRAPHIC PLACED AT: {attach_str}

The graphic illustrates what the speaker is discussing at {attach_str}.
Estimate how long (in seconds of playback time) the graphic should remain on screen.

Look for where the speaker naturally moves on: a completed thought, topic shift, or transition phrase ("anyway", "so", "next", "moving on", etc.).

Rules: minimum 2 s, maximum 30 s. Prefer 5-10 s if unsure.

Return ONLY valid JSON: {{"durationSec": <playback seconds>, "reasoning": "<one sentence: what's being said at attach point and where the topic ends>"}}"""

    print(f"[overlay_duration] prompt:\n{prompt}\n", file=sys.stderr)

    schema = {
        "type": "object",
        "properties": {
            "durationSec": {"type": "number", "minimum": 2, "maximum": 30},
            "reasoning":   {"type": "string"},
        },
        "required": ["durationSec", "reasoning"],
    }

    try:
        from llm import generate, generate_vision
        image_bytes: bytes | None = None
        if has_image:
            with open(image_path, "rb") as f:
                image_bytes = f.read()

        if image_bytes:
            print(f"[overlay_duration] vision API, image {len(image_bytes)//1024}KB", file=sys.stderr)
            raw = generate_vision(prompt=prompt, images=[image_bytes], schema=schema)
        else:
            raw = generate(prompt=prompt, schema=schema)

        if isinstance(raw, str):
            raw = json.loads(raw)
        duration  = max(2.0, min(30.0, float(raw.get("durationSec", 4.0))))
        reasoning = str(raw.get("reasoning", ""))
        # Convert the output end time back to a source timestamp so edits that
        # add/remove content inside the overlay window auto-adjust the duration.
        source_end_sec = _out_to_src(attach_out + duration, spans)
        print(f"[overlay_duration] {duration}s (src {attach_sec:.2f}→{source_end_sec:.2f}) — {reasoning}", file=sys.stderr)
        print(json.dumps({"durationSec": round(duration, 1), "sourceEndSec": round(source_end_sec, 3), "reasoning": reasoning, "source": "ai"}))
    except Exception as exc:
        print(f"[overlay_duration] error: {exc}", file=sys.stderr)
        print(json.dumps({**_FALLBACK, "error": str(exc)}))


if __name__ == "__main__":
    main()
