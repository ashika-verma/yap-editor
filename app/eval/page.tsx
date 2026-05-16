"use client";

import { useEffect, useState } from "react";

interface FalseEntry {
  segment_index: number;
  text: string;
  reason: string;
}

interface FixtureResult {
  fixture: string;
  cut_pct: number;
  coherence: number;
  preservation: number;
  conciseness: number;
  avg: number;
  false_positives: FalseEntry[];
  false_negatives: FalseEntry[];
  overall_notes: string;
}

interface EvalRun {
  timestamp: string;
  model: string;
  results: FixtureResult[];
}

// Returns color style for 1–5 integer scores and avg float
function scorePill(value: number): { background: string; color: string; border: string } {
  if (value < 2.5) {
    return {
      background: "rgba(239,68,68,0.1)",
      color: "#ef4444",
      border: "rgba(239,68,68,0.3)",
    };
  }
  if (value < 3.5) {
    return {
      background: "rgba(245,158,11,0.1)",
      color: "#f59e0b",
      border: "rgba(245,158,11,0.3)",
    };
  }
  return {
    background: "rgba(34,197,94,0.08)",
    color: "#22c55e",
    border: "rgba(34,197,94,0.25)",
  };
}

function ScorePill({ value }: { value: number }) {
  const { background, color, border } = scorePill(value);
  return (
    <span
      style={{
        background,
        color,
        border: `1px solid ${border}`,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 12,
        fontWeight: 500,
        padding: "2px 8px",
        borderRadius: 4,
        display: "inline-block",
        minWidth: 36,
        textAlign: "center",
      }}
    >
      {value % 1 === 0 ? value.toFixed(0) : value.toFixed(1)}
    </span>
  );
}

function CutPct({ value }: { value: number }) {
  return (
    <span
      style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 12,
        color: "var(--muted-foreground)",
        fontWeight: 400,
      }}
    >
      {value}%
    </span>
  );
}

function FalseEntryList({ entries, label }: { entries: FalseEntry[]; label: string }) {
  if (entries.length === 0) return null;
  return (
    <div style={{ marginTop: 12 }}>
      <p
        style={{
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: "var(--muted-foreground)",
          marginBottom: 6,
          fontFamily: "'DM Sans', sans-serif",
        }}
      >
        {label}
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {entries.map((e, i) => (
          <div
            key={i}
            style={{
              background: "var(--secondary)",
              borderRadius: 6,
              padding: "10px 12px",
              borderLeft: `3px solid ${label.startsWith("False") ? "rgba(239,68,68,0.5)" : "rgba(245,158,11,0.5)"}`,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
              <span
                style={{
                  fontFamily: "'JetBrains Mono', monospace",
                  fontSize: 10,
                  color: "var(--muted-foreground)",
                  background: "rgba(255,255,255,0.04)",
                  padding: "1px 6px",
                  borderRadius: 3,
                }}
              >
                seg {e.segment_index}
              </span>
            </div>
            <p style={{ fontSize: 13, color: "var(--foreground)", margin: 0, lineHeight: 1.5 }}>
              &ldquo;{e.text}&rdquo;
            </p>
            <p
              style={{
                fontSize: 12,
                color: "var(--muted-foreground)",
                margin: "4px 0 0",
                lineHeight: 1.5,
              }}
            >
              {e.reason}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

function FixtureRow({ result }: { result: FixtureResult }) {
  const hasFPs = result.false_positives?.length > 0;
  const hasFNs = result.false_negatives?.length > 0;
  const hasNotes = !!result.overall_notes;
  const hasDetails = hasFPs || hasFNs || hasNotes;

  return (
    <div
      style={{
        borderBottom: "1px solid var(--border)",
      }}
    >
      {/* Score row */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 80px 80px 88px 88px 72px",
          alignItems: "center",
          gap: 8,
          padding: "10px 16px",
        }}
      >
        <span
          style={{
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 12,
            color: "var(--foreground)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={result.fixture}
        >
          {result.fixture}
        </span>
        <div style={{ textAlign: "center" }}>
          <CutPct value={result.cut_pct} />
        </div>
        <div style={{ textAlign: "center" }}>
          <ScorePill value={result.coherence} />
        </div>
        <div style={{ textAlign: "center" }}>
          <ScorePill value={result.preservation} />
        </div>
        <div style={{ textAlign: "center" }}>
          <ScorePill value={result.conciseness} />
        </div>
        <div style={{ textAlign: "center" }}>
          <ScorePill value={result.avg} />
        </div>
      </div>

      {/* Expandable details */}
      {hasDetails && (
        <details
          style={{
            padding: "0 16px",
            marginBottom: 10,
          }}
        >
          <summary
            style={{
              cursor: "pointer",
              fontSize: 11,
              color: "var(--muted-foreground)",
              letterSpacing: "0.04em",
              userSelect: "none",
              listStyle: "none",
              display: "flex",
              alignItems: "center",
              gap: 6,
              paddingBottom: 2,
            }}
          >
            <svg
              width="10"
              height="10"
              viewBox="0 0 10 10"
              fill="none"
              style={{ flexShrink: 0, transition: "transform 0.15s" }}
            >
              <path d="M2 3.5L5 6.5L8 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span>
              {[
                hasFPs && `${result.false_positives.length} false positive${result.false_positives.length !== 1 ? "s" : ""}`,
                hasFNs && `${result.false_negatives.length} false negative${result.false_negatives.length !== 1 ? "s" : ""}`,
              ]
                .filter(Boolean)
                .join(" · ")}
              {hasNotes && (hasFPs || hasFNs) ? " · notes" : hasNotes ? "notes" : ""}
            </span>
          </summary>
          <div style={{ paddingTop: 8, paddingBottom: 12 }}>
            <FalseEntryList
              entries={result.false_positives ?? []}
              label="False positives — good content cut"
            />
            <FalseEntryList
              entries={result.false_negatives ?? []}
              label="Flab that survived"
            />
            {hasNotes && (
              <div style={{ marginTop: 12 }}>
                <p
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    letterSpacing: "0.06em",
                    textTransform: "uppercase",
                    color: "var(--muted-foreground)",
                    marginBottom: 6,
                    fontFamily: "'DM Sans', sans-serif",
                  }}
                >
                  Overall notes
                </p>
                <p
                  style={{
                    fontSize: 13,
                    color: "var(--foreground)",
                    lineHeight: 1.6,
                    margin: 0,
                  }}
                >
                  {result.overall_notes}
                </p>
              </div>
            )}
          </div>
        </details>
      )}
    </div>
  );
}

function RunCard({ run }: { run: EvalRun }) {
  // Format timestamp: "2026-05-16_103037" → "2026-05-16 10:30:37"
  const displayTimestamp = run.timestamp.replace(/_(\d{2})(\d{2})(\d{2})$/, " $1:$2:$3");

  return (
    <div
      className="animate-fade-in-up"
      style={{
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-lg)",
        overflow: "hidden",
      }}
    >
      {/* Run header */}
      <div
        style={{
          padding: "14px 16px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: "var(--primary)",
              flexShrink: 0,
            }}
          />
          <span
            style={{
              fontFamily: "'Syne', sans-serif",
              fontWeight: 600,
              fontSize: 14,
              color: "var(--foreground)",
            }}
          >
            {run.model}
          </span>
        </div>
        <span
          style={{
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 11,
            color: "var(--muted-foreground)",
          }}
        >
          {displayTimestamp}
        </span>
      </div>

      {/* Column headers */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 80px 80px 88px 88px 72px",
          gap: 8,
          padding: "8px 16px",
          borderBottom: "1px solid var(--border)",
          background: "rgba(255,255,255,0.015)",
        }}
      >
        {["Fixture", "Cut%", "Coherence", "Preservation", "Conciseness", "Avg"].map((h) => (
          <span
            key={h}
            style={{
              fontSize: 10,
              fontWeight: 600,
              letterSpacing: "0.07em",
              textTransform: "uppercase",
              color: "var(--muted-foreground)",
              fontFamily: "'DM Sans', sans-serif",
              textAlign: h === "Fixture" ? "left" : "center",
            }}
          >
            {h}
          </span>
        ))}
      </div>

      {/* Fixture rows */}
      {run.results.length === 0 ? (
        <p
          style={{
            padding: "20px 16px",
            fontSize: 13,
            color: "var(--muted-foreground)",
            margin: 0,
          }}
        >
          No fixture results in this run.
        </p>
      ) : (
        run.results.map((r, i) => <FixtureRow key={`${r.fixture}-${i}`} result={r} />)
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "50vh",
        gap: 20,
        textAlign: "center",
      }}
    >
      <div
        style={{
          width: 48,
          height: 48,
          borderRadius: 12,
          background: "var(--card)",
          border: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
          <rect x="3" y="3" width="16" height="16" rx="2" stroke="var(--muted-foreground)" strokeWidth="1.5" />
          <path d="M7 8h8M7 11h5M7 14h6" stroke="var(--muted-foreground)" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </div>
      <div style={{ maxWidth: 400 }}>
        <p
          style={{
            fontFamily: "'Syne', sans-serif",
            fontWeight: 700,
            fontSize: 20,
            color: "var(--foreground)",
            margin: "0 0 8px",
          }}
        >
          No eval runs yet
        </p>
        <p style={{ fontSize: 14, color: "var(--muted-foreground)", margin: 0, lineHeight: 1.6 }}>
          Run the eval script to populate <code
            style={{
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 12,
              background: "var(--secondary)",
              padding: "1px 6px",
              borderRadius: 4,
              border: "1px solid var(--border)",
            }}
          >eval_results/</code> with JSON result files.
        </p>
        <p
          style={{
            marginTop: 12,
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 12,
            color: "var(--muted-foreground)",
            background: "var(--secondary)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            padding: "10px 14px",
            display: "inline-block",
          }}
        >
          python scripts/eval.py
        </p>
      </div>
    </div>
  );
}

export default function EvalPage() {
  const [runs, setRuns] = useState<EvalRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/eval")
      .then(async (res) => {
        const data = await res.json();
        setRuns(data.runs ?? []);
      })
      .catch(() => {
        setError("Failed to load eval results.");
      });
  }, []);

  return (
    <div style={{ minHeight: "100vh", background: "var(--background)" }}>
      {/* Header — mirrors app/page.tsx sticky header */}
      <header
        style={{
          borderBottom: "1px solid var(--border)",
          background: "rgba(8,8,9,0.9)",
          backdropFilter: "blur(8px)",
          position: "sticky",
          top: 0,
          zIndex: 50,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "16px 24px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div
            style={{
              width: 24,
              height: 24,
              borderRadius: 4,
              background: "var(--primary)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
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
            style={{
              fontFamily: "'Syne', sans-serif",
              fontWeight: 600,
              fontSize: 14,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              color: "var(--foreground)",
            }}
          >
            Yap Editor
          </span>
        </div>

        <a
          href="/"
          style={{
            fontSize: 12,
            color: "var(--muted-foreground)",
            textDecoration: "none",
            padding: "6px 12px",
            border: "1px solid var(--border)",
            borderRadius: 6,
            transition: "color 0.15s",
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLAnchorElement).style.color = "var(--foreground)";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLAnchorElement).style.color = "var(--muted-foreground)";
          }}
        >
          &larr; Editor
        </a>
      </header>

      {/* Main */}
      <main
        style={{
          maxWidth: 960,
          margin: "0 auto",
          padding: "40px 32px",
        }}
      >
        {/* Page heading */}
        <div className="animate-fade-in-up" style={{ marginBottom: 32 }}>
          <h1
            style={{
              fontFamily: "'Syne', sans-serif",
              fontWeight: 800,
              fontSize: 28,
              color: "var(--foreground)",
              margin: "0 0 6px",
              letterSpacing: "-0.02em",
            }}
          >
            Eval Results
          </h1>
          {runs !== null && (
            <p style={{ fontSize: 13, color: "var(--muted-foreground)", margin: 0 }}>
              {runs.length === 0
                ? "No runs found"
                : `${runs.length} run${runs.length !== 1 ? "s" : ""} · ${runs.reduce((acc, r) => acc + r.results.length, 0)} fixture${runs.reduce((acc, r) => acc + r.results.length, 0) !== 1 ? "s" : ""} total`}
            </p>
          )}
        </div>

        {/* Loading */}
        {runs === null && !error && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              color: "var(--muted-foreground)",
              fontSize: 13,
            }}
          >
            <div className="spinner" />
            Loading results…
          </div>
        )}

        {/* Error */}
        {error && (
          <div
            style={{
              padding: "14px 16px",
              borderRadius: 8,
              background: "rgba(239,68,68,0.08)",
              border: "1px solid rgba(239,68,68,0.2)",
              fontSize: 13,
              color: "#ef4444",
            }}
          >
            {error}
          </div>
        )}

        {/* Empty state */}
        {runs !== null && runs.length === 0 && <EmptyState />}

        {/* Run cards */}
        {runs !== null && runs.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
            {runs.map((run) => (
              <RunCard key={run.timestamp} run={run} />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
