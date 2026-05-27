"use client";

import { useState, useRef, useCallback, useEffect, Fragment } from "react";
import {
  buildCleanTranscript,
  FillerSensitivity,
  JudgeItem,
  JudgeResult,
  NarrativeAnalysis,
  Segment,
  WordCut,
  WordTimestamp,
  wordCutId,
} from "@/lib/editPlan";

interface Props {
  segments: Segment[];
  summary: string;
  rationale?: string;
  lowConfidence?: boolean;
  narrativeAnalysis?: NarrativeAnalysis;
  totalDuration: string;
  keptCount: number;
  keptSeconds: number;
  videoUrl: string | null;
  fillerSensitivity: FillerSensitivity;
  isReplanning?: boolean;
  isJudging?: boolean;
  isRefining?: boolean;
  refineIterations?: number;
  judgeResult?: JudgeResult | null;
  onToggle: (index: number) => void;
  onToggleWordCut: (segmentIndex: number, wordIndex: number, range: boolean) => void;
  onCutRange: (segIdx: number, startWordIdx: number, endWordIdx: number) => void;
  onResetToGemini: () => void;
  onToggleAll: (keep: boolean) => void;
  onSensitivityChange: (s: FillerSensitivity) => void;
  onJudge?: () => void;
  onRefine?: () => void;
}

const DROP_REASON_LABELS: Record<string, { label: string; color: string; bg: string }> = {
  filler:       { label: "filler",       color: "#f59e0b", bg: "rgba(245,158,11,0.1)"  },
  repetition:   { label: "repeat",       color: "#a78bfa", bg: "rgba(167,139,250,0.1)" },
  "off-topic":  { label: "off-topic",    color: "#f97316", bg: "rgba(249,115,22,0.1)"  },
  manual:       { label: "manual",       color: "#38bdf8", bg: "rgba(56,189,248,0.1)"  },
  ramble:       { label: "ramble",       color: "#fb923c", bg: "rgba(251,146,60,0.1)"  },
  tangent:      { label: "tangent",      color: "#818cf8", bg: "rgba(129,140,248,0.12)" },
  circular:     { label: "circular",     color: "#c084fc", bg: "rgba(192,132,252,0.12)" },
  superseded:   { label: "superseded",   color: "#94a3b8", bg: "rgba(148,163,184,0.1)"  },
  false_start:  { label: "false start",  color: "#64748b", bg: "rgba(100,116,139,0.1)"  },
};

function formatSeconds(sec: number) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// ── Word-level cut helpers ─────────────────────────────────────────────────────

function findWordCut(seg: Segment, word: WordTimestamp, wordIndex: number): WordCut | undefined {
  const id = wordCutId(seg, wordIndex);
  return (seg.wordCuts ?? []).find(
    (cut) => cut.id === id || (word.start >= cut.startSec && word.end <= cut.endSec),
  );
}

function findWordSpan(node: Node | null): HTMLElement | null {
  let el = (node instanceof HTMLElement ? node : node?.parentElement) ?? null;
  while (el) {
    if (el.dataset.seg !== undefined && el.dataset.word !== undefined) return el;
    el = el.parentElement;
  }
  return null;
}

function getSelectionWordPairs(
  container: HTMLElement,
  segments: Segment[],
): { segIdx: number; wordIdx: number }[] | null {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
  const anchorSpan = findWordSpan(sel.anchorNode);
  const focusSpan  = findWordSpan(sel.focusNode);
  if (!anchorSpan || !focusSpan) return null;
  const allSpans = Array.from(container.querySelectorAll<HTMLElement>("[data-seg][data-word]"));
  const ai = allSpans.indexOf(anchorSpan);
  const fi = allSpans.indexOf(focusSpan);
  if (ai === -1 || fi === -1) return null;
  const [lo, hi] = ai <= fi ? [ai, fi] : [fi, ai];
  return allSpans.slice(lo, hi + 1).flatMap((span) => {
    const segIdx  = parseInt(span.dataset.seg  ?? "", 10);
    const wordIdx = parseInt(span.dataset.word ?? "", 10);
    if (isNaN(segIdx) || isNaN(wordIdx)) return [];
    if (!segments[segIdx]?.keep) return [];     // dropped segments can't take word cuts
    return [{ segIdx, wordIdx }];
  });
}

// ── Main component ─────────────────────────────────────────────────────────────

// Each undo entry is one cut action, which may span one or more segments
type UndoRange = { segIdx: number; startWordIdx: number; endWordIdx: number };

export function TranscriptEditor({
  segments,
  summary,
  rationale,
  lowConfidence,
  narrativeAnalysis,
  totalDuration,
  keptCount,
  keptSeconds,
  videoUrl,
  fillerSensitivity,
  isReplanning = false,
  isJudging = false,
  isRefining = false,
  refineIterations = 0,
  judgeResult = null,
  onToggle,
  onToggleWordCut,
  onCutRange,
  onResetToGemini,
  onToggleAll,
  onSensitivityChange,
  onJudge,
  onRefine,
}: Props) {
  const [showVideo, setShowVideo] = useState(false);
  const [copied, setCopied]       = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const [selPairs, setSelPairs]   = useState<{ segIdx: number; wordIdx: number }[] | null>(null);
  const [undoStack, setUndoStack] = useState<UndoRange[][]>([]);

  // selectionchange fires on every selection change (mouse AND keyboard navigation).
  // This replaces the old mouseUp handler and enables Shift+arrow extension.
  useEffect(() => {
    const handleSelChange = () => {
      const container = containerRef.current;
      if (!container) return;
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
        setSelPairs(null);
        return;
      }
      const range = sel.getRangeAt(0);
      const ancestor = range.commonAncestorContainer;
      if (!(ancestor instanceof Node) || !container.contains(ancestor)) {
        setSelPairs(null);
        return;
      }
      const pairs = getSelectionWordPairs(container, segments);
      if (pairs && pairs.length > 0) {
        setSelPairs(pairs);
        // Ensure the container has keyboard focus so Backspace/Cmd+Z fire here
        if (document.activeElement !== container) {
          container.focus({ preventScroll: true });
        }
      } else {
        setSelPairs(null);
      }
    };
    document.addEventListener("selectionchange", handleSelChange);
    return () => document.removeEventListener("selectionchange", handleSelChange);
  }, [segments]);

  const handleCopyTranscript = () => {
    const text = buildCleanTranscript(segments);
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const totalSegments  = segments.length;
  const totalSeconds   = segments.reduce((a, s) => a + (s.endSec - s.startSec), 0);
  const savedSeconds   = totalSeconds - keptSeconds;
  const keptPct        = totalSeconds > 0 ? Math.round((keptSeconds / totalSeconds) * 100) : 0;
  const riskyKept      = segments.filter((s) => s.keep && s.jobRisk);
  const riskyCount     = riskyKept.length;

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key === "Escape") {
        setSelPairs(null);
        window.getSelection()?.removeAllRanges();
        return;
      }
      if (e.key === "Backspace" && selPairs && selPairs.length > 0) {
        e.preventDefault();
        // Group selected words by segment, then create one spanning cut per segment
        const bySegment = new Map<number, number[]>();
        for (const { segIdx, wordIdx } of selPairs) {
          if (!bySegment.has(segIdx)) bySegment.set(segIdx, []);
          bySegment.get(segIdx)!.push(wordIdx);
        }
        const undoEntry: UndoRange[] = [];
        for (const [segIdx, wordIndices] of bySegment) {
          const min = Math.min(...wordIndices);
          const max = Math.max(...wordIndices);
          undoEntry.push({ segIdx, startWordIdx: min, endWordIdx: max });
          onCutRange(segIdx, min, max);
        }
        setUndoStack((prev) => [...prev.slice(-19), undoEntry]);
        setSelPairs(null);
        window.getSelection()?.removeAllRanges();
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "z" && !e.shiftKey && undoStack.length > 0) {
        e.preventDefault();
        const last = undoStack[undoStack.length - 1];
        for (const { segIdx, startWordIdx, endWordIdx } of last) {
          onCutRange(segIdx, startWordIdx, endWordIdx);
        }
        setUndoStack((prev) => prev.slice(0, -1));
        return;
      }
    },
    [selPairs, undoStack, onCutRange],
  );

  return (
    <div className="space-y-6">
      {/* Summary card */}
      <div
        className="rounded-xl border p-5 space-y-3 animate-fade-in-up-delay-1"
        style={{ borderColor: "rgba(99,102,241,0.2)", background: "rgba(99,102,241,0.04)" }}
      >
        <div className="flex items-start gap-3">
          <div
            className="mt-0.5 w-5 h-5 rounded flex items-center justify-center flex-shrink-0"
            style={{ background: "rgba(99,102,241,0.15)" }}
          >
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
              <path d="M1 5h8M5 1l4 4-4 4" stroke="var(--primary)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <div className="flex-1 min-w-0 space-y-2">
            <p className="text-xs font-medium" style={{ color: "var(--primary)", fontFamily: "'Syne', sans-serif", letterSpacing: "0.05em", textTransform: "uppercase" }}>
              Gemini&apos;s narrative read
            </p>
            {narrativeAnalysis?.coreStory && (
              <div className="space-y-0.5">
                <p className="text-xs" style={{ color: "var(--muted-foreground)", fontSize: "10px", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                  Anchor story
                </p>
                <p className="text-sm font-medium leading-snug" style={{ color: "var(--foreground)" }}>
                  {narrativeAnalysis.coreStory}
                </p>
              </div>
            )}
            <p className="text-sm leading-relaxed" style={{ color: "var(--foreground)", opacity: 0.75 }}>
              {summary}
            </p>
            {rationale && (
              <p className="text-xs leading-relaxed" style={{ color: "var(--muted-foreground)", fontStyle: "italic" }}>
                {rationale}
              </p>
            )}
            {(narrativeAnalysis?.tangents?.length || narrativeAnalysis?.repetitionGroups?.length || narrativeAnalysis?.circularSections?.length) ? (
              <div className="flex flex-wrap gap-1.5 pt-0.5">
                {narrativeAnalysis.tangents?.map((t, i) => (
                  <span key={`t${i}`} className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full"
                    style={{ background: "rgba(129,140,248,0.12)", color: "#818cf8", fontFamily: "'JetBrains Mono', monospace", fontSize: "10px" }}
                    title={`Tangent cut: ${t.segIndices.length} segments`}
                  >
                    ↗ {t.label}
                  </span>
                ))}
                {narrativeAnalysis.circularSections?.map((c, i) => (
                  <span key={`c${i}`} className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full"
                    style={{ background: "rgba(192,132,252,0.12)", color: "#c084fc", fontFamily: "'JetBrains Mono', monospace", fontSize: "10px" }}
                    title={`Circular section: ${c.segIndices.length} segments`}
                  >
                    ↺ {c.label}
                  </span>
                ))}
                {narrativeAnalysis.repetitionGroups?.map((r, i) => (
                  <span key={`r${i}`} className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full"
                    style={{ background: "rgba(148,163,184,0.1)", color: "#94a3b8", fontFamily: "'JetBrains Mono', monospace", fontSize: "10px" }}
                    title={`Repetition: ${r.duplicateIndices.length} duplicates cut`}
                  >
                    ⟳ {r.topic}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {/* Low-confidence banner */}
      {lowConfidence && (
        <div
          className="rounded-xl border p-4 flex items-start gap-3"
          style={{ borderColor: "rgba(245,158,11,0.3)", background: "rgba(245,158,11,0.06)" }}
        >
          <svg className="flex-shrink-0 mt-0.5" width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M8 1.5L14.5 13H1.5L8 1.5Z" stroke="#f59e0b" strokeWidth="1.4" strokeLinejoin="round"/>
            <line x1="8" y1="6" x2="8" y2="9.5" stroke="#f59e0b" strokeWidth="1.4" strokeLinecap="round"/>
            <circle cx="8" cy="11.5" r="0.6" fill="#f59e0b"/>
          </svg>
          <div className="flex-1 min-w-0">
            <p className="text-xs font-semibold mb-0.5" style={{ color: "#f59e0b" }}>
              Auto-edit was low-confidence — transcript left intact
            </p>
            <p className="text-xs leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
              The AI couldn&apos;t produce a confident edit. Review and cut manually, or try replanning.
            </p>
          </div>
        </div>
      )}

      {/* Job risk banner */}
      {riskyCount > 0 && (
        <div
          className="rounded-xl border p-4 flex items-start gap-3"
          style={{ borderColor: "rgba(239,68,68,0.3)", background: "rgba(239,68,68,0.06)" }}
        >
          <svg className="flex-shrink-0 mt-0.5" width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M8 1.5L14.5 13H1.5L8 1.5Z" stroke="#ef4444" strokeWidth="1.4" strokeLinejoin="round"/>
            <line x1="8" y1="6" x2="8" y2="9.5" stroke="#ef4444" strokeWidth="1.4" strokeLinecap="round"/>
            <circle cx="8" cy="11.5" r="0.6" fill="#ef4444"/>
          </svg>
          <div className="flex-1 min-w-0">
            <p className="text-xs font-semibold mb-0.5" style={{ color: "#ef4444" }}>
              {riskyCount} kept segment{riskyCount > 1 ? "s" : ""} may be professionally risky
            </p>
            <p className="text-xs leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
              Flagged content is marked with a red left-border in the document below. Review before exporting.
            </p>
          </div>
        </div>
      )}

      {/* Stats bar */}
      <div
        className="rounded-xl border p-4 animate-fade-in-up-delay-2"
        style={{ borderColor: "var(--border)", background: "var(--card)" }}
      >
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
          <Stat label="Original" value={totalDuration} mono />
          <Stat label="Edited" value={formatSeconds(keptSeconds)} mono accent />
          <Stat label="Cut" value={`${formatSeconds(savedSeconds)} saved`} mono dim />
          <Stat label="Segments" value={`${keptCount} / ${totalSegments} kept`} mono />
        </div>

        {/* Timeline bar — click segments to toggle keep/drop */}
        <div className="space-y-1.5">
          <div className="flex h-3 rounded-full overflow-hidden gap-px" style={{ background: "var(--secondary)" }}>
            {segments.map((seg, i) => {
              const width = ((seg.endSec - seg.startSec) / totalSeconds) * 100;
              return (
                <button
                  key={i}
                  title={`${seg.keep ? "kept" : "dropped"}: ${seg.text.slice(0, 60)}`}
                  onClick={() => onToggle(i)}
                  className="h-full transition-opacity hover:opacity-80"
                  style={{
                    width: `${width}%`,
                    minWidth: 2,
                    background: seg.keep ? "var(--keep)" : "var(--drop)",
                    opacity: seg.keep ? 0.8 : 0.3,
                  }}
                />
              );
            })}
          </div>
          <div className="flex justify-between text-xs" style={{ color: "var(--muted-foreground)", fontFamily: "'JetBrains Mono', monospace" }}>
            <span>0:00</span>
            <span className="text-xs" style={{ color: "var(--muted-foreground)", fontSize: "10px" }}>click bar segments to toggle keep/drop</span>
            <span>{totalDuration}</span>
          </div>
        </div>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-2 animate-fade-in-up-delay-3">
        {/* Filler sensitivity */}
        <div className="flex items-center gap-1.5">
          <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>fillers:</span>
          <div className="flex rounded-lg overflow-hidden border text-xs" style={{ borderColor: "var(--border)" }}>
            {(["conservative", "balanced", "aggressive"] as const).map((s, i, arr) => (
              <button
                key={s}
                onClick={() => onSensitivityChange(s)}
                disabled={isReplanning}
                className="px-2.5 py-1.5 capitalize transition-colors duration-100"
                style={{
                  background: fillerSensitivity === s ? "rgba(245,158,11,0.12)" : "transparent",
                  color: fillerSensitivity === s ? "#f59e0b" : "var(--muted-foreground)",
                  borderRight: i < arr.length - 1 ? "1px solid var(--border)" : "none",
                  opacity: isReplanning ? 0.6 : 1,
                }}
              >
                {s === "conservative" ? "low" : s === "balanced" ? "mid" : "high"}
              </button>
            ))}
          </div>
          {isReplanning && (
            <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>updating…</span>
          )}
        </div>

        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => onToggleAll(true)}
            className="text-xs px-3 py-1.5 rounded-lg border transition-colors"
            style={{ borderColor: "var(--border)", color: "var(--muted-foreground)", background: "transparent" }}
            onMouseEnter={(e) => { (e.currentTarget).style.color = "var(--foreground)"; }}
            onMouseLeave={(e) => { (e.currentTarget).style.color = "var(--muted-foreground)"; }}
          >
            Keep all
          </button>
          <button
            onClick={() => onToggleAll(false)}
            className="text-xs px-3 py-1.5 rounded-lg border transition-colors"
            style={{ borderColor: "var(--border)", color: "var(--muted-foreground)", background: "transparent" }}
            onMouseEnter={(e) => { (e.currentTarget).style.color = "var(--foreground)"; }}
            onMouseLeave={(e) => { (e.currentTarget).style.color = "var(--muted-foreground)"; }}
          >
            Drop all
          </button>
          <button
            onClick={handleCopyTranscript}
            className="text-xs px-3 py-1.5 rounded-lg border transition-all flex items-center gap-1.5"
            style={{
              borderColor: copied ? "rgba(34,197,94,0.4)" : "var(--border)",
              color: copied ? "#22c55e" : "var(--muted-foreground)",
              background: copied ? "rgba(34,197,94,0.06)" : "transparent",
            }}
          >
            {copied ? (
              <>
                <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                  <path d="M2 5l2.5 2.5 4-4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
                Copied
              </>
            ) : (
              <>
                <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                  <rect x="3.5" y="1" width="5.5" height="7" rx="1" stroke="currentColor" strokeWidth="1.2"/>
                  <path d="M2 3H1.5A.5.5 0 001 3.5v5A.5.5 0 001.5 9h5a.5.5 0 00.5-.5V8" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
                </svg>
                Copy transcript
              </>
            )}
          </button>
          <button
            onClick={onResetToGemini}
            className="text-xs px-3 py-1.5 rounded-lg border transition-colors flex items-center gap-1.5"
            style={{ borderColor: "rgba(99,102,241,0.3)", color: "var(--primary)", background: "rgba(99,102,241,0.06)" }}
          >
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
              <path d="M1.5 5A3.5 3.5 0 105 1.5M1.5 1.5V5H5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            Gemini&apos;s picks
          </button>

          {onJudge && (
            <button
              onClick={onJudge}
              disabled={isJudging || isRefining}
              className="text-xs px-3 py-1.5 rounded-lg border transition-colors flex items-center gap-1.5"
              style={{
                borderColor: isJudging ? "rgba(234,179,8,0.4)" : "rgba(234,179,8,0.3)",
                color: isJudging ? "#eab308" : "#ca8a04",
                background: isJudging ? "rgba(234,179,8,0.08)" : "rgba(234,179,8,0.04)",
                opacity: isRefining ? 0.5 : 1,
              }}
            >
              {isJudging ? (
                <><div className="spinner" style={{ width: 8, height: 8, borderWidth: 1.2 }} />judging…</>
              ) : (
                <>
                  <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                    <path d="M5 1v4l2.5 1.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
                    <circle cx="5" cy="5" r="4" stroke="currentColor" strokeWidth="1.2"/>
                  </svg>
                  Judge
                </>
              )}
            </button>
          )}

          {onRefine && (
            <button
              onClick={onRefine}
              disabled={isJudging || isRefining}
              className="text-xs px-3 py-1.5 rounded-lg border transition-colors flex items-center gap-1.5"
              style={{
                borderColor: isRefining ? "rgba(16,185,129,0.4)" : "rgba(16,185,129,0.3)",
                color: isRefining ? "#10b981" : "#059669",
                background: isRefining ? "rgba(16,185,129,0.08)" : "rgba(16,185,129,0.04)",
                opacity: isJudging ? 0.5 : 1,
              }}
            >
              {isRefining ? (
                <><div className="spinner" style={{ width: 8, height: 8, borderWidth: 1.2 }} />refining{refineIterations > 0 ? ` (${refineIterations})` : "…"}</>
              ) : (
                <>
                  <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                    <path d="M1 5a4 4 0 104-4M1 1v4h4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/>
                    <circle cx="5" cy="5" r="1.2" fill="currentColor"/>
                  </svg>
                  Auto-refine
                </>
              )}
            </button>
          )}
        </div>
      </div>

      {/* Judge results panel */}
      {judgeResult && (
        <JudgePanel result={judgeResult} segments={segments} onToggle={onToggle} />
      )}

      {/* Video preview toggle */}
      {videoUrl && (
        <div className="animate-fade-in-up-delay-3">
          <button
            onClick={() => setShowVideo((v) => !v)}
            className="flex items-center gap-2 text-xs mb-2 transition-colors"
            style={{ color: "var(--muted-foreground)" }}
            onMouseEnter={(e) => { (e.currentTarget).style.color = "var(--foreground)"; }}
            onMouseLeave={(e) => { (e.currentTarget).style.color = "var(--muted-foreground)"; }}
          >
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none" style={{ transform: showVideo ? "rotate(90deg)" : "rotate(0)", transition: "transform 0.2s" }}>
              <path d="M3 2l4 3-4 3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            {showVideo ? "Hide" : "Show"} original video
          </button>
          {showVideo && (
            <video
              src={videoUrl}
              controls
              className="w-full max-w-lg rounded-xl border"
              style={{ borderColor: "var(--border)", background: "#000" }}
            />
          )}
        </div>
      )}

      {/* Document view */}
      <div className="rounded-xl border overflow-hidden" style={{ borderColor: "var(--border)" }}>
        <div
          ref={containerRef}
          tabIndex={0}
          onKeyDown={handleKeyDown}
          className="focus:outline-none"
          style={{
            fontSize: "14px",
            lineHeight: 1.9,
            padding: "28px 32px",
            background: "var(--card)",
            maxHeight: "65vh",
            overflowY: "auto",
            cursor: "text",
            userSelect: "text",
          }}
        >
          {segments.map((seg, si) => {
            const isDrop     = !seg.keep;
            const hasJobRisk = Boolean(seg.keep && seg.jobRisk);
            const reasonMeta = seg.dropReason ? DROP_REASON_LABELS[seg.dropReason] : null;

            if (isDrop) {
              return (
                <div key={si} style={{ marginBottom: "0.5em" }}>
                  <span
                    style={{
                      opacity: 0.28,
                      textDecorationLine: "line-through",
                      textDecorationColor: "rgba(239,68,68,0.5)",
                      color: "#ef4444",
                      cursor: "pointer",
                      userSelect: "none",
                    }}
                    onClick={() => onToggle(si)}
                    title={`${reasonMeta?.label ?? "dropped"} — click to restore`}
                  >
                    {seg.text}
                  </span>
                  {reasonMeta && (
                    <span
                      className="ml-2 text-xs px-1.5 py-0.5 rounded"
                      style={{
                        background: reasonMeta.bg,
                        color: reasonMeta.color,
                        fontFamily: "'JetBrains Mono', monospace",
                        fontSize: "10px",
                        opacity: 0.6,
                        userSelect: "none",
                      }}
                    >
                      {reasonMeta.label}
                    </span>
                  )}
                </div>
              );
            }

            // Kept segment
            return (
              <div
                key={si}
                style={{
                  marginBottom: "0.6em",
                  borderLeft: hasJobRisk
                    ? "2px solid rgba(239,68,68,0.45)"
                    : "2px solid transparent",
                  paddingLeft: hasJobRisk ? "10px" : "0",
                }}
                title={hasJobRisk ? `⚠ Job risk: ${seg.jobRisk}` : undefined}
              >
                {!seg.words?.length ? (
                  <span>{seg.text}</span>
                ) : (
                  seg.words.map((w, wi) => {
                    const cut      = findWordCut(seg, w, wi);
                    const isCut    = Boolean(cut);
                    const isManual = cut?.source === "manual";
                    const isTrim   = cut?.source === "trim";
                    const cutColor = isManual ? "#38bdf8" : isTrim ? "#ef4444" : "#f59e0b";
                    const cutColorAlpha = isManual ? "rgba(56,189,248,0.5)" : isTrim ? "rgba(239,68,68,0.5)" : "rgba(245,158,11,0.5)";
                    return (
                      // Space is a separate text node BEFORE the span so that
                      // browser selection anchored in the space does not
                      // include the preceding word span.
                      <Fragment key={wi}>
                        {wi > 0 && " "}
                        <span
                          data-seg={String(si)}
                          data-word={String(wi)}
                          style={{
                            color: isCut ? cutColor : "inherit",
                            textDecorationLine: isCut ? "line-through" : "none",
                            textDecorationColor: isCut ? cutColorAlpha : "transparent",
                            opacity: isCut ? 0.6 : 1,
                          }}
                        >
                          {w.word}
                        </span>
                      </Fragment>
                    );
                  })
                )}
              </div>
            );
          })}
        </div>

        {/* Footer — shows selection hint when words are selected, stats otherwise */}
        <div
          className="px-5 py-3 flex items-center justify-between border-t"
          style={{ borderColor: "var(--border)", background: "var(--card)" }}
        >
          {selPairs && selPairs.length > 0 ? (
            <span className="text-xs" style={{ color: "#38bdf8" }}>
              {selPairs.length} word{selPairs.length !== 1 ? "s" : ""} selected
              <span style={{ color: "var(--muted-foreground)" }}>
                {" "}· Backspace to cut · Esc to clear
              </span>
            </span>
          ) : (
            <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>
              {keptCount} of {totalSegments} kept · {keptPct}% of runtime
              {undoStack.length > 0 && (
                <> · <span style={{ color: "#38bdf8" }}>Cmd+Z</span> to undo {undoStack.length} cut{undoStack.length > 1 ? "s" : ""}</>
              )}
            </span>
          )}
          <span className="text-xs" style={{ color: "var(--keep)", fontFamily: "'JetBrains Mono', monospace" }}>
            {formatSeconds(keptSeconds)} edited
          </span>
        </div>
      </div>
    </div>
  );
}

// ── Subcomponents ──────────────────────────────────────────────────────────────

function Stat({
  label,
  value,
  mono,
  accent,
  dim,
}: {
  label: string;
  value: string;
  mono?: boolean;
  accent?: boolean;
  dim?: boolean;
}) {
  return (
    <div className="space-y-0.5">
      <p className="text-xs" style={{ color: "var(--muted-foreground)", fontSize: "10px", letterSpacing: "0.05em", textTransform: "uppercase" }}>
        {label}
      </p>
      <p
        className="text-lg font-semibold"
        style={{
          fontFamily: mono ? "'JetBrains Mono', monospace" : undefined,
          color: accent ? "var(--keep)" : dim ? "var(--muted-foreground)" : "var(--foreground)",
          fontWeight: mono ? 500 : 600,
          fontSize: mono ? "1rem" : "1.1rem",
        }}
      >
        {value}
      </p>
    </div>
  );
}

function JudgePanel({
  result,
  segments,
  onToggle,
}: {
  result: JudgeResult;
  segments: Segment[];
  onToggle: (index: number) => void;
}) {
  const fps = result.false_positives ?? [];
  const fns = result.false_negatives ?? [];
  const hasFeedback = fps.length > 0 || fns.length > 0;
  const cohColor =
    result.coherence >= 85 ? "#22c55e" : result.coherence >= 70 ? "#eab308" : "#ef4444";

  return (
    <div
      className="rounded-xl border p-5 space-y-4"
      style={{ borderColor: "rgba(234,179,8,0.25)", background: "rgba(234,179,8,0.03)" }}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M7 1.5v5l3 1.5" stroke="#eab308" strokeWidth="1.4" strokeLinecap="round"/>
            <circle cx="7" cy="7" r="5.5" stroke="#eab308" strokeWidth="1.4"/>
          </svg>
          <span className="text-xs font-semibold uppercase tracking-widest" style={{ color: "#eab308", fontFamily: "'Syne', sans-serif" }}>
            Judge&apos;s review
          </span>
        </div>
        <div className="flex items-center gap-3">
          {(["coherence", "preservation", "conciseness"] as const).map((k) => (
            <div key={k} className="flex items-center gap-1">
              <span className="text-xs" style={{ color: "var(--muted-foreground)", fontSize: "10px", textTransform: "uppercase" }}>{k.slice(0, 3)}</span>
              <span
                className="text-xs font-semibold"
                style={{
                  fontFamily: "'JetBrains Mono', monospace",
                  color: k === "coherence" ? cohColor : "var(--foreground)",
                }}
              >
                {result[k]}
              </span>
            </div>
          ))}
        </div>
      </div>

      {result.overall_notes && (
        <p className="text-xs leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
          {result.overall_notes}
        </p>
      )}

      {hasFeedback ? (
        <div className="space-y-2">
          {fps.map((fp, i) => (
            <RepairRow key={`fp-${fp.segment_index}-${i}`} item={fp} action="restore" segments={segments} onToggle={onToggle} />
          ))}
          {fns.map((fn, i) => (
            <RepairRow key={`fn-${fn.segment_index}-${i}`} item={fn} action="drop" segments={segments} onToggle={onToggle} />
          ))}
        </div>
      ) : (
        <p className="text-xs" style={{ color: "#22c55e" }}>No specific repairs suggested — edit looks clean.</p>
      )}
    </div>
  );
}

function RepairRow({
  item,
  action,
  segments,
  onToggle,
}: {
  item: JudgeItem;
  action: "restore" | "drop";
  segments: Segment[];
  onToggle: (index: number) => void;
}) {
  const seg = segments[item.segment_index];
  const alreadyApplied = seg && (action === "restore" ? seg.keep : !seg.keep);
  const accentColor = action === "restore" ? "#22c55e" : "#ef4444";
  const label = action === "restore" ? "Restore" : "Drop";

  return (
    <div
      className="flex items-start gap-3 rounded-lg p-3"
      style={{ background: "rgba(255,255,255,0.03)", border: "1px solid var(--border)" }}
    >
      <div className="flex-1 min-w-0 space-y-1">
        <span
          className="text-xs font-mono px-1.5 py-0.5 rounded"
          style={{ background: `${accentColor}18`, color: accentColor, fontSize: "10px" }}
        >
          [{item.segment_index}] {action === "restore" ? "cut → keep" : "keep → drop"}
        </span>
        <p className="text-xs leading-snug" style={{ color: "var(--foreground)", opacity: 0.85 }}>
          &ldquo;{item.text.slice(0, 120)}{item.text.length > 120 ? "…" : ""}&rdquo;
        </p>
        <p className="text-xs leading-snug" style={{ color: "var(--muted-foreground)" }}>
          {item.reason}
        </p>
      </div>
      <button
        onClick={() => { if (!alreadyApplied) onToggle(item.segment_index); }}
        disabled={!!alreadyApplied}
        className="flex-shrink-0 text-xs px-3 py-1.5 rounded-lg border transition-colors"
        style={{
          borderColor: alreadyApplied ? "var(--border)" : `${accentColor}40`,
          color: alreadyApplied ? "var(--muted-foreground)" : accentColor,
          background: alreadyApplied ? "transparent" : `${accentColor}0a`,
          opacity: alreadyApplied ? 0.5 : 1,
        }}
      >
        {alreadyApplied ? "Applied" : label}
      </button>
    </div>
  );
}
