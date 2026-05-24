"use client";

import { useState } from "react";

export interface ThumbnailData {
  title: string;
  textHook: string;
  imageData: string;
}

interface Props {
  thumbnails: ThumbnailData[];
  onClose: () => void;
}

export function ThumbnailStudio({ thumbnails, onClose }: Props) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

  const handleDownload = (t: ThumbnailData, i: number) => {
    const a = document.createElement("a");
    a.href = t.imageData;
    a.download = `thumbnail_${i + 1}.jpg`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.8)", backdropFilter: "blur(10px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="relative w-full max-w-5xl rounded-2xl border overflow-hidden animate-fade-in-up"
        style={{ background: "var(--background)", borderColor: "var(--border)" }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-6 py-4 border-b"
          style={{ borderColor: "var(--border)" }}
        >
          <div>
            <p
              className="text-sm font-semibold"
              style={{ fontFamily: "'Syne', sans-serif", color: "var(--foreground)" }}
            >
              Thumbnail Studio
            </p>
            <p className="text-xs mt-0.5" style={{ color: "var(--muted-foreground)" }}>
              {thumbnails.length} options generated · click to download
            </p>
          </div>
          <button
            onClick={onClose}
            className="w-7 h-7 flex items-center justify-center rounded-lg border transition-all"
            style={{ borderColor: "var(--border)", color: "var(--muted-foreground)", background: "transparent" }}
            onMouseEnter={(e) => { (e.currentTarget).style.color = "var(--foreground)"; }}
            onMouseLeave={(e) => { (e.currentTarget).style.color = "var(--muted-foreground)"; }}
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        {/* Grid */}
        <div className="p-5 grid grid-cols-3 gap-4 max-h-[80vh] overflow-y-auto">
          {thumbnails.map((t, i) => (
            <div
              key={i}
              className="rounded-xl border overflow-hidden cursor-pointer transition-all duration-150"
              style={{
                borderColor: hoveredIdx === i ? "var(--primary)" : "var(--border)",
                background: "var(--card)",
                boxShadow: hoveredIdx === i ? "0 0 0 2px rgba(99,102,241,0.25)" : "none",
              }}
              onMouseEnter={() => setHoveredIdx(i)}
              onMouseLeave={() => setHoveredIdx(null)}
              onClick={() => handleDownload(t, i)}
            >
              {/* 16:9 image */}
              <div style={{ aspectRatio: "16/9", overflow: "hidden" }}>
                <img
                  src={t.imageData}
                  alt={t.title}
                  className="w-full h-full object-cover"
                  style={{ display: "block" }}
                />
              </div>

              {/* Info row */}
              <div className="px-3 py-2.5 space-y-1.5">
                <p
                  className="text-xs font-medium leading-tight"
                  style={{ color: "var(--foreground)" }}
                >
                  {t.title}
                </p>
                <div className="flex items-center justify-between gap-2">
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded truncate"
                    style={{
                      background: "rgba(99,102,241,0.1)",
                      color: "var(--primary)",
                      fontFamily: "'JetBrains Mono', monospace",
                      maxWidth: "65%",
                    }}
                  >
                    {t.textHook}
                  </span>
                  <div
                    className="flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium flex-shrink-0"
                    style={{ background: "var(--primary)", color: "white" }}
                  >
                    <svg width="9" height="9" viewBox="0 0 9 9" fill="none">
                      <path
                        d="M4.5 1v5M2 4l2.5 2.5L7 4M1 8h7"
                        stroke="white"
                        strokeWidth="1.3"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                    Save
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
