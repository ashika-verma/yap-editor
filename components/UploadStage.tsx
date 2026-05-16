"use client";

import { useRef, useState, DragEvent } from "react";

interface Props {
  onUpload: (file: File) => void;
  uploadProgress: number;
  disableVision: boolean;
  onDisableVisionChange: (v: boolean) => void;
}

const ACCEPTED = ["video/mp4", "video/quicktime", "video/webm", "video/avi", "video/x-msvideo"];

export function UploadStage({ onUpload, uploadProgress, disableVision, onDisableVisionChange }: Props) {
  const [dragging, setDragging] = useState(false);
  const [selected, setSelected] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = (file: File) => {
    if (!ACCEPTED.includes(file.type)) {
      alert("Please upload a video file (MP4, MOV, WebM, AVI)");
      return;
    }
    setSelected(file);
    setUploading(true);
    onUpload(file);
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  };

  const fmt = (bytes: number) => {
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  };

  return (
    <div className="min-h-[65vh] flex flex-col items-center justify-center gap-10">
      {/* Hero text */}
      <div className="text-center space-y-4 max-w-lg">
        <div
          className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs border mb-2"
          style={{
            borderColor: "rgba(99,102,241,0.3)",
            background: "rgba(99,102,241,0.08)",
            color: "var(--primary)",
            fontFamily: "'JetBrains Mono', monospace",
          }}
        >
          <div className="w-1.5 h-1.5 rounded-full" style={{ background: "var(--primary)" }} />
          MLX-Whisper + Gemini 2.5 Flash
        </div>
        <h1
          className="text-4xl sm:text-5xl font-bold leading-tight"
          style={{ fontFamily: "'Syne', sans-serif" }}
        >
          Drop your yap.
          <br />
          <span style={{ color: "var(--muted-foreground)", fontWeight: 400 }}>
            Get the gold.
          </span>
        </h1>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
          Upload a video up to ~15 minutes. Gemini transcribes it, finds the narrative,
          cuts the fluff — you get an edited cut ready to download.
        </p>
      </div>

      {/* Drop zone */}
      <div
        className={`w-full max-w-xl relative rounded-2xl border-2 border-dashed transition-all duration-200 cursor-pointer ${dragging ? "drag-over" : ""}`}
        style={{
          borderColor: dragging ? "var(--primary)" : "var(--border)",
          background: dragging ? "rgba(99,102,241,0.05)" : "var(--card)",
          minHeight: 220,
        }}
        onClick={() => !uploading && inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED.join(",")}
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFile(file);
          }}
        />

        <div className="flex flex-col items-center justify-center h-full py-14 px-8 text-center gap-4">
          {!selected ? (
            <>
              <div
                className="w-14 h-14 rounded-2xl flex items-center justify-center"
                style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}
              >
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                  <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" stroke="var(--muted-foreground)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  <polyline points="17 8 12 3 7 8" stroke="var(--muted-foreground)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  <line x1="12" y1="3" x2="12" y2="15" stroke="var(--muted-foreground)" strokeWidth="1.5" strokeLinecap="round"/>
                </svg>
              </div>
              <div>
                <p className="text-sm font-medium" style={{ color: "var(--foreground)" }}>
                  Drag & drop your video here
                </p>
                <p className="text-xs mt-1" style={{ color: "var(--muted-foreground)" }}>
                  MP4, MOV, WebM, AVI · up to ~2 GB
                </p>
              </div>
              <button
                className="mt-2 px-5 py-2 rounded-lg text-sm font-medium transition-all duration-150"
                style={{
                  background: "var(--primary)",
                  color: "white",
                }}
                onMouseEnter={(e) => { (e.currentTarget).style.opacity = "0.85"; }}
                onMouseLeave={(e) => { (e.currentTarget).style.opacity = "1"; }}
              >
                Choose file
              </button>
            </>
          ) : (
            <div className="w-full space-y-4">
              <div className="flex items-center gap-3">
                <div
                  className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
                  style={{ background: "rgba(99,102,241,0.12)", border: "1px solid rgba(99,102,241,0.25)" }}
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                    <rect x="2" y="2" width="20" height="20" rx="3" stroke="var(--primary)" strokeWidth="1.5"/>
                    <polygon points="10,8 16,12 10,16" fill="var(--primary)"/>
                  </svg>
                </div>
                <div className="text-left flex-1 min-w-0">
                  <p className="text-sm font-medium truncate" style={{ color: "var(--foreground)" }}>
                    {selected.name}
                  </p>
                  <p className="text-xs" style={{ color: "var(--muted-foreground)" }}>
                    {fmt(selected.size)}
                  </p>
                </div>
                {uploadProgress === 100 ? (
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <circle cx="8" cy="8" r="7" fill="rgba(34,197,94,0.15)"/>
                    <path d="M5 8l2 2 4-4" stroke="#22c55e" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                ) : (
                  <div className="spinner" style={{ width: 14, height: 14 }} />
                )}
              </div>

              {/* Progress bar */}
              <div className="space-y-1.5">
                <div
                  className="h-1 rounded-full overflow-hidden"
                  style={{ background: "var(--secondary)" }}
                >
                  <div
                    className="h-full rounded-full timeline-bar"
                    style={{
                      width: `${uploadProgress}%`,
                      background: uploadProgress === 100
                        ? "#22c55e"
                        : "linear-gradient(90deg, var(--primary), #818cf8)",
                    }}
                  />
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-xs" style={{ color: "var(--muted-foreground)", fontFamily: "'JetBrains Mono', monospace" }}>
                    {uploadProgress < 100 ? "Uploading…" : "Uploaded · Sending to Gemini…"}
                  </span>
                  <span className="text-xs" style={{ color: "var(--muted-foreground)", fontFamily: "'JetBrains Mono', monospace" }}>
                    {uploadProgress}%
                  </span>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Settings */}
      <button
        type="button"
        onClick={() => onDisableVisionChange(!disableVision)}
        className="flex items-center gap-2.5 text-xs transition-opacity"
        style={{ color: "var(--muted-foreground)", opacity: uploading ? 0.4 : 1 }}
        disabled={uploading}
      >
        <span
          className="relative inline-flex items-center"
          style={{
            width: 32, height: 18,
            borderRadius: 9,
            background: disableVision ? "var(--primary)" : "var(--border)",
            transition: "background 0.2s",
            flexShrink: 0,
          }}
        >
          <span
            style={{
              position: "absolute",
              left: disableVision ? 16 : 2,
              width: 14, height: 14,
              borderRadius: "50%",
              background: "white",
              transition: "left 0.2s",
              boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
            }}
          />
        </span>
        Skip Gemma visual scan
        <span style={{ color: "var(--muted-foreground)", opacity: 0.6 }}>
          (faster, no visual tags)
        </span>
      </button>

      {/* Feature list */}
      <div className="flex flex-wrap justify-center gap-3">
        {[
          { icon: "✦", label: "Timestamped transcript" },
          { icon: "◆", label: "Filler word detection" },
          { icon: "▲", label: "Narrative extraction" },
          { icon: "●", label: "ffmpeg video export" },
        ].map((f) => (
          <div
            key={f.label}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs"
            style={{ background: "var(--secondary)", color: "var(--muted-foreground)" }}
          >
            <span style={{ color: "var(--primary)", fontSize: "8px" }}>{f.icon}</span>
            {f.label}
          </div>
        ))}
      </div>
    </div>
  );
}
