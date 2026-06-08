import { NextRequest, NextResponse } from "next/server";
import { existsSync } from "fs";
import { execFile } from "child_process";
import { promisify } from "util";
import path from "path";
import { randomUUID } from "crypto";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 1800;

const execFileAsync = promisify(execFile);

const PYTHON = path.join(process.cwd(), ".venv", "bin", "python3");
const ORCHESTRATOR = path.join(process.cwd(), "scripts", "orchestrator.py");
const WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo";

export async function POST(req: NextRequest) {
  const { filePath, fillerSensitivity = "balanced", disableVision = false, disableLLM = false } = await req.json();
  if (!filePath || !existsSync(filePath)) {
    return NextResponse.json({ error: "File not found" }, { status: 400 });
  }

  console.log("Running orchestrator pipeline...");

  const orchestratorArgs = [ORCHESTRATOR, filePath, WHISPER_MODEL, fillerSensitivity, "--enhance"];
  if (disableVision) orchestratorArgs.push("--no-vision");
  if (disableLLM) orchestratorArgs.push("--no-llm");

  try {
    const { stdout, stderr } = await execFileAsync(
      PYTHON,
      orchestratorArgs,
      {
        maxBuffer: 50 * 1024 * 1024,
        timeout: 1_800_000,
        env: {
          ...process.env,
          GEMINI_API_KEY: process.env.GEMINI_API_KEY ?? "",
        },
      }
    );

    if (stderr) console.log("Orchestrator:", stderr.slice(0, 1000));

    const result = JSON.parse(stdout);

    if (result.error) {
      return NextResponse.json({ error: result.error }, { status: 422 });
    }

    if (!result.segments?.length) {
      return NextResponse.json({ error: "No speech detected in video" }, { status: 422 });
    }

    if (result.linterIssues?.length) {
      const errors = result.linterIssues.filter((i: { severity: string }) => i.severity === "error");
      if (errors.length > 0) {
        console.warn("Linter errors:", JSON.stringify(errors));
      }
    }

    // Mux enhanced audio into a preview MP4 so the browser preview uses the
    // processed audio rather than the original track.
    let previewVideoPath: string | null = null;
    if (result.enhancedAudioPath && existsSync(result.enhancedAudioPath)) {
      const previewPath = path.join(
        path.dirname(filePath),
        `${randomUUID()}_preview.mp4`,
      );
      try {
        await execFileAsync("ffmpeg", [
          "-y",
          "-i", filePath,
          "-i", result.enhancedAudioPath,
          "-map", "0:v:0",
          "-map", "1:a:0",
          "-c:v", "copy",
          "-c:a", "aac", "-b:a", "128k",
          "-movflags", "+faststart",
          previewPath,
        ], { timeout: 120_000 });
        previewVideoPath = previewPath;
      } catch (e) {
        console.warn("Preview mux failed, falling back to original:", e);
      }
    }

    const issues = result.linterIssues ?? [];
    const plan = {
      version: 1,
      settings: {
        fillerSensitivity: result.settings?.fillerSensitivity ?? fillerSensitivity,
      },
      segments:          result.segments,
      summary:           result.summary,
      rationale:         result.rationale ?? "",
      lowConfidence:     result.lowConfidence ?? false,
      totalDuration:     result.totalDuration,
      editedDuration:    result.editedDuration,
      language:          result.language,
      linterPassed:      result.linterPassed,
      issues,
      linterIssues:      issues,
      directorConfig:    result.directorConfig,
      narrativeAnalysis: result.narrativeAnalysis ?? {},
      enhancedAudioPath: result.enhancedAudioPath ?? null,
    };

    return NextResponse.json({ plan, previewVideoPath });
  } catch (err: unknown) {
    const msg    = err instanceof Error ? err.message : String(err);
    const stderr = (err as { stderr?: string }).stderr ?? "";
    const stdout = (err as { stdout?: string }).stdout ?? "";
    console.error("Orchestrator failed:", msg);
    if (stderr) console.error("Orchestrator stderr:", stderr.slice(-2000));

    // Orchestrator writes JSON errors to stdout — surface those first
    if (stdout) {
      try {
        const parsed = JSON.parse(stdout.trim());
        if (parsed.error) {
          return NextResponse.json({ error: parsed.error }, { status: 500 });
        }
      } catch {}
    }

    const detail = stderr ? stderr.slice(-800) : msg.slice(0, 300);
    return NextResponse.json(
      { error: "Pipeline failed: " + detail },
      { status: 500 }
    );
  }
}
