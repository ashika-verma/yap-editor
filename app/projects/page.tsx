"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import type { ProjectMeta } from "@/app/api/projects/route";

function formatDate(iso: string) {
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export default function ProjectsPage() {
  const router = useRouter();
  const [projects, setProjects] = useState<ProjectMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/projects")
      .then((r) => r.json())
      .then((d) => setProjects(d.projects ?? []))
      .finally(() => setLoading(false));
  }, []);

  const handleOpen = (id: string) => {
    router.push(`/?project=${id}`);
  };

  const handleDelete = async (id: string, name: string) => {
    if (!confirm(`Delete "${name}"? This cannot be undone.`)) return;
    setDeleting(id);
    try {
      const res = await fetch(`/api/projects/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete");
      setProjects((p) => p.filter((x) => x.id !== id));
      toast.success("Project deleted");
    } catch {
      toast.error("Delete failed");
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div style={{ minHeight: "100vh", background: "var(--background)", color: "var(--foreground)" }}>
      {/* Header */}
      <header
        className="border-b flex items-center justify-between px-8 py-4"
        style={{ borderColor: "var(--border)" }}
      >
        <div className="flex items-center gap-3">
          <div
            className="w-6 h-6 rounded flex items-center justify-center flex-shrink-0"
            style={{ background: "var(--primary)" }}
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M1 1v10M1 6h10M11 2L7 6l4 4" stroke="white" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <span className="font-semibold text-sm" style={{ fontFamily: "'Syne', sans-serif", letterSpacing: "0.05em" }}>
            Yap
          </span>
        </div>
        <button
          onClick={() => router.push("/")}
          className="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg border transition-all"
          style={{ borderColor: "var(--border)", color: "var(--muted-foreground)", background: "transparent" }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.color = "var(--foreground)"; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.color = "var(--muted-foreground)"; }}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M8 3L4 7l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          New video
        </button>
      </header>

      <main className="max-w-[1200px] mx-auto px-8 py-12">
        <div className="mb-8">
          <h1 className="text-2xl font-bold" style={{ fontFamily: "'Syne', sans-serif" }}>Projects</h1>
          <p className="text-sm mt-1" style={{ color: "var(--muted-foreground)" }}>
            Your saved editing sessions
          </p>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-24" style={{ color: "var(--muted-foreground)" }}>
            <div className="w-5 h-5 rounded-full border-2 animate-spin" style={{ borderTopColor: "var(--primary)", borderRightColor: "var(--border)", borderBottomColor: "var(--border)", borderLeftColor: "var(--border)" }} />
          </div>
        ) : projects.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 gap-4 text-center">
            <div className="w-12 h-12 rounded-xl border flex items-center justify-center" style={{ borderColor: "var(--border)" }}>
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                <path d="M3 3v14M3 10h14M17 5L11 10l6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.4"/>
              </svg>
            </div>
            <div>
              <p className="font-medium">No saved projects yet</p>
              <p className="text-sm mt-1" style={{ color: "var(--muted-foreground)" }}>
                Drop a video and hit Save to keep your work here
              </p>
            </div>
            <button
              onClick={() => router.push("/")}
              className="px-4 py-2 rounded-lg text-sm font-medium"
              style={{ background: "var(--primary)", color: "white" }}
            >
              Start editing
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {projects.map((p) => (
              <div
                key={p.id}
                className="rounded-xl border overflow-hidden flex flex-col"
                style={{ borderColor: "var(--border)", background: "var(--card)" }}
              >
                {/* Thumbnail */}
                <div
                  className="relative"
                  style={{ aspectRatio: "16/9", background: "var(--muted)", overflow: "hidden" }}
                >
                  {p.hasThumbnail ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={`/api/projects/${p.id}/thumbnail`}
                      alt={p.name}
                      style={{ width: "100%", height: "100%", objectFit: "cover" }}
                    />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center" style={{ color: "var(--muted-foreground)" }}>
                      <svg width="32" height="32" viewBox="0 0 32 32" fill="none" opacity="0.3">
                        <rect x="3" y="6" width="26" height="20" rx="3" stroke="currentColor" strokeWidth="2"/>
                        <path d="M13 12l7 4-7 4V12z" fill="currentColor"/>
                      </svg>
                    </div>
                  )}
                </div>

                {/* Info */}
                <div className="p-4 flex flex-col gap-3 flex-1">
                  <div>
                    <p className="font-medium text-sm truncate" title={p.name}>{p.name}</p>
                    <p className="text-xs mt-0.5" style={{ color: "var(--muted-foreground)" }}>{formatDate(p.savedAt)}</p>
                  </div>

                  <div className="flex gap-3 text-xs" style={{ color: "var(--muted-foreground)" }}>
                    <span>{p.originalDuration} → <span style={{ color: "var(--keep)" }}>{p.editedDuration}</span></span>
                    <span>·</span>
                    <span>{p.segmentsKept}/{p.segmentsTotal} segments</span>
                  </div>

                  <div className="flex gap-2 mt-auto">
                    <button
                      onClick={() => handleOpen(p.id)}
                      className="flex-1 py-2 rounded-lg text-sm font-medium"
                      style={{ background: "var(--primary)", color: "white" }}
                    >
                      Open
                    </button>
                    <button
                      onClick={() => handleDelete(p.id, p.name)}
                      disabled={deleting === p.id}
                      className="px-3 py-2 rounded-lg text-sm border transition-all"
                      style={{ borderColor: "var(--border)", color: "var(--muted-foreground)", background: "transparent" }}
                      onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.color = "#ef4444"; (e.currentTarget as HTMLButtonElement).style.borderColor = "rgba(239,68,68,0.4)"; }}
                      onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.color = "var(--muted-foreground)"; (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--border)"; }}
                    >
                      {deleting === p.id ? "…" : (
                        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                          <path d="M2 3.5h10M5.5 3.5V2.5h3v1M5 3.5v7.5c0 .3.2.5.5.5h3c.3 0 .5-.2.5-.5V3.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
                        </svg>
                      )}
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
