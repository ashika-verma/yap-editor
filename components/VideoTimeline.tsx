"use client";

import React, { useRef, useState, useEffect, useCallback, useMemo } from "react";
import { Overlay, Segment, WordCut } from "@/lib/editPlan";

interface RangeCut {
  segIdx: number;
  srcStart: number;
  srcEnd: number;
}

interface Props {
  segments: Segment[];
  overlays?: Overlay[];
  videoUrl: string;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  onTrim: (index: number, newStart: number, newEnd: number) => void;
  onRangeCut: (ranges: RangeCut[]) => void;
}

type PlaySpan = { startSec: number; endSec: number };

type OutputSpan = {
  sourceStart: number;
  sourceEnd: number;
  outputStart: number;
  outputEnd: number;
  segIdx: number;
  isFirstInSeg: boolean;
  isLastInSeg: boolean;
};

type TrimDrag = {
  segIdx: number;
  edge: "start" | "end";
  originX: number;
  originSec: number;
  timelineWidth: number;
  outputDuration: number;
  zoom: number;
};

type Selection = { frac1: number; frac2: number }; // fractions of outputDuration

function computeKeepSpans(rangeStart: number, rangeEnd: number, wordCuts: WordCut[]): PlaySpan[] {
  const MIN_SPAN = 0.15; // match export route — slivers shorter than this aren't worth seeking to
  const sorted = [...wordCuts].sort((a, b) => a.startSec - b.startSec);
  const spans: PlaySpan[] = [];
  let cursor = rangeStart;
  for (const wc of sorted) {
    if (wc.startSec - cursor >= MIN_SPAN) spans.push({ startSec: cursor, endSec: wc.startSec });
    cursor = Math.max(cursor, wc.endSec);
  }
  if (rangeEnd - cursor >= MIN_SPAN) spans.push({ startSec: cursor, endSec: rangeEnd });
  return spans;
}

function buildOutputSpans(segments: Segment[]): OutputSpan[] {
  const result: OutputSpan[] = [];
  let cursor = 0;
  for (let si = 0; si < segments.length; si++) {
    const seg = segments[si];
    if (!seg.keep) continue;
    const subs = computeKeepSpans(seg.startSec, seg.endSec, seg.wordCuts ?? []);
    subs.forEach((sub, idx) => {
      const dur = sub.endSec - sub.startSec;
      result.push({
        sourceStart: sub.startSec,
        sourceEnd: sub.endSec,
        outputStart: cursor,
        outputEnd: cursor + dur,
        segIdx: si,
        isFirstInSeg: idx === 0,
        isLastInSeg: idx === subs.length - 1,
      });
      cursor += dur;
    });
  }
  return result;
}

function sourceToOutput(t: number, spans: OutputSpan[]): number {
  for (const s of spans) {
    if (t >= s.sourceStart && t <= s.sourceEnd) return s.outputStart + (t - s.sourceStart);
  }
  let best = 0;
  for (const s of spans) { if (s.sourceEnd <= t) best = s.outputEnd; }
  return best;
}

function outputToSource(t: number, spans: OutputSpan[]): number {
  for (const s of spans) {
    if (t >= s.outputStart && t <= s.outputEnd) return s.sourceStart + (t - s.outputStart);
  }
  return spans.at(-1)?.sourceEnd ?? 0;
}

function formatTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  const ms = Math.floor((sec % 1) * 10);
  return `${m}:${String(s).padStart(2, "0")}.${ms}`;
}

export function VideoTimeline({ segments, overlays = [], videoUrl, videoRef, onTrim, onRangeCut }: Props) {
  const timelineRef = useRef<HTMLDivElement>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [trimDrag, setTrimDrag] = useState<TrimDrag | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [zoom, setZoom] = useState(1);
  const [viewStart, setViewStart] = useState(0); // fraction of outputDuration at left edge

  // Refs so event handlers in useEffect closures see current values without resubscribing
  const zoomRef = useRef(zoom);
  const viewStartRef = useRef(viewStart);
  zoomRef.current = zoom;
  viewStartRef.current = viewStart;

  const dragOrigin = useRef<{ clientX: number; frac: number } | null>(null);
  const hasDragged = useRef(false);

  const totalSourceDuration = useMemo(
    () => (segments.length > 0 ? segments[segments.length - 1].endSec : 1),
    [segments],
  );
  const outputSpans = useMemo(() => buildOutputSpans(segments), [segments]);
  const playSpans = useMemo(
    () => outputSpans.map((s) => ({ startSec: s.sourceStart, endSec: s.sourceEnd })),
    [outputSpans],
  );
  const outputDuration = useMemo(() => outputSpans.at(-1)?.outputEnd ?? 0.001, [outputSpans]);

  // Coordinate helpers
  // outputFrac (0–1) → screen left% within the timeline div
  const toScreenPct = useCallback(
    (outputFrac: number) => (outputFrac - viewStart) * zoom * 100,
    [zoom, viewStart],
  );
  // clientX on the timeline → outputFrac (0–1), clamped
  const clientToOutputFrac = useCallback(
    (clientX: number) => {
      const rect = timelineRef.current!.getBoundingClientRect();
      const screenFrac = (clientX - rect.left) / rect.width;
      return Math.max(0, Math.min(1, screenFrac / zoom + viewStart));
    },
    [zoom, viewStart],
  );

  // Keep viewStart valid when zoom decreases (can't scroll past end)
  useEffect(() => {
    setViewStart((vs) => Math.max(0, Math.min(1 - 1 / zoom, vs)));
  }, [zoom]);

  // rAF: sync currentTime + skip cut regions + auto-scroll while playing
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let rafId: number;
    const tick = () => {
      // While the browser is mid-seek, currentTime hasn't updated yet — don't
      // issue another seek or we'll storm the seek queue and stall for seconds.
      if (!video.seeking) {
        const t = video.currentTime;
        setCurrentTime(t);
        if (!video.paused) {
          const inSpan = playSpans.some((s) => t >= s.startSec && t < s.endSec);
          if (!inSpan) {
            const next = playSpans.find((s) => s.startSec > t);
            if (next) video.currentTime = next.startSec;
            else video.pause();
          }
        }
      }
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [playSpans, videoRef]);

  // Auto-scroll to follow playhead while playing
  useEffect(() => {
    if (!isPlaying) return;
    const playFrac = sourceToOutput(currentTime, outputSpans) / outputDuration;
    setViewStart((vs) => {
      const visible = 1 / zoom;
      if (playFrac >= vs && playFrac <= vs + visible * 0.9) return vs;
      return Math.max(0, Math.min(1 - visible, playFrac - visible * 0.1));
    });
  }, [currentTime, isPlaying, zoom, outputSpans, outputDuration]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);
    video.addEventListener("play", onPlay);
    video.addEventListener("pause", onPause);
    return () => {
      video.removeEventListener("play", onPlay);
      video.removeEventListener("pause", onPause);
    };
  }, [videoRef]);

  // Scroll-wheel zoom (centered on mouse cursor position)
  useEffect(() => {
    const tl = timelineRef.current;
    if (!tl) return;
    const onWheel = (e: WheelEvent) => {
      if (e.metaKey) {
        // Cmd+scroll → zoom (only unambiguous zoom gesture in a browser)
        e.preventDefault();
        const rect = tl.getBoundingClientRect();
        const screenFrac = (e.clientX - rect.left) / rect.width;
        const mouseOutputFrac = screenFrac / zoomRef.current + viewStartRef.current;
        const factor = Math.exp(-e.deltaY * 0.002);
        const newZoom = Math.max(1, Math.min(64, zoomRef.current * factor));
        const newViewStart = Math.max(0, Math.min(1 - 1 / newZoom, mouseOutputFrac - screenFrac / newZoom));
        setZoom(newZoom);
        setViewStart(newViewStart);
      } else if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        // Predominantly horizontal swipe → pan (also prevents browser back/forward)
        e.preventDefault();
        const rect = tl.getBoundingClientRect();
        const z = Math.max(1, zoomRef.current);
        const panFrac = (e.deltaX / rect.width / z) * 0.5;
        setViewStart((vs) => Math.max(0, Math.min(1 - 1 / z, vs + panFrac)));
      }
      // Plain vertical scroll → do nothing, pass through to page
    };
    tl.addEventListener("wheel", onWheel, { passive: false });
    return () => tl.removeEventListener("wheel", onWheel);
  }, []);

  const handlePlayPause = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      const t = video.currentTime;
      const lastEnd = playSpans.at(-1)?.endSec ?? 0;
      if (playSpans.length > 0 && t >= lastEnd - 0.15) {
        video.currentTime = playSpans[0].startSec;
      } else {
        const inSpan = playSpans.some((s) => t >= s.startSec && t < s.endSec);
        if (!inSpan) {
          const next = playSpans.find((s) => s.startSec > t) ?? playSpans[0];
          if (next) video.currentTime = next.startSec;
        }
      }
      video.play();
    } else {
      video.pause();
    }
  }, [videoRef, playSpans]);

  const applySelectionCut = useCallback(() => {
    if (!selection) return;
    const outStart = Math.min(selection.frac1, selection.frac2) * outputDuration;
    const outEnd = Math.max(selection.frac1, selection.frac2) * outputDuration;
    if (outEnd - outStart < 0.05) return;
    const bySegIdx = new Map<number, RangeCut>();
    for (const span of outputSpans) {
      if (span.outputEnd <= outStart || span.outputStart >= outEnd) continue;
      const srcStart = span.sourceStart + (Math.max(span.outputStart, outStart) - span.outputStart);
      const srcEnd = span.sourceStart + (Math.min(span.outputEnd, outEnd) - span.outputStart);
      const ex = bySegIdx.get(span.segIdx);
      if (ex) { ex.srcStart = Math.min(ex.srcStart, srcStart); ex.srcEnd = Math.max(ex.srcEnd, srcEnd); }
      else bySegIdx.set(span.segIdx, { segIdx: span.segIdx, srcStart, srcEnd });
    }
    if (bySegIdx.size > 0) onRangeCut([...bySegIdx.values()]);
    setSelection(null);
  }, [selection, outputDuration, outputSpans, onRangeCut]);

  // Global keyboard shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement as HTMLElement | null;
      const inText = el?.tagName === "INPUT" || el?.tagName === "TEXTAREA" || el?.isContentEditable;
      const inTranscript = !!el?.closest("[data-transcript-editor]");
      if (e.code === "Space" && !inText) { e.preventDefault(); handlePlayPause(); }
      if (e.code === "Escape") setSelection(null);
      if ((e.code === "Delete" || e.code === "Backspace") && !inText && !inTranscript && selection) {
        e.preventDefault();
        applySelectionCut();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [handlePlayPause, selection, applySelectionCut]);

  // Trim drag
  const startTrimDrag = useCallback(
    (e: React.MouseEvent, segIdx: number, edge: "start" | "end", originSec: number) => {
      e.preventDefault();
      e.stopPropagation();
      const tl = timelineRef.current;
      if (!tl) return;
      setTrimDrag({ segIdx, edge, originX: e.clientX, originSec, timelineWidth: tl.getBoundingClientRect().width, outputDuration, zoom });
    },
    [outputDuration, zoom],
  );

  useEffect(() => {
    if (!trimDrag) return;
    const { segIdx, edge, originX, originSec, timelineWidth, outputDuration: od, zoom: z } = trimDrag;
    const MIN_DUR = 0.15;
    const onMove = (e: MouseEvent) => {
      const seg = segments[segIdx];
      if (!seg) return;
      // px → output sec (accounts for zoom)
      const dSec = ((e.clientX - originX) / timelineWidth) * (od / z);
      const newSec = originSec + dSec;
      const prevEnd = segIdx > 0 ? segments[segIdx - 1].endSec : 0;
      const nextStart = segIdx < segments.length - 1 ? segments[segIdx + 1].startSec : totalSourceDuration;
      if (edge === "start") {
        onTrim(segIdx, Math.max(prevEnd, Math.min(newSec, seg.endSec - MIN_DUR)), seg.endSec);
      } else {
        onTrim(segIdx, seg.startSec, Math.min(nextStart, Math.max(newSec, seg.startSec + MIN_DUR)));
      }
    };
    const onUp = () => setTrimDrag(null);
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => { document.removeEventListener("mousemove", onMove); document.removeEventListener("mouseup", onUp); };
  }, [trimDrag, segments, totalSourceDuration, onTrim]);

  // Selection drag (via refs to avoid stale closure on zoom/viewStart)
  const handleTimelineMouseDown = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const tl = timelineRef.current;
    if (!tl) return;
    const rect = tl.getBoundingClientRect();
    const screenFrac = (e.clientX - rect.left) / rect.width;
    const frac = Math.max(0, Math.min(1, screenFrac / zoomRef.current + viewStartRef.current));
    dragOrigin.current = { clientX: e.clientX, frac };
    hasDragged.current = false;
  }, []);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragOrigin.current) return;
      if (Math.abs(e.clientX - dragOrigin.current.clientX) < 4) return;
      hasDragged.current = true;
      const tl = timelineRef.current;
      if (!tl) return;
      const rect = tl.getBoundingClientRect();
      const screenFrac = (e.clientX - rect.left) / rect.width;
      const frac2 = Math.max(0, Math.min(1, screenFrac / zoomRef.current + viewStartRef.current));
      setSelection({ frac1: dragOrigin.current.frac, frac2 });
    };
    const onUp = () => { dragOrigin.current = null; };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => { document.removeEventListener("mousemove", onMove); document.removeEventListener("mouseup", onUp); };
  }, []);

  const handleTimelineClick = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (hasDragged.current || trimDrag) return;
    setSelection(null);
    const rect = e.currentTarget.getBoundingClientRect();
    const screenFrac = (e.clientX - rect.left) / rect.width;
    const outputFrac = Math.max(0, Math.min(1, screenFrac / zoom + viewStart));
    const sourceTime = outputToSource(outputFrac * outputDuration, outputSpans);
    if (videoRef.current) { videoRef.current.currentTime = sourceTime; setCurrentTime(sourceTime); }
  }, [trimDrag, zoom, viewStart, outputDuration, outputSpans, videoRef]);

  // Zoom buttons
  const zoomBy = useCallback((factor: number) => {
    setZoom((z) => {
      const newZ = Math.max(1, Math.min(64, z * factor));
      setViewStart((vs) => Math.max(0, Math.min(1 - 1 / newZ, vs)));
      return newZ;
    });
  }, []);

  const playFrac = sourceToOutput(currentTime, outputSpans) / outputDuration;
  const playheadPct = toScreenPct(playFrac);
  const visibleRange = 1 / zoom;

  // Precompute overlay positions. Duration is recomputed from sourceEndSec when
  // present so the timeline marker tracks edits automatically.
  const overlayMarkers = overlays.map((ov) => {
    const outStart = sourceToOutput(ov.sourceAttachSec, outputSpans);
    const outEnd = ov.sourceEndSec != null
      ? sourceToOutput(ov.sourceEndSec, outputSpans)
      : outStart + ov.durationSec;
    return { ov, outStart, outEnd };
  });

  // Which overlays are active at the current output time?
  const outputCurrentTime = playFrac * outputDuration;
  const activeOverlays = overlayMarkers
    .filter(({ outStart, outEnd }) => outputCurrentTime >= outStart && outputCurrentTime < outEnd)
    .map(({ ov }) => ov);

  // Time ruler: 5 ticks spaced evenly across the visible window
  const rulerTicks = Array.from({ length: 5 }, (_, i) => {
    const frac = viewStart + (i / 4) * visibleRange;
    return { pct: i * 25, time: frac * outputDuration };
  });

  const selMinFrac = selection ? Math.min(selection.frac1, selection.frac2) : 0;
  const selMaxFrac = selection ? Math.max(selection.frac1, selection.frac2) : 0;
  const selDuration = (selMaxFrac - selMinFrac) * outputDuration;

  return (
    <div className="flex flex-col gap-4">
      {/* Video player */}
      <div className="relative rounded-xl overflow-hidden" style={{ aspectRatio: "16/9", background: "#000" }}>
        {(() => {
          const splitOverlay = activeOverlays.find(o => o.layout === "split-left" || o.layout === "split-right");
          const sl = splitOverlay?.layout;
          // Split mode: scale(1.35) with a face-aware transform-origin so the
          // speaker lands opposite the graphic. The origin formula mirrors the
          // export's cropX = max(0, min(max, facePx - panelCenter)), keeping
          // edges covered (no black gaps) while placing the face accurately.
          const IMG_W = 0.42, MARGIN = 0.02;
          let videoStyle: React.CSSProperties;
          if (sl) {
            const fx = splitOverlay?.faceCenterX ?? 0.5;
            const targetX = sl === "split-left"
              ? IMG_W + MARGIN + (1 - IMG_W - MARGIN) / 2   // center of right panel
              : (1 - IMG_W - MARGIN) / 2;                    // center of left panel
            // origin that places face exactly at targetX; clamp to [0,100] to
            // guarantee the near edge stays inside the container (no black gap)
            const rawOrigin = (1.35 * fx - targetX) / 0.35 * 100;
            const originX = Math.max(0, Math.min(100, rawOrigin)).toFixed(1);
            // Vertical: anchor at the face's detected y position so zooming
            // keeps the face vertically centered rather than drifting toward
            // the frame center (which can cut off the top of the head).
            const fy = splitOverlay?.faceCenterY ?? 0.4;
            const originY = (fy * 100).toFixed(1);
            videoStyle = {
              width: "100%", height: "100%",
              objectFit: "cover",
              transformOrigin: `${originX}% ${originY}%`,
              transform: "scale(1.35)",
            };
          } else {
            videoStyle = { width: "100%", height: "100%", objectFit: "contain" };
          }
          return <video ref={videoRef} src={videoUrl} style={videoStyle} />;
        })()}

        {/* Live overlay preview: shows the graphic composited over the video */}
        {activeOverlays.map((ov) => {
          const layout = ov.layout ?? "overlay";
          const isSplit = layout === "split-left" || layout === "split-right";
          return isSplit ? (
            // Graphic floats on top of the zoomed video — no black panel.
            // The video (object-cover) fills behind it, showing the room background.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              key={ov.id}
              src={ov.imageUrl}
              alt="overlay preview"
              style={{
                position: "absolute",
                top: "50%",
                transform: "translateY(-50%)",
                [layout === "split-left" ? "left" : "right"]: "2%",
                maxWidth: "42%",
                maxHeight: "90%",
                objectFit: "contain",
                pointerEvents: "none",
                zIndex: 5,
              }}
            />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              key={ov.id}
              src={ov.imageUrl}
              alt="overlay preview"
              style={{
                position: "absolute",
                top: "50%",
                left: "50%",
                transform: "translate(-50%, -50%)",
                maxWidth: "60%",
                maxHeight: "70%",
                objectFit: "contain",
                borderRadius: 6,
                pointerEvents: "none",
                zIndex: 5,
              }}
            />
          );
        })}

        <button
          onClick={handlePlayPause}
          className="absolute inset-0 flex items-center justify-center"
          style={{ background: isPlaying ? "transparent" : "rgba(0,0,0,0.28)", zIndex: 10 }}
        >
          {!isPlaying && (
            <div className="w-14 h-14 rounded-full flex items-center justify-center" style={{ background: "rgba(255,255,255,0.12)", backdropFilter: "blur(10px)", border: "1px solid rgba(255,255,255,0.2)" }}>
              <svg width="18" height="18" viewBox="0 0 18 18" fill="white"><path d="M5 3l11 6-11 6V3z" /></svg>
            </div>
          )}
        </button>
      </div>

      {/* Transport */}
      <div className="flex items-center gap-3">
        <button onClick={handlePlayPause} className="w-7 h-7 rounded-lg flex items-center justify-center border" style={{ borderColor: "var(--border)", background: "var(--card)", color: "var(--foreground)" }}>
          {isPlaying
            ? <svg width="9" height="9" viewBox="0 0 9 9" fill="currentColor"><rect x="0.5" y="0.5" width="3" height="8" rx="0.5" /><rect x="5.5" y="0.5" width="3" height="8" rx="0.5" /></svg>
            : <svg width="9" height="9" viewBox="0 0 9 9" fill="currentColor"><path d="M1.5 0.5l7 4-7 4V0.5z" /></svg>}
        </button>
        <span className="text-xs" style={{ color: "var(--muted-foreground)", fontFamily: "'JetBrains Mono', monospace" }}>
          {formatTime(sourceToOutput(currentTime, outputSpans))}
          <span style={{ opacity: 0.4 }}> / {formatTime(outputDuration)}</span>
        </span>
        {selection && selDuration > 0.05 && (
          <span className="text-xs px-2 py-0.5 rounded" style={{ background: "rgba(99,102,241,0.15)", color: "#818cf8", fontSize: "10px" }}>
            {formatTime(selDuration)} · Delete to cut
          </span>
        )}
        {/* Zoom controls */}
        <div className="ml-auto flex items-center gap-1">
          <button onClick={() => zoomBy(1 / 1.5)} className="w-6 h-6 rounded flex items-center justify-center border text-xs" style={{ borderColor: "var(--border)", background: "var(--card)", color: "var(--muted-foreground)", opacity: zoom <= 1 ? 0.3 : 1 }} disabled={zoom <= 1}>−</button>
          <span className="text-xs w-8 text-center" style={{ color: "var(--muted-foreground)", fontFamily: "'JetBrains Mono', monospace", fontSize: "10px" }}>
            {zoom < 1.5 ? "1×" : zoom < 3 ? "2×" : zoom < 6 ? "4×" : zoom < 12 ? "8×" : zoom < 24 ? "16×" : "32×"}
          </span>
          <button onClick={() => zoomBy(1.5)} className="w-6 h-6 rounded flex items-center justify-center border text-xs" style={{ borderColor: "var(--border)", background: "var(--card)", color: "var(--muted-foreground)", opacity: zoom >= 64 ? 0.3 : 1 }} disabled={zoom >= 64}>+</button>
        </div>
      </div>

      {/* Output timeline */}
      <div className="space-y-1">
        <div
          ref={timelineRef}
          className="relative rounded-lg overflow-hidden select-none"
          style={{ height: 56, background: "var(--card)", border: "1px solid var(--border)", cursor: "crosshair" }}
          onMouseDown={handleTimelineMouseDown}
          onClick={handleTimelineClick}
        >
          {outputSpans.map((span, i) => {
            const leftPct = toScreenPct(span.outputStart / outputDuration);
            const rightPct = toScreenPct(span.outputEnd / outputDuration);
            if (rightPct <= 0 || leftPct >= 100) return null;
            const clampedLeft = Math.max(0, leftPct);
            const clampedRight = Math.min(100, rightPct);
            const isWordCutSeam = !span.isLastInSeg && outputSpans[i + 1]?.segIdx === span.segIdx;
            // Only show left handle if it's in view
            const showLeftHandle = span.isFirstInSeg && leftPct >= -1;
            // Only show right handle if it's in view
            const showRightHandle = span.isLastInSeg && rightPct <= 101;
            return (
              <OutputSpanBlock
                key={i}
                span={span}
                seg={segments[span.segIdx]}
                left={clampedLeft}
                width={clampedRight - clampedLeft}
                hasRightSeam={isWordCutSeam}
                showLeftHandle={showLeftHandle && clampedLeft === leftPct}
                showRightHandle={showRightHandle && clampedRight === rightPct}
                onStartTrimDrag={startTrimDrag}
              />
            );
          })}

          {/* Selection rect */}
          {selection && selDuration > 0.01 && (
            <div
              className="absolute top-0 bottom-0 z-20 pointer-events-none"
              style={{
                left: `${Math.max(0, toScreenPct(selMinFrac))}%`,
                width: `${Math.min(100, toScreenPct(selMaxFrac)) - Math.max(0, toScreenPct(selMinFrac))}%`,
                background: "rgba(99,102,241,0.25)",
                borderLeft: toScreenPct(selMinFrac) >= 0 ? "2px solid #6366f1" : "none",
                borderRight: toScreenPct(selMaxFrac) <= 100 ? "2px solid #6366f1" : "none",
              }}
            />
          )}

          {/* Overlay markers */}
          {overlayMarkers.map(({ ov, outStart, outEnd }) => {
            const pct    = toScreenPct(outStart / outputDuration);
            const endPct = toScreenPct(outEnd   / outputDuration);
            if (endPct <= 0 || pct >= 100) return null;
            return (
              <React.Fragment key={ov.id}>
                {/* Duration band */}
                <div
                  className="absolute top-0 bottom-0 pointer-events-none z-10"
                  style={{
                    left: `${Math.max(0, pct)}%`,
                    width: `${Math.min(100, endPct) - Math.max(0, pct)}%`,
                    background: "rgba(139,92,246,0.18)",
                    borderLeft: pct >= 0 ? "2px solid rgba(139,92,246,0.8)" : "none",
                    borderRight: endPct <= 100 ? "2px solid rgba(139,92,246,0.5)" : "none",
                  }}
                />
                {/* Thumbnail pip above the band */}
                {pct >= 0 && pct <= 100 && (
                  <div
                    className="absolute z-20 pointer-events-none"
                    style={{ left: `${pct}%`, top: 0, transform: "translateX(-50%)" }}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={ov.imageUrl}
                      alt=""
                      style={{ width: 28, height: 18, objectFit: "cover", borderRadius: 3, border: "1.5px solid rgba(139,92,246,0.8)", display: "block" }}
                    />
                  </div>
                )}
              </React.Fragment>
            );
          })}

          {/* Playhead */}
          {playheadPct >= -1 && playheadPct <= 101 && (
            <div className="absolute top-0 bottom-0 z-30 pointer-events-none" style={{ left: `${playheadPct}%`, width: 2, background: "#ffffff", boxShadow: "0 0 6px rgba(255,255,255,0.5)" }}>
              <div className="absolute" style={{ top: -1, left: -4, width: 10, height: 6, background: "#fff", clipPath: "polygon(50% 100%, 0% 0%, 100% 0%)", transform: "rotate(180deg)" }} />
            </div>
          )}
        </div>

        {/* Time ruler (shows the currently visible range) */}
        <div className="relative" style={{ height: 14 }}>
          {rulerTicks.map(({ pct, time }, i) => (
            <span key={i} className="absolute" style={{ left: `${pct}%`, transform: i === 0 ? "none" : i === 4 ? "translateX(-100%)" : "translateX(-50%)", color: "var(--muted-foreground)", fontSize: "9px", fontFamily: "'JetBrains Mono', monospace" }}>
              {formatTime(time)}
            </span>
          ))}
        </div>

        {/* Minimap scrollbar */}
        {zoom > 1 && (
          <div className="relative rounded" style={{ height: 4, background: "rgba(255,255,255,0.06)" }}>
            <div
              className="absolute top-0 bottom-0 rounded"
              style={{
                left: `${viewStart * 100}%`,
                width: `${visibleRange * 100}%`,
                background: "rgba(20,184,166,0.5)",
              }}
            />
          </div>
        )}
      </div>

      <p style={{ color: "var(--muted-foreground)", fontSize: "10px", opacity: 0.55 }}>
        Space play/pause · scroll to zoom · drag to select · Delete to cut · drag edges to trim
      </p>
    </div>
  );
}

function OutputSpanBlock({
  span, seg, left, width, hasRightSeam, showLeftHandle, showRightHandle, onStartTrimDrag,
}: {
  span: OutputSpan;
  seg: Segment;
  left: number;
  width: number;
  hasRightSeam: boolean;
  showLeftHandle: boolean;
  showRightHandle: boolean;
  onStartTrimDrag: (e: React.MouseEvent, segIdx: number, edge: "start" | "end", originSec: number) => void;
}) {
  const HANDLE_W = 5;
  return (
    <div
      className="absolute top-0 bottom-0"
      style={{
        left: `${left}%`,
        width: `${width}%`,
        background: "rgba(20,184,166,0.5)",
        borderRight: hasRightSeam ? "1px solid rgba(0,0,0,0.4)" : "2px solid rgba(0,0,0,0.3)",
      }}
    >
      {showLeftHandle && (
        <div
          className="absolute left-0 top-0 bottom-0 z-10 flex items-center justify-center"
          style={{ width: HANDLE_W, background: "#0d9488", cursor: "col-resize" }}
          onMouseDown={(e) => onStartTrimDrag(e, span.segIdx, "start", span.sourceStart)}
        >
          <div style={{ width: 1, height: 14, background: "rgba(255,255,255,0.55)", borderRadius: 1 }} />
        </div>
      )}
      {showRightHandle && (
        <div
          className="absolute right-0 top-0 bottom-0 z-10 flex items-center justify-center"
          style={{ width: HANDLE_W, background: "#0d9488", cursor: "col-resize" }}
          onMouseDown={(e) => onStartTrimDrag(e, span.segIdx, "end", span.sourceEnd)}
        >
          <div style={{ width: 1, height: 14, background: "rgba(255,255,255,0.55)", borderRadius: 1 }} />
        </div>
      )}
      {span.isFirstInSeg && width > 3 && (
        <div className="absolute bottom-0 left-0 right-0 px-1.5 pb-1 pointer-events-none overflow-hidden">
          <span style={{ fontSize: "8px", color: "rgba(255,255,255,0.6)", whiteSpace: "nowrap", display: "block", overflow: "hidden", textOverflow: "ellipsis" }}>
            {seg.text.slice(0, 50)}
          </span>
        </div>
      )}
    </div>
  );
}
