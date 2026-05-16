import { NextRequest, NextResponse } from "next/server";
import { existsSync, writeFileSync, unlinkSync, mkdirSync } from "fs";
import { execFile } from "child_process";
import { promisify } from "util";
import path from "path";
import { randomUUID } from "crypto";

const execFileAsync = promisify(execFile);

const POETRY_VENV = "/Users/ashikaverma/Library/Caches/pypoetry/virtualenvs/transcript-editor-py-pSpjchbs-py3.14";
const PYTHON = path.join(POETRY_VENV, "bin", "python");
const REPLAN = path.join(process.cwd(), "scripts", "replan.py");

async function runReplan(payload: unknown) {
  const tmpDir = path.join(process.cwd(), "tmp");
  mkdirSync(tmpDir, { recursive: true });
  const payloadPath = path.join(tmpDir, `${randomUUID()}_replan.json`);
  writeFileSync(payloadPath, JSON.stringify(payload), "utf8");
  try {
    const { stdout, stderr } = await execFileAsync(
      PYTHON,
      [REPLAN, payloadPath],
      {
        maxBuffer: 50 * 1024 * 1024,
        timeout: 600_000,
      },
    );
    if (stderr) console.log("Replan:", stderr.slice(0, 1000));
    return JSON.parse(stdout);
  } finally {
    try { unlinkSync(payloadPath); } catch {}
  }
}

export async function handleReplan(req: NextRequest) {
  const { filePath, plan, fillerSensitivity } = await req.json();

  if (!filePath || !existsSync(filePath)) {
    return NextResponse.json({ error: "Source file not found" }, { status: 400 });
  }

  if (!plan?.segments?.length) {
    return NextResponse.json({ error: "Edit plan not found" }, { status: 400 });
  }

  try {
    return NextResponse.json(await runReplan({ filePath, plan, fillerSensitivity }));
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("Replan failed:", msg);
    return NextResponse.json(
      { error: "Replan failed: " + msg.slice(0, 300) },
      { status: 500 },
    );
  }
}
