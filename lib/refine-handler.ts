import { NextRequest, NextResponse } from "next/server";
import { writeFileSync, unlinkSync, mkdirSync } from "fs";
import { execFile } from "child_process";
import { promisify } from "util";
import path from "path";
import { randomUUID } from "crypto";

const execFileAsync = promisify(execFile);

const POETRY_VENV = "/Users/ashikaverma/Library/Caches/pypoetry/virtualenvs/transcript-editor-py-pSpjchbs-py3.14";
const PYTHON      = path.join(POETRY_VENV, "bin", "python");
const REFINE      = path.join(process.cwd(), "scripts", "refine.py");

async function runRefine(payload: unknown) {
  const tmpDir = path.join(process.cwd(), "tmp");
  mkdirSync(tmpDir, { recursive: true });
  const payloadPath = path.join(tmpDir, `${randomUUID()}_refine.json`);
  writeFileSync(payloadPath, JSON.stringify(payload), "utf8");
  try {
    const { stdout, stderr } = await execFileAsync(
      PYTHON,
      [REFINE, payloadPath],
      {
        maxBuffer: 50 * 1024 * 1024,
        timeout: 600_000,
        env: { ...process.env },
      },
    );
    if (stderr) console.log("Refine:", stderr.slice(0, 2000));
    return JSON.parse(stdout);
  } finally {
    try { unlinkSync(payloadPath); } catch {}
  }
}

export async function handleRefine(req: NextRequest) {
  const { plan, maxIterations, targetCoherence } = await req.json();

  if (!plan?.segments?.length) {
    return NextResponse.json({ error: "Edit plan not found" }, { status: 400 });
  }

  try {
    return NextResponse.json(
      await runRefine({ plan, maxIterations: maxIterations ?? 20, targetCoherence: targetCoherence ?? 95 }),
    );
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("Refine failed:", msg);
    return NextResponse.json(
      { error: "Refine failed: " + msg.slice(0, 300) },
      { status: 500 },
    );
  }
}
