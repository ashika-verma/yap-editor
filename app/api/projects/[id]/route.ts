import { NextRequest, NextResponse } from "next/server";
import { existsSync, readFileSync, rmSync } from "fs";
import path from "path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const PROJECTS_DIR = path.join(process.cwd(), "projects");

function projectDir(id: string) {
  return path.join(PROJECTS_DIR, id);
}

// GET /api/projects/[id] — load a project
export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const dir = projectDir(id);
  const metaPath = path.join(dir, "meta.json");
  const planPath = path.join(dir, "plan.json");

  if (!existsSync(metaPath) || !existsSync(planPath)) {
    return NextResponse.json({ error: "Project not found" }, { status: 404 });
  }

  const meta = JSON.parse(readFileSync(metaPath, "utf8"));
  const plan = JSON.parse(readFileSync(planPath, "utf8"));

  if (!existsSync(meta.videoPath)) {
    return NextResponse.json({ error: "Project video file is missing" }, { status: 404 });
  }

  return NextResponse.json({ meta, plan, filePath: meta.videoPath });
}

// DELETE /api/projects/[id] — delete a project
export async function DELETE(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const dir = projectDir(id);

  if (!existsSync(dir)) {
    return NextResponse.json({ error: "Project not found" }, { status: 404 });
  }

  // Safety: only delete inside the projects directory
  if (!dir.startsWith(PROJECTS_DIR)) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  rmSync(dir, { recursive: true, force: true });
  return NextResponse.json({ ok: true });
}
