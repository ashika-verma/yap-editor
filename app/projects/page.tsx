"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/authContext";
import { supabase } from "@/lib/supabase";
import type { EditPlan } from "@/lib/editPlan";

type ProjectRow = {
  id: string;
  name: string;
  created_at: string;
  data: EditPlan;
};

export default function ProjectsPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);

  useEffect(() => {
    if (!loading && !user) {
      router.push("/auth");
      return;
    }

    if (!user) return;

    const loadProjects = async () => {
      const { data, error } = await supabase
        .from("projects")
        .select("*")
        .eq("user_id", user.id)
        .order("created_at", { ascending: false });

      if (error) {
        console.error("Failed to load projects:", error);
      } else {
        setProjects((data || []) as ProjectRow[]);
      }
      setProjectsLoading(false);
    };

    loadProjects();
  }, [user, loading, router]);

  if (loading || projectsLoading) {
    return <div className="flex items-center justify-center min-h-screen">Loading...</div>;
  }

  return (
    <div className="min-h-screen bg-background">
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
            My Projects
          </span>
        </div>

        <button
          onClick={async () => {
            router.push("/auth");
          }}
          className="text-xs px-3 py-1.5 rounded border transition-all duration-150"
          style={{ borderColor: "var(--border)", color: "var(--muted-foreground)" }}
        >
          Sign out
        </button>
      </header>

      <main className="max-w-4xl mx-auto px-4 sm:px-8 py-10">
        {projects.length === 0 ? (
          <div className="text-center py-20">
            <p className="text-lg text-muted-foreground">No projects yet</p>
            <button
              onClick={() => router.push("/")}
              className="mt-4 px-4 py-2 rounded"
              style={{
                background: "var(--primary)",
                color: "var(--primary-foreground)",
              }}
            >
              Create your first project
            </button>
          </div>
        ) : (
          <div className="grid gap-4">
            {projects.map((project) => (
              <button
                key={project.id}
                onClick={() => router.push(`/?project=${project.id}`)}
                className="p-4 rounded border text-left transition-all hover:border-primary"
                style={{
                  borderColor: "var(--border)",
                  background: "var(--card)",
                }}
              >
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="font-semibold text-foreground">{project.name}</h3>
                    <p className="text-xs text-muted-foreground">
                      {new Date(project.created_at).toLocaleDateString()}
                    </p>
                  </div>
                  <span className="text-xs px-2 py-1 rounded" style={{ background: "var(--secondary)", color: "var(--secondary-foreground)" }}>
                    {project.data.editedDuration}
                  </span>
                </div>
              </button>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
