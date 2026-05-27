import { NextRequest, NextResponse } from "next/server";
import { existsSync } from "fs";
import { execFile } from "child_process";
import { promisify } from "util";
import path from "path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 600;

const execFileAsync = promisify(execFile);

const POETRY_VENV = "/Users/ashikaverma/Library/Caches/pypoetry/virtualenvs/transcript-editor-py-pSpjchbs-py3.14";
const PYTHON = path.join(POETRY_VENV, "bin", "python");
const ORCHESTRATOR = path.join(process.cwd(), "scripts", "orchestrator.py");
const WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo";

export async function POST(req: NextRequest) {
  const { filePath, fillerSensitivity = "balanced", disableVision = false } = await req.json();
  if (!filePath || !existsSync(filePath)) {
    return NextResponse.json({ error: "File not found" }, { status: 400 });
  }

  console.log("Running orchestrator pipeline...");

  const orchestratorArgs = [ORCHESTRATOR, filePath, WHISPER_MODEL, fillerSensitivity];
  if (disableVision) orchestratorArgs.push("--no-vision");

  try {
    const { stdout, stderr } = await execFileAsync(
      PYTHON,
      orchestratorArgs,
      {
        maxBuffer: 50 * 1024 * 1024,
        timeout: 600_000,
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
    };

    return NextResponse.json({ plan });
  } catch (err: unknown) {
    const msg    = err instanceof Error ? err.message : String(err);
    const stderr = (err as { stderr?: string }).stderr ?? "";
    console.error("Orchestrator failed:", msg);
    if (stderr) console.error("Orchestrator stderr:", stderr.slice(-2000));
    const detail = stderr ? stderr.slice(-800) : msg.slice(0, 300);
    return NextResponse.json(
      { error: "Pipeline failed: " + detail },
      { status: 500 }
    );
  }
}
