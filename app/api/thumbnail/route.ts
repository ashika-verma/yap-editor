import { NextRequest, NextResponse } from "next/server";
import { existsSync, writeFileSync, unlinkSync, mkdirSync } from "fs";
import { execFile } from "child_process";
import { promisify } from "util";
import path from "path";
import { randomUUID } from "crypto";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const execFileAsync = promisify(execFile);

export async function POST(req: NextRequest) {
  const { filePath, plan } = (await req.json()) as {
    filePath: string;
    plan: object;
  };

  if (!filePath || !existsSync(filePath)) {
    return NextResponse.json({ error: "Video file not found" }, { status: 400 });
  }

  const tmpDir = path.join(process.cwd(), "tmp");
  mkdirSync(tmpDir, { recursive: true });

  const planPath = path.join(tmpDir, `${randomUUID()}_thumb_plan.json`);
  writeFileSync(planPath, JSON.stringify(plan));

  const venvPython = path.join(process.cwd(), ".venv", "bin", "python3");
  const pythonBin = existsSync(venvPython) ? venvPython : "python3";
  const scriptPath = path.join(process.cwd(), "scripts", "thumbnail.py");

  try {
    const { stdout, stderr } = await execFileAsync(
      pythonBin,
      [scriptPath, filePath, "--plan-json", planPath, "--out-dir", tmpDir],
      { maxBuffer: 100 * 1024 * 1024, timeout: 180_000 },
    );

    if (stderr) {
      console.log("[thumbnail stderr]", stderr.slice(-800));
    }

    const result = JSON.parse(stdout);
    return NextResponse.json(result);
  } catch (err: unknown) {
    const e = err as { message?: string; stderr?: string };
    console.error("[thumbnail error]", e.message);
    if (e.stderr) console.error("[thumbnail stderr]", e.stderr.slice(-800));
    return NextResponse.json(
      { error: "Thumbnail generation failed: " + (e.message ?? String(err)) },
      { status: 500 },
    );
  } finally {
    try { unlinkSync(planPath); } catch {}
  }
}
