import { NextRequest, NextResponse } from "next/server";
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync, copyFileSync } from "fs";
import { execFile } from "child_process";
import { promisify } from "util";
import path from "path";
import { randomUUID } from "crypto";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const execFileAsync = promisify(execFile);

const PROJECTS_DIR = path.join(process.cwd(), "projects");

function ensureProjectsDir() {
  mkdirSync(PROJECTS_DIR, { recursive: true });
}

export interface ProjectMeta {
  id: string;
  name: string;
  savedAt: string;
  originalDuration: string;
  editedDuration: string;
  segmentsKept: number;
  segmentsTotal: number;
  videoPath: string;
  enhancedAudioPath: string | null;
  hasThumbnail: boolean;
}

// GET /api/projects — list all saved projects, newest first
export async function GET() {
  ensureProjectsDir();
  const projects: ProjectMeta[] = [];

  for (const id of readdirSync(PROJECTS_DIR)) {
    const metaPath = path.join(PROJECTS_DIR, id, "meta.json");
    if (!existsSync(metaPath)) continue;
    try {
      projects.push(JSON.parse(readFileSync(metaPath, "utf8")));
    } catch {}
  }

  projects.sort((a, b) => new Date(b.savedAt).getTime() - new Date(a.savedAt).getTime());
  return NextResponse.json({ projects });
}

// POST /api/projects — save a project
export async function POST(req: NextRequest) {
  ensureProjectsDir();
  const { filePath, plan, name } = await req.json();

  if (!filePath || !existsSync(filePath)) {
    return NextResponse.json({ error: "Video file not found" }, { status: 400 });
  }
  if (!plan?.segments?.length) {
    return NextResponse.json({ error: "No plan to save" }, { status: 400 });
  }

  const id = randomUUID();
  const projectDir = path.join(PROJECTS_DIR, id);
  mkdirSync(projectDir, { recursive: true });

  // Copy video
  const ext = path.extname(filePath) || ".mp4";
  const savedVideoPath = path.join(projectDir, `video${ext}`);
  copyFileSync(filePath, savedVideoPath);

  // Copy enhanced audio if present
  let savedEnhancedPath: string | null = null;
  const enhancedSrc = plan.enhancedAudioPath;
  if (enhancedSrc && existsSync(enhancedSrc)) {
    savedEnhancedPath = path.join(projectDir, "enhanced.wav");
    copyFileSync(enhancedSrc, savedEnhancedPath);
  }

  // Extract thumbnail (best-effort)
  const thumbnailPath = path.join(projectDir, "thumbnail.jpg");
  let hasThumbnail = false;
  try {
    await execFileAsync("ffmpeg", [
      "-y", "-i", savedVideoPath,
      "-ss", "00:00:02",
      "-vframes", "1",
      "-q:v", "4",
      "-vf", "scale=480:-1",
      thumbnailPath,
    ], { timeout: 15_000 });
    hasThumbnail = existsSync(thumbnailPath);
  } catch {}

  // Save plan with updated paths
  const savedPlan = {
    ...plan,
    enhancedAudioPath: savedEnhancedPath,
  };
  writeFileSync(path.join(projectDir, "plan.json"), JSON.stringify(savedPlan), "utf8");

  // Build meta
  const keptSegs = plan.segments.filter((s: { keep: boolean }) => s.keep);
  const meta: ProjectMeta = {
    id,
    name: name || path.basename(filePath, ext),
    savedAt: new Date().toISOString(),
    originalDuration: plan.totalDuration ?? "",
    editedDuration: plan.editedDuration ?? "",
    segmentsKept: keptSegs.length,
    segmentsTotal: plan.segments.length,
    videoPath: savedVideoPath,
    enhancedAudioPath: savedEnhancedPath,
    hasThumbnail,
  };
  writeFileSync(path.join(projectDir, "meta.json"), JSON.stringify(meta, null, 2), "utf8");

  return NextResponse.json({ id, meta });
}
