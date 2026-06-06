"use client";

import { useState, useRef } from "react";

function formatSeconds(sec: number) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

interface Props {
  stage: "edit" | "exporting";
  progress: number;
  exportUrl: string | null;
  keptSegments: number;
  keptSeconds: number;
  exportedAudioUrl?: string | null;
  onExport: () => void;
  onReExport: () => void;
  onReset: () => void;
  onCopyTranscript: () => string;
  onGenerateThumbnails: (layout: "gap" | "split" | "editorial") => void;
  onAudioUpload?: (file: File) => Promise<void>;
  onRemux?: (audioPath: string) => Promise<void>;
  onSaveFixture?: () => Promise<void>;
  isThumbnailGenerating?: boolean;
}

export function ExportPanel({
  stage,
  progress,
  exportUrl,
  keptSegments,
  keptSeconds,
  exportedAudioUrl,
  onExport,
  onReExport,
  onReset,
  onCopyTranscript,
  onGenerateThumbnails,
  onAudioUpload,
  onRemux,
  onSaveFixture,
  isThumbnailGenerating = false,
}: Props) {
  const [copied, setCopied] = useState(false);
  const [thumbLayout, setThumbLayout] = useState<"gap" | "split" | "editorial">("editorial");
  const [audioUploading, setAudioUploading] = useState(false);
  const [fixtureSaving, setFixtureSaving] = useState(false);
  const [fixtureSaved, setFixtureSaved] = useState(false);
  const [remuxing, setRemuxing] = useState(false);
  const audioInputRef = useRef<HTMLInputElement>(null);
  const adobeInputRef = useRef<HTMLInputElement>(null);
  const isExporting = stage === "exporting" && !exportUrl;
  const isDone = !!exportUrl;

  const handleCopy = () => {
    navigator.clipboard.writeText(onCopyTranscript()).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  if (isDone) {
    return (
      <div
        className="rounded-xl border p-6 animate-fade-in-up"
        style={{ borderColor: "rgba(34,197,94,0.2)", background: "rgba(34,197,94,0.04)" }}
      >
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <div
              className="w-11 h-11 rounded-xl flex items-center justify-center flex-shrink-0"
              style={{ background: "rgba(34,197,94,0.12)", border: "1px solid rgba(34,197,94,0.25)" }}
            >
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                <path d="M5 10l3.5 3.5L15 7" stroke="#22c55e" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </div>
            <div>
              <p className="text-sm font-semibold" style={{ fontFamily: "'Syne', sans-serif", color: "var(--foreground)" }}>
                Export complete
              </p>
              <p className="text-xs mt-0.5" style={{ color: "var(--muted-foreground)" }}>
                {keptSegments} segments · {formatSeconds(keptSeconds)} edited cut
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 w-full sm:w-auto flex-wrap">
            <a
              href={exportUrl!}
              download={`edited_${Date.now()}.mp4`}
              className="flex-1 sm:flex-none flex items-center justify-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium transition-all"
              style={{ background: "var(--keep)", color: "white" }}
              onMouseEnter={(e) => { (e.currentTarget).style.opacity = "0.85"; }}
              onMouseLeave={(e) => { (e.currentTarget).style.opacity = "1"; }}
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M7 1v8M4 6l3 3 3-3M2 11h10" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
              Download MP4
            </a>
            {exportedAudioUrl && (
              <>
                <a
                  href={`${exportedAudioUrl}&download=cut_audio.wav`}
                  className="flex items-center gap-1.5 px-4 py-2.5 rounded-lg text-sm border transition-all"
                  style={{ borderColor: "var(--border)", color: "var(--muted-foreground)", background: "transparent" }}
                  onMouseEnter={(e) => { (e.currentTarget).style.color = "var(--foreground)"; }}
                  onMouseLeave={(e) => { (e.currentTarget).style.color = "var(--muted-foreground)"; }}
                >
                  <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                    <path d="M6.5 1v8M4 6.5l2.5 2.5 2.5-2.5M1.5 11.5h10" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                  Download for Adobe
                </a>
                <input
                  ref={adobeInputRef}
                  type="file"
                  accept="audio/*,.wav,.mp3,.m4a,.aac"
                  className="hidden"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (!file || !onAudioUpload || !onRemux) return;
                    setRemuxing(true);
                    try {
                      await onAudioUpload(file);
                      // onAudioUpload sets customAudioPath in parent; we need to re-fetch it
                      // Instead, upload inline and call remux directly
                      const uploadRes = await fetch("/api/upload-audio", {
                        method: "POST",
                        headers: { "Content-Type": file.type || "audio/wav" },
                        body: file,
                      });
                      if (!uploadRes.ok) throw new Error("Upload failed");
                      const { audioPath } = await uploadRes.json();
                      await onRemux(audioPath);
                    } catch (err) {
                      alert(err instanceof Error ? err.message : "Remux failed");
                    } finally {
                      setRemuxing(false);
                      e.target.value = "";
                    }
                  }}
                />
                <button
                  onClick={() => adobeInputRef.current?.click()}
                  disabled={remuxing}
                  className="flex items-center gap-1.5 px-4 py-2.5 rounded-lg text-sm border transition-all disabled:opacity-50"
                  style={{ borderColor: "var(--border)", color: "var(--muted-foreground)", background: "transparent" }}
                  onMouseEnter={(e) => { if (!remuxing) (e.currentTarget).style.color = "var(--foreground)"; }}
                  onMouseLeave={(e) => { (e.currentTarget).style.color = "var(--muted-foreground)"; }}
                >
                  {remuxing ? (
                    <><div className="spinner" style={{ width: 12, height: 12, borderWidth: 1.5 }} /> Re-rendering…</>
                  ) : (
                    <>
                      <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                        <path d="M6.5 9.5V1.5M4 4l2.5-2.5L9 4M1.5 11.5h10" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
                      </svg>
                      Upload Adobe audio
                    </>
                  )}
                </button>
              </>
            )}
            <button
              onClick={handleCopy}
              className="flex items-center gap-1.5 px-4 py-2.5 rounded-lg text-sm border transition-all"
              style={{
                borderColor: copied ? "rgba(34,197,94,0.4)" : "var(--border)",
                color: copied ? "#22c55e" : "var(--muted-foreground)",
                background: copied ? "rgba(34,197,94,0.06)" : "transparent",
              }}
            >
              {copied ? (
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                  <path d="M2 6l3 3 5-5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              ) : (
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                  <rect x="4" y="1" width="7" height="8.5" rx="1.2" stroke="currentColor" strokeWidth="1.2"/>
                  <path d="M2.5 3.5H2a1 1 0 00-1 1v6a1 1 0 001 1h6a1 1 0 001-1v-.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
                </svg>
              )}
              {copied ? "Copied" : "Copy transcript"}
            </button>
            {/* layout toggle */}
            <div
              className="flex items-center rounded-lg border overflow-hidden"
              style={{ borderColor: "var(--border)" }}
            >
              {(["editorial", "split", "gap"] as const).map((opt, idx, arr) => (
                <button
                  key={opt}
                  onClick={() => setThumbLayout(opt)}
                  className="px-2.5 py-2 text-xs transition-all"
                  style={{
                    background: thumbLayout === opt ? "var(--primary)" : "transparent",
                    color: thumbLayout === opt ? "white" : "var(--muted-foreground)",
                    fontFamily: "'JetBrains Mono', monospace",
                    borderRight: idx < arr.length - 1 ? "1px solid var(--border)" : undefined,
                  }}
                >
                  {opt}
                </button>
              ))}
            </div>
            <button
              onClick={() => onGenerateThumbnails(thumbLayout)}
              disabled={isThumbnailGenerating}
              className="flex items-center gap-1.5 px-4 py-2.5 rounded-lg text-sm border transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              style={{ borderColor: "var(--border)", color: "var(--muted-foreground)", background: "transparent" }}
              onMouseEnter={(e) => { if (!isThumbnailGenerating) (e.currentTarget).style.color = "var(--foreground)"; }}
              onMouseLeave={(e) => { (e.currentTarget).style.color = "var(--muted-foreground)"; }}
            >
              {isThumbnailGenerating ? (
                <>
                  <div className="spinner" style={{ width: 12, height: 12, borderWidth: 1.5 }} />
                  Generating…
                </>
              ) : (
                <>
                  <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                    <rect x="1" y="1" width="11" height="8" rx="1.2" stroke="currentColor" strokeWidth="1.3" />
                    <path d="M3.5 12h6M6.5 9v3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
                  </svg>
                  Thumbnails
                </>
              )}
            </button>
            {onSaveFixture && (
              <button
                onClick={async () => {
                  if (fixtureSaved) return;
                  setFixtureSaving(true);
                  try { await onSaveFixture(); setFixtureSaved(true); } finally { setFixtureSaving(false); }
                }}
                disabled={fixtureSaving || fixtureSaved}
                className="flex items-center gap-1.5 px-4 py-2.5 rounded-lg text-sm border transition-all disabled:opacity-50"
                style={{
                  borderColor: fixtureSaved ? "rgba(34,197,94,0.4)" : "var(--border)",
                  color: fixtureSaved ? "#22c55e" : "var(--muted-foreground)",
                  background: fixtureSaved ? "rgba(34,197,94,0.06)" : "transparent",
                }}
                title="Save this session to fixtures/ for eval runs"
              >
                {fixtureSaved ? (
                  <><svg width="11" height="11" viewBox="0 0 11 11" fill="none"><path d="M2 5.5l2.5 2.5 4.5-4.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/></svg>Saved</>
                ) : fixtureSaving ? (
                  <><div className="spinner" style={{ width: 11, height: 11, borderWidth: 1.5 }} />Saving…</>
                ) : (
                  <><svg width="11" height="11" viewBox="0 0 11 11" fill="none"><path d="M5.5 1v6M3 5l2.5 2.5L8 5M1.5 9.5h8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/></svg>Save fixture</>
                )}
              </button>
            )}
            <button
              onClick={onReExport}
              className="px-4 py-2.5 rounded-lg text-sm border transition-all"
              style={{ borderColor: "var(--border)", color: "var(--muted-foreground)", background: "transparent" }}
              onMouseEnter={(e) => { (e.currentTarget).style.color = "var(--foreground)"; }}
              onMouseLeave={(e) => { (e.currentTarget).style.color = "var(--muted-foreground)"; }}
            >
              Re-export
            </button>
            <button
              onClick={onReset}
              className="px-4 py-2.5 rounded-lg text-sm border transition-all"
              style={{ borderColor: "var(--border)", color: "var(--muted-foreground)", background: "transparent" }}
              onMouseEnter={(e) => { (e.currentTarget).style.color = "var(--foreground)"; }}
              onMouseLeave={(e) => { (e.currentTarget).style.color = "var(--muted-foreground)"; }}
            >
              New video
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (isExporting) {
    return (
      <div
        className="rounded-xl border p-6 animate-fade-in-up"
        style={{ borderColor: "var(--border)", background: "var(--card)" }}
      >
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="spinner" />
              <div>
                <p className="text-sm font-medium" style={{ color: "var(--foreground)" }}>
                  Cutting & stitching your video
                </p>
                <p className="text-xs mt-0.5" style={{ color: "var(--muted-foreground)" }}>
                  Using ffmpeg to concat {keptSegments} segments…
                </p>
              </div>
            </div>
            <span
              className="text-sm"
              style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--muted-foreground)" }}
            >
              {Math.round(progress)}%
            </span>
          </div>

          <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--secondary)" }}>
            <div
              className="h-full rounded-full timeline-bar"
              style={{
                width: `${progress}%`,
                background: "linear-gradient(90deg, var(--primary), #818cf8)",
              }}
            />
          </div>

          <div className="flex gap-3 pt-1">
            {[
              "Slicing kept segments",
              "Re-encoding cuts",
              "Concatenating timeline",
            ].map((step, i) => {
              const active = progress < 30 ? i === 0 : progress < 70 ? i === 1 : i === 2;
              const done = progress >= 30 && i === 0 || progress >= 70 && i === 1;
              return (
                <div key={step} className="flex items-center gap-1.5">
                  <div className="w-3 h-3 flex items-center justify-center">
                    {done ? (
                      <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                        <circle cx="5" cy="5" r="4" fill="rgba(34,197,94,0.15)"/>
                        <path d="M3 5l1.5 1.5L7 3.5" stroke="#22c55e" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/>
                      </svg>
                    ) : active ? (
                      <div className="spinner-sm" />
                    ) : (
                      <div className="w-1 h-1 rounded-full" style={{ background: "var(--border)" }} />
                    )}
                  </div>
                  <span className="text-xs" style={{ color: done ? "#22c55e" : active ? "var(--foreground)" : "var(--muted-foreground)", opacity: done || active ? 1 : 0.45 }}>
                    {step}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  const handleAudioFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !onAudioUpload) return;
    setAudioUploading(true);
    try {
      await onAudioUpload(file);
    } finally {
      setAudioUploading(false);
      e.target.value = "";
    }
  };

  // Edit stage — ready to export
  return (
    <div
      className="rounded-xl border p-5 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4"
      style={{ borderColor: "var(--border)", background: "var(--card)" }}
    >
      <div>
        <div>
          <p className="text-sm font-medium" style={{ color: "var(--foreground)" }}>
            Ready to export
          </p>
          <p className="text-xs mt-0.5" style={{ color: "var(--muted-foreground)" }}>
            {keptSegments} segments · {formatSeconds(keptSeconds)} edited cut · ffmpeg will stitch them together
          </p>
        </div>

        <button
          onClick={onExport}
          disabled={keptSegments === 0}
          className="flex items-center gap-2 px-6 py-2.5 rounded-lg text-sm font-semibold transition-all duration-150 disabled:opacity-40 disabled:cursor-not-allowed"
          style={{
            background: "var(--primary)",
            color: "white",
            fontFamily: "'Syne', sans-serif",
            letterSpacing: "0.02em",
          }}
          onMouseEnter={(e) => { if (keptSegments > 0) (e.currentTarget).style.opacity = "0.85"; }}
          onMouseLeave={(e) => { (e.currentTarget).style.opacity = "1"; }}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M2 7h10M7 2l5 5-5 5" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          Export video
        </button>
      </div>
    </div>
  );
}
