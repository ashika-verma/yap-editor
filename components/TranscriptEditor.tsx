"use client";

import { useState, useMemo } from "react";
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

export function TranscriptEditor({
  segments,
  summary,
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
  onResetToGemini,
  onToggleAll,
  onSensitivityChange,
  onJudge,
  onRefine,
}: Props) {
  const [showVideo, setShowVideo] = useState(false);
  const [filter, setFilter] = useState<"all" | "keep" | "drop" | "risky">("all");
  const [copied, setCopied] = useState(false);

  const handleCopyTranscript = () => {
    const text = buildCleanTranscript(segments);
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const totalSegments = segments.length;
  const droppedCount = totalSegments - keptCount;
  const totalSeconds = segments.reduce((a, s) => a + (s.endSec - s.startSec), 0);
  const savedSeconds = totalSeconds - keptSeconds;
  const keptPct = totalSeconds > 0 ? Math.round((keptSeconds / totalSeconds) * 100) : 0;

  const riskyKeptSegments = segments.filter((s) => s.keep && s.jobRisk);
  const riskyCount = riskyKeptSegments.length;

  const filtered =
    filter === "keep"
      ? segments.filter((s) => s.keep)
      : filter === "drop"
      ? segments.filter((s) => !s.keep)
      : filter === "risky"
      ? segments.filter((s) => s.jobRisk)
      : segments;

  type GapItem = { type: "gap"; dropped: Segment[]; afterRealIdx: number };
  type SegItem  = { type: "segment"; seg: Segment; realIdx: number };

  // In "keep" view, inject gap cards between consecutive kept segments that have
  // dropped segments between them — makes broken-sentence cuts immediately visible.
  const renderList = useMemo<Array<SegItem | GapItem>>(() => {
    if (filter !== "keep") {
      return filtered.map((seg) => ({ type: "segment", seg, realIdx: segments.indexOf(seg) }));
    }
    const items: Array<SegItem | GapItem> = [];
    for (let i = 0; i < filtered.length; i++) {
      const seg = filtered[i];
      const realIdx = segments.indexOf(seg);
      items.push({ type: "segment", seg, realIdx });
      if (i < filtered.length - 1) {
        const nextRealIdx = segments.indexOf(filtered[i + 1]);
        const dropped = segments.slice(realIdx + 1, nextRealIdx).filter((s) => !s.keep);
        if (dropped.length > 0) {
          items.push({ type: "gap", dropped, afterRealIdx: realIdx });
        }
      }
    }
    return items;
  }, [filter, filtered, segments]);

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
              Gemini's narrative read
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
            {/* Structural findings */}
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
              Gemini flagged content that could embarrass you with your employer or online. Review and drop before exporting.
            </p>
          </div>
          <button
            onClick={() => setFilter("risky")}
            className="flex-shrink-0 text-xs px-2.5 py-1 rounded-lg border transition-colors"
            style={{ borderColor: "rgba(239,68,68,0.3)", color: "#ef4444", background: "rgba(239,68,68,0.08)", whiteSpace: "nowrap" }}
          >
            Review
          </button>
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

        {/* Timeline preview bar */}
        <div className="space-y-1.5">
          <div className="flex h-3 rounded-full overflow-hidden gap-px" style={{ background: "var(--secondary)" }}>
            {segments.map((seg, i) => {
              const width = ((seg.endSec - seg.startSec) / totalSeconds) * 100;
              return (
                <button
                  key={i}
                  title={seg.text.slice(0, 60)}
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
            <span style={{ color: "var(--keep)", fontSize: "10px" }}>■ keep</span>
            <span style={{ color: "var(--drop)", fontSize: "10px" }}>■ drop</span>
            <span>{totalDuration}</span>
          </div>
        </div>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-2 animate-fade-in-up-delay-3">
        <div
          className="flex rounded-lg overflow-hidden border text-xs"
          style={{ borderColor: "var(--border)" }}
        >
          {(["all", "keep", "drop", "risky"] as const).map((f, i, arr) => {
            const count = f === "keep" ? keptCount : f === "drop" ? droppedCount : f === "risky" ? segments.filter(s => s.jobRisk).length : totalSegments;
            const isRisky = f === "risky";
            return (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className="px-3 py-1.5 capitalize transition-colors duration-100"
                style={{
                  background: filter === f ? (isRisky ? "rgba(239,68,68,0.1)" : "var(--secondary)") : "transparent",
                  color: filter === f ? (isRisky ? "#ef4444" : "var(--foreground)") : isRisky && count > 0 ? "rgba(239,68,68,0.7)" : "var(--muted-foreground)",
                  borderRight: i < arr.length - 1 ? "1px solid var(--border)" : "none",
                }}
              >
                {f === "risky" ? "⚠ risky" : f} ({count})
              </button>
            );
          })}
        </div>

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
            <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>
              updating…
            </span>
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
            Gemini's picks
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

      {/* Segments */}
      <div
        className="rounded-xl border overflow-hidden"
        style={{ borderColor: "var(--border)" }}
      >
        <div className="overflow-y-auto" style={{ maxHeight: "60vh" }}>
          {renderList.map((item, displayIdx) => {
            if (item.type === "gap") {
              const preview = item.dropped.map((s) => s.text).join(" ").slice(0, 120);
              return (
                <div
                  key={`gap-${item.afterRealIdx}`}
                  className="px-4 py-2.5 flex items-start gap-3 border-b"
                  style={{ borderColor: "var(--border)", background: "rgba(99,102,241,0.03)" }}
                >
                  <div className="flex-shrink-0 mt-0.5" style={{ width: 90 }}>
                    <span className="text-xs" style={{ color: "var(--muted-foreground)", opacity: 0.5, fontFamily: "'JetBrains Mono', monospace", fontSize: "10px" }}>
                      ↕ {item.dropped.length} cut
                    </span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs leading-relaxed italic" style={{ color: "var(--muted-foreground)", opacity: 0.6 }}>
                      "{preview}{preview.length < item.dropped.map((s) => s.text).join(" ").length ? "…" : ""}"
                    </p>
                  </div>
                  <button
                    onClick={() => item.dropped.forEach((s) => onToggle(segments.indexOf(s)))}
                    className="flex-shrink-0 text-xs px-2 py-1 rounded border transition-colors"
                    style={{ borderColor: "var(--border)", color: "var(--muted-foreground)", background: "transparent", whiteSpace: "nowrap" }}
                    onMouseEnter={(e) => { (e.currentTarget).style.color = "var(--foreground)"; }}
                    onMouseLeave={(e) => { (e.currentTarget).style.color = "var(--muted-foreground)"; }}
                  >
                    restore
                  </button>
                </div>
              );
            }

            const { seg, realIdx } = item;
            const reasonMeta = seg.dropReason ? DROP_REASON_LABELS[seg.dropReason] : null;
            return (
              <div
                key={realIdx}
                className={`segment-row border-b flex items-start gap-0 transition-all duration-150 ${seg.keep ? "segment-keep" : "segment-drop"}`}
                style={{
                  borderColor: "var(--border)",
                  borderLeft: `3px solid ${seg.keep && seg.jobRisk ? "#ef4444" : seg.keep ? "var(--keep)" : "var(--drop)"}`,
                }}
              >
                {/* Timestamp */}
                <div
                  className="flex-shrink-0 px-4 py-3.5 text-right select-none flex flex-col justify-between"
                  style={{ width: 90, borderRight: "1px solid var(--border)" }}
                >
                  <div>
                    <span
                      className="text-xs block"
                      style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--muted-foreground)" }}
                    >
                      {seg.start}
                    </span>
                    <span
                      className="text-xs block"
                      style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--muted-foreground)", opacity: 0.5, fontSize: "10px" }}
                    >
                      {seg.end}
                    </span>
                  </div>
                  {seg.energyScore != null && seg.energyScore > 0 && (
                    <div
                      className="mt-2 h-0.5 rounded-full overflow-hidden"
                      style={{ background: "var(--secondary)" }}
                      title={`energy ${Math.round(seg.energyScore * 100)}%${seg.visualTag ? ` · ${seg.visualTag}` : ""}`}
                    >
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${seg.energyScore * 100}%`,
                          background: seg.energyScore > 0.6
                            ? "#f59e0b"
                            : seg.energyScore > 0.3
                            ? "var(--primary)"
                            : "var(--muted-foreground)",
                          opacity: 0.7,
                        }}
                      />
                    </div>
                  )}
                </div>

                {/* Text */}
                <div className="flex-1 px-4 py-3.5 pr-3 min-w-0">
                  <p
                    className="text-sm leading-relaxed"
                    style={{ color: seg.keep ? "var(--foreground)" : "var(--muted-foreground)" }}
                  >
                    <SegmentText
                      seg={seg}
                      segmentIndex={realIdx}
                      onToggleWordCut={onToggleWordCut}
                    />
                  </p>
                  {!seg.keep && reasonMeta && (
                    <span
                      className="inline-block mt-1.5 text-xs px-2 py-0.5 rounded-full"
                      style={{
                        background: reasonMeta.bg,
                        color: reasonMeta.color,
                        fontFamily: "'JetBrains Mono', monospace",
                        fontSize: "10px",
                      }}
                    >
                      {reasonMeta.label}
                    </span>
                  )}
                  {seg.keep && seg.wordCuts && seg.wordCuts.length > 0 && (() => {
                    const fillerCuts   = seg.wordCuts.filter((wc) => wc.source === "filler");
                    const manualCuts   = seg.wordCuts.filter((wc) => wc.source === "manual");
                    const silenceCuts  = seg.wordCuts.filter((wc) => wc.source === "silence" || wc.word === "<silence>");
                    return (
                      <>
                        {fillerCuts.length > 0 && (
                          <span
                            className="inline-block mt-1.5 text-xs px-2 py-0.5 rounded-full"
                            style={{
                              background: "rgba(245,158,11,0.1)",
                              color: "#f59e0b",
                              fontFamily: "'JetBrains Mono', monospace",
                              fontSize: "10px",
                            }}
                          >
                            {fillerCuts.length} filler{fillerCuts.length > 1 ? "s" : ""} cut
                          </span>
                        )}
                        {silenceCuts.length > 0 && (
                          <span
                            className="inline-block mt-1.5 ml-1 text-xs px-2 py-0.5 rounded-full"
                            style={{
                              background: "rgba(148,163,184,0.1)",
                              color: "#94a3b8",
                              fontFamily: "'JetBrains Mono', monospace",
                              fontSize: "10px",
                            }}
                          >
                            {silenceCuts.length} silence cut
                          </span>
                        )}
                        {manualCuts.length > 0 && (
                          <span
                            className="inline-block mt-1.5 ml-1 text-xs px-2 py-0.5 rounded-full"
                            style={{
                              background: "rgba(56,189,248,0.1)",
                              color: "#38bdf8",
                              fontFamily: "'JetBrains Mono', monospace",
                              fontSize: "10px",
                            }}
                          >
                            {manualCuts.length} manual cut{manualCuts.length > 1 ? "s" : ""}
                          </span>
                        )}
                      </>
                    );
                  })()}
                  {seg.keep && seg.continuityRepair && (
                    <span
                      className="inline-block mt-1.5 ml-1 text-xs px-2 py-0.5 rounded-full"
                      title={seg.continuityRepair.reason}
                      style={{
                        background: "rgba(34,197,94,0.1)",
                        color: "#22c55e",
                        fontFamily: "'JetBrains Mono', monospace",
                        fontSize: "10px",
                      }}
                    >
                      continuity kept
                    </span>
                  )}
                  {seg.keep && seg.transition && seg.transition.type !== "cut" && (
                    <span
                      className="inline-block mt-1.5 ml-1 text-xs px-2 py-0.5 rounded-full"
                      title={
                        seg.transition.type === "j-cut"
                          ? `J-cut: audio starts ${seg.transition.offsetSec.toFixed(2)}s before video`
                          : `L-cut: audio from previous lingers ${seg.transition.offsetSec.toFixed(2)}s`
                      }
                      style={{
                        background: "rgba(99,102,241,0.12)",
                        color: "var(--primary)",
                        fontFamily: "'JetBrains Mono', monospace",
                        fontSize: "10px",
                        border: "1px solid rgba(99,102,241,0.2)",
                      }}
                    >
                      {seg.transition.type === "j-cut" ? "J-cut" : "L-cut"}
                    </span>
                  )}
                  {seg.keep && seg.duckLevel != null && seg.duckLevel < 1.0 && (
                    <span
                      className="inline-block mt-1.5 ml-1 text-xs px-2 py-0.5 rounded-full"
                      title={`Audio ducked to ${Math.round(seg.duckLevel * 100)}% (B-roll)`}
                      style={{
                        background: "rgba(251,146,60,0.1)",
                        color: "#fb923c",
                        fontFamily: "'JetBrains Mono', monospace",
                        fontSize: "10px",
                      }}
                    >
                      ducked
                    </span>
                  )}
                  {seg.visualTag && (
                    <span
                      className="inline-block mt-1.5 text-xs px-2 py-0.5 rounded-full"
                      style={{
                        background: "rgba(99,102,241,0.08)",
                        color: "var(--primary)",
                        fontFamily: "'JetBrains Mono', monospace",
                        fontSize: "10px",
                        opacity: 0.75,
                        maxWidth: "100%",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                      title={seg.visualTag}
                    >
                      🎥 {seg.visualTag}
                    </span>
                  )}
                  {seg.jobRisk && (
                    <span
                      className="inline-flex items-center gap-1 mt-1.5 text-xs px-2 py-0.5 rounded-full"
                      title={seg.jobRisk}
                      style={{
                        background: seg.keep ? "rgba(239,68,68,0.12)" : "rgba(239,68,68,0.06)",
                        color: seg.keep ? "#ef4444" : "rgba(239,68,68,0.6)",
                        fontFamily: "'JetBrains Mono', monospace",
                        fontSize: "10px",
                        border: seg.keep ? "1px solid rgba(239,68,68,0.25)" : "none",
                      }}
                    >
                      ⚠ {seg.jobRisk}
                    </span>
                  )}
                </div>

                {/* Toggle */}
                <div className="flex-shrink-0 px-3 py-3.5 flex items-center">
                  <button
                    onClick={() => onToggle(realIdx)}
                    className="toggle-btn w-9 h-5 rounded-full flex items-center transition-all duration-200 relative"
                    style={{
                      background: seg.keep ? "var(--keep)" : "var(--secondary)",
                      border: `1px solid ${seg.keep ? "var(--keep)" : "var(--border)"}`,
                    }}
                    aria-label={seg.keep ? "Mark as drop" : "Mark as keep"}
                  >
                    <div
                      className="w-3.5 h-3.5 rounded-full absolute transition-all duration-200"
                      style={{
                        background: seg.keep ? "white" : "var(--muted-foreground)",
                        left: seg.keep ? "calc(100% - 18px)" : "2px",
                        top: "2px",
                      }}
                    />
                  </button>
                </div>
              </div>
            );
          })}

          {renderList.length === 0 && (
            <div className="py-12 text-center" style={{ color: "var(--muted-foreground)" }}>
              <p className="text-sm">No segments match this filter.</p>
            </div>
          )}
        </div>

        {/* Footer stat */}
        <div
          className="px-5 py-3 flex items-center justify-between border-t"
          style={{ borderColor: "var(--border)", background: "var(--card)" }}
        >
          <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>
            {keptCount} of {totalSegments} segments kept · {keptPct}% of original runtime
          </span>
          <span
            className="text-xs"
            style={{ color: "var(--keep)", fontFamily: "'JetBrains Mono', monospace" }}
          >
            {formatSeconds(keptSeconds)} edited
          </span>
        </div>
      </div>
    </div>
  );
}

function findWordCut(
  seg: Segment,
  word: WordTimestamp,
  wordIndex: number,
): WordCut | undefined {
  const id = wordCutId(seg, wordIndex);
  return (seg.wordCuts ?? []).find(
    (cut) =>
      cut.id === id ||
      (word.start >= cut.startSec && word.end <= cut.endSec),
  );
}

function SegmentText({
  seg,
  segmentIndex,
  onToggleWordCut,
}: {
  seg: Segment;
  segmentIndex: number;
  onToggleWordCut: (segmentIndex: number, wordIndex: number, range: boolean) => void;
}) {
  if (!seg.words?.length) {
    return <>{seg.text}</>;
  }

  return (
    <>
      {seg.words.map((w, i) => {
        const cut = findWordCut(seg, w, i);
        const isCut = Boolean(cut);
        const isManual = cut?.source === "manual";
        const canToggle = !isCut || isManual;
        const cutColor = isManual ? "#38bdf8" : "#f59e0b";
        return (
          <span
            key={i}
            role="button"
            tabIndex={canToggle ? 0 : -1}
            title={
              isCut
                ? isManual
                  ? "manual cut — click to restore"
                  : `${cut?.source ?? "auto"} cut`
                : "click to cut this word"
            }
            onClick={(event) => {
              event.preventDefault();
              if (canToggle) onToggleWordCut(segmentIndex, i, event.shiftKey);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                if (canToggle) onToggleWordCut(segmentIndex, i, event.shiftKey);
              }
            }}
            className={`rounded px-0.5 transition-colors ${canToggle ? "cursor-pointer" : "cursor-default"}`}
            style={isCut ? {
              color: cutColor,
              textDecoration: "line-through",
              textDecorationColor: isManual
                ? "rgba(56,189,248,0.55)"
                : "rgba(245,158,11,0.5)",
              opacity: 0.75,
            } : {
              color: "inherit",
            }}
          >
            {i > 0 ? " " : ""}{w.word}
          </span>
        );
      })}
    </>
  );
}

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
          {fps.map((fp) => (
            <RepairRow key={`fp-${fp.segment_index}`} item={fp} action="restore" segments={segments} onToggle={onToggle} />
          ))}
          {fns.map((fn) => (
            <RepairRow key={`fn-${fn.segment_index}`} item={fn} action="drop" segments={segments} onToggle={onToggle} />
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
