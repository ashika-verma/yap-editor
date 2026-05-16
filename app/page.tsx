"use client";

import { useState, useCallback, useMemo } from "react";
import { toast } from "sonner";
import { UploadStage } from "@/components/UploadStage";
import { TranscriptEditor } from "@/components/TranscriptEditor";
import { ExportPanel } from "@/components/ExportPanel";
import {
  buildCleanTranscript as buildTranscriptText,
  EditPlan,
  FillerSensitivity,
  WordCut,
  wordCutId,
} from "@/lib/editPlan";

type Stage = "upload" | "transcribing" | "edit" | "exporting";

export default function Home() {
  const [stage, setStage] = useState<Stage>("upload");
  const [uploadProgress, setUploadProgress] = useState(0);
  const [filePath, setFilePath] = useState<string | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [plan, setPlan] = useState<EditPlan | null>(null);
  const [originalPlan, setOriginalPlan] = useState<EditPlan | null>(null);
  const [fillerSensitivity, setFillerSensitivity] =
    useState<FillerSensitivity>("balanced");
  const [disableVision, setDisableVision] = useState(false);
  const [exportProgress, setExportProgress] = useState(0);
  const [exportUrl, setExportUrl] = useState<string | null>(null);
  const [isReplanning, setIsReplanning] = useState(false);
  const [lastWordSelection, setLastWordSelection] = useState<{
    segmentIndex: number;
    wordIndex: number;
  } | null>(null);

  const segments = useMemo(() => plan?.segments ?? [], [plan?.segments]);

  const handleTranscribe = useCallback(async (path: string) => {
    setStage("transcribing");
    try {
      const res = await fetch("/api/transcribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filePath: path, fillerSensitivity, disableVision }),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.error || "Transcription failed");
      }

      const data: { plan: EditPlan } = await res.json();
      setPlan(data.plan);
      setOriginalPlan(data.plan);
      setFillerSensitivity(data.plan.settings.fillerSensitivity);
      setStage("edit");
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Transcription failed";
      toast.error(message);
      setStage("upload");
    }
  }, [fillerSensitivity]);

  const handleUpload = useCallback(async (file: File) => {
    setUploadProgress(0);
    const localUrl = URL.createObjectURL(file);
    setVideoUrl(localUrl);

    const xhr = new XMLHttpRequest();
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) {
        setUploadProgress(Math.round((e.loaded / e.total) * 100));
      }
    });

    const uploadPromise = new Promise<string>((resolve, reject) => {
      xhr.onload = () => {
        if (xhr.status === 200) {
          const { filePath } = JSON.parse(xhr.responseText);
          resolve(filePath);
        } else {
          reject(new Error("Upload failed"));
        }
      };
      xhr.onerror = () => reject(new Error("Upload failed"));
    });

    xhr.open("POST", "/api/upload");
    xhr.setRequestHeader("Content-Type", file.type || "video/mp4");
    xhr.send(file);

    try {
      const path = await uploadPromise;
      setFilePath(path);
      setUploadProgress(100);
      await handleTranscribe(path);
    } catch {
      toast.error("Upload failed. Try again.");
      setStage("upload");
    }
  }, [handleTranscribe]);

  const handleToggleSegment = (index: number) => {
    setPlan((prev) =>
      prev
        ? {
            ...prev,
            segments: prev.segments.map((seg, i) =>
              i === index
                ? {
                    ...seg,
                    keep: !seg.keep,
                    decisionSource: "user",
                    dropReason: seg.keep ? "manual" : "",
                  }
                : seg,
            ),
          }
        : prev,
    );
  };

  const handleResetToGemini = () => {
    if (!originalPlan) return;
    setPlan(originalPlan);
    setFillerSensitivity(originalPlan.settings.fillerSensitivity);
    toast.success("Reset to Gemini's suggestions");
  };

  const handleSensitivityChange = async (s: FillerSensitivity) => {
    if (!filePath || !plan || s === fillerSensitivity) return;
    setFillerSensitivity(s);
    setIsReplanning(true);
    try {
      const res = await fetch("/api/replan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filePath, plan, fillerSensitivity: s }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.error || "Replan failed");
      }
      const data: { plan: EditPlan } = await res.json();
      setPlan(data.plan);
      toast.success("Filler cuts updated");
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Replan failed";
      toast.error(message);
    } finally {
      setIsReplanning(false);
    }
  };

  const buildCleanTranscript = useCallback(() => {
    return buildTranscriptText(segments);
  }, [segments]);

  const handleToggleAll = (keep: boolean) => {
    setPlan((prev) =>
      prev
        ? {
            ...prev,
            segments: prev.segments.map((seg) => ({
              ...seg,
              keep,
              decisionSource: "user",
              dropReason: keep ? "" : "manual",
            })),
          }
        : prev,
    );
  };

  const handleToggleWordCut = (
    segmentIndex: number,
    wordIndex: number,
    range: boolean,
  ) => {
    setPlan((prev) => {
      if (!prev) return prev;
      const segment = prev.segments[segmentIndex];
      if (!segment?.words?.[wordIndex]) return prev;
      const rangeStart =
        range && lastWordSelection?.segmentIndex === segmentIndex
          ? Math.min(lastWordSelection.wordIndex, wordIndex)
          : wordIndex;
      const rangeEnd =
        range && lastWordSelection?.segmentIndex === segmentIndex
          ? Math.max(lastWordSelection.wordIndex, wordIndex)
          : wordIndex;

      const nextSegments = prev.segments.map((seg, i) => {
        if (i !== segmentIndex) return seg;
        const existingCuts = seg.wordCuts ?? [];
        const rangeIds = new Set(
          seg.words
            ?.slice(rangeStart, rangeEnd + 1)
            .map((_, offset) => wordCutId(seg, rangeStart + offset)) ?? [],
        );
        const hasAnyManualCut = existingCuts.some(
          (cut) => cut.source === "manual" && cut.id && rangeIds.has(cut.id),
        );
        const withoutRangeManualCuts = existingCuts.filter(
          (cut) => !(cut.source === "manual" && cut.id && rangeIds.has(cut.id)),
        );
        const addedManualCuts: WordCut[] = hasAnyManualCut
          ? []
          : (seg.words ?? [])
              .slice(rangeStart, rangeEnd + 1)
              .map((word, offset) => ({ word, wordIndex: rangeStart + offset }))
              .filter(({ word, wordIndex }) => {
                const id = wordCutId(seg, wordIndex);
                return !existingCuts.some(
                  (cut) =>
                    cut.id === id ||
                    (word.start >= cut.startSec && word.end <= cut.endSec),
                );
              })
              .map(({ word, wordIndex }) => ({
                id: wordCutId(seg, wordIndex),
                startSec: word.start,
                endSec: word.end,
                word: word.word,
                source: "manual",
              }));
        return {
          ...seg,
          wordCuts: [...withoutRangeManualCuts, ...addedManualCuts].sort(
            (a, b) => a.startSec - b.startSec,
          ),
        };
      });

      return { ...prev, segments: nextSegments };
    });
    setLastWordSelection({ segmentIndex, wordIndex });
  };

  const handleExport = async () => {
    if (!filePath || !plan) return;
    setStage("exporting");
    setExportProgress(0);
    setExportUrl(null);

    const interval = setInterval(() => {
      setExportProgress((p) => Math.min(p + 1.5, 88));
    }, 500);

    try {
      const finalizeRes = await fetch("/api/finalize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filePath, plan }),
      });
      if (!finalizeRes.ok) {
        const err = await finalizeRes.json();
        throw new Error(err.error || "Finalize failed");
      }
      const finalized: { plan: EditPlan } = await finalizeRes.json();
      setPlan(finalized.plan);

      const res = await fetch("/api/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filePath, plan: finalized.plan }),
      });

      clearInterval(interval);

      if (!res.ok) {
        let errMsg = "Export failed";
        try {
          const err = await res.json();
          errMsg = err.error || errMsg;
        } catch {}
        throw new Error(errMsg);
      }

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      setExportUrl(url);
      setExportProgress(100);
    } catch (err: unknown) {
      clearInterval(interval);
      const message = err instanceof Error ? err.message : "Export failed";
      toast.error(message);
      setStage("edit");
    }
  };

  const handleReset = () => {
    setStage("upload");
    setFilePath(null);
    setVideoUrl(null);
    setPlan(null);
    setOriginalPlan(null);
    setExportUrl(null);
    setUploadProgress(0);
    setExportProgress(0);
    setLastWordSelection(null);
  };

  const keptCount = segments.filter((s) => s.keep).length;
  const keptSeconds = segments
    .filter((s) => s.keep)
    .reduce((acc, s) => acc + (s.endSec - s.startSec), 0);

  const stageIndex = ["upload", "transcribing", "edit", "exporting"].indexOf(
    stage,
  );

  return (
    <div className="min-h-screen" style={{ background: "var(--background)" }}>
      {/* Header */}
      <header
        className="border-b flex items-center justify-between px-6 sm:px-10 py-4"
        style={{
          borderColor: "var(--border)",
          background: "rgba(8,8,9,0.9)",
          backdropFilter: "blur(8px)",
          position: "sticky",
          top: 0,
          zIndex: 50,
        }}
      >
        <div className="flex items-center gap-3">
          <div
            className="w-6 h-6 rounded flex items-center justify-center flex-shrink-0"
            style={{ background: "var(--primary)" }}
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path
                d="M1.5 1.5v9M1.5 6h9M10.5 3.5L8 6l2.5 2.5"
                stroke="white"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>
          <span
            className="text-sm font-semibold tracking-widest uppercase"
            style={{
              fontFamily: "'Syne', sans-serif",
              color: "var(--foreground)",
            }}
          >
            Yap Editor
          </span>
        </div>

        {/* Step indicator */}
        <div className="flex items-center gap-1">
          {(["Upload", "Analyse", "Edit", "Export"] as const).map(
            (label, i) => {
              const active = i === stageIndex;
              const done = i < stageIndex;
              return (
                <div key={label} className="flex items-center gap-1">
                  <div className="flex items-center gap-1.5">
                    <div
                      className="w-4 h-4 rounded-full flex items-center justify-center text-[8px] font-bold"
                      style={{
                        fontFamily: "'JetBrains Mono', monospace",
                        background: active
                          ? "var(--primary)"
                          : done
                            ? "rgba(99,102,241,0.25)"
                            : "var(--secondary)",
                        color: active
                          ? "white"
                          : done
                            ? "var(--primary)"
                            : "var(--muted-foreground)",
                        transition: "all 0.25s",
                      }}
                    >
                      {done ? "✓" : i + 1}
                    </div>
                    <span
                      className="text-xs hidden sm:block"
                      style={{
                        color: active
                          ? "var(--foreground)"
                          : "var(--muted-foreground)",
                        fontWeight: active ? 500 : 400,
                        transition: "color 0.25s",
                      }}
                    >
                      {label}
                    </span>
                  </div>
                  {i < 3 && (
                    <div
                      className="w-3 h-px mx-0.5"
                      style={{
                        background: done
                          ? "rgba(99,102,241,0.4)"
                          : "var(--border)",
                        transition: "background 0.3s",
                      }}
                    />
                  )}
                </div>
              );
            },
          )}
        </div>

        <div className="w-24 flex justify-end">
          {stage !== "upload" && stage !== "transcribing" && (
            <button
              onClick={handleReset}
              className="text-xs px-3 py-1.5 rounded border transition-all duration-150"
              style={{
                borderColor: "var(--border)",
                color: "var(--muted-foreground)",
                background: "transparent",
              }}
              onMouseEnter={(e) => {
                (e.target as HTMLButtonElement).style.color =
                  "var(--foreground)";
              }}
              onMouseLeave={(e) => {
                (e.target as HTMLButtonElement).style.color =
                  "var(--muted-foreground)";
              }}
            >
              Start over
            </button>
          )}
        </div>
      </header>

      {/* Main */}
      <main className="max-w-5xl mx-auto px-4 sm:px-8 py-10">
        {stage === "upload" && (
          <div className="animate-fade-in-up">
            <UploadStage
              onUpload={handleUpload}
              uploadProgress={uploadProgress}
              disableVision={disableVision}
              onDisableVisionChange={setDisableVision}
            />
          </div>
        )}

        {stage === "transcribing" && (
          <div className="animate-fade-in-up">
            <TranscribingState />
          </div>
        )}

        {(stage === "edit" || stage === "exporting") && plan && (
          <div className="animate-fade-in-up space-y-8">
            <TranscriptEditor
              segments={segments}
              summary={plan.summary}
              narrativeAnalysis={plan.narrativeAnalysis}
              totalDuration={plan.totalDuration}
              keptCount={keptCount}
              keptSeconds={keptSeconds}
              videoUrl={videoUrl}
              fillerSensitivity={fillerSensitivity}
              isReplanning={isReplanning}
              onToggle={handleToggleSegment}
              onToggleWordCut={handleToggleWordCut}
              onResetToGemini={handleResetToGemini}
              onToggleAll={handleToggleAll}
              onSensitivityChange={handleSensitivityChange}
            />
            <ExportPanel
              stage={stage}
              progress={exportProgress}
              exportUrl={exportUrl}
              keptSegments={keptCount}
              keptSeconds={keptSeconds}
              onExport={handleExport}
              onReset={handleReset}
              onCopyTranscript={buildCleanTranscript}
            />
          </div>
        )}
      </main>
    </div>
  );
}

function TranscribingState() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[65vh] gap-10">
      <div className="text-center space-y-3">
        <p
          className="text-3xl sm:text-4xl font-bold"
          style={{ fontFamily: "'Syne', sans-serif" }}
        >
          Gemini is watching your yap
        </p>
        <p className="text-sm" style={{ color: "var(--muted-foreground)" }}>
          1–3 min for a 15-minute video. Sit tight.
        </p>
      </div>

      <div
        className="w-full max-w-sm rounded-xl border overflow-hidden"
        style={{ borderColor: "var(--border)", background: "var(--card)" }}
      >
        {[
          { label: "Video received", done: true },
          { label: "MLX-Whisper transcribing audio…", active: true },
          { label: "Extracting word-level timestamps" },
          { label: "Gemini analysing narrative structure" },
          { label: "Flagging filler & redundancies" },
        ].map((step, i) => (
          <div
            key={i}
            className="flex items-center gap-3 px-5 py-3 border-b last:border-0"
            style={{
              borderColor: "var(--border)",
              opacity: step.done || step.active ? 1 : 0.35,
              animation: `fade-in-up 0.35s ${i * 0.07}s both`,
            }}
          >
            <div className="w-4 h-4 flex items-center justify-center flex-shrink-0">
              {step.done ? (
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <circle cx="7" cy="7" r="6" fill="rgba(34,197,94,0.12)" />
                  <path
                    d="M4 7l2 2 4-4"
                    stroke="#22c55e"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              ) : step.active ? (
                <div
                  className="spinner"
                  style={{ width: 14, height: 14, borderWidth: 1.5 }}
                />
              ) : (
                <div
                  className="w-1 h-1 rounded-full"
                  style={{ background: "var(--muted-foreground)" }}
                />
              )}
            </div>
            <span
              className="text-sm"
              style={{
                color: step.done
                  ? "#22c55e"
                  : step.active
                    ? "var(--foreground)"
                    : "var(--muted-foreground)",
              }}
            >
              {step.label}
            </span>
          </div>
        ))}
      </div>

      <span
        className="text-xs"
        style={{
          color: "var(--muted-foreground)",
          fontFamily: "'JetBrains Mono', monospace",
          opacity: 0.6,
        }}
      >
        mlx-whisper · local · gemini-2.5-flash · narrative
      </span>
    </div>
  );
}
