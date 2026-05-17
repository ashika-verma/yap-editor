import { NextRequest, NextResponse } from "next/server";
import { writeFileSync, unlinkSync, mkdirSync } from "fs";
import { execFile } from "child_process";
import { promisify } from "util";
import path from "path";
import { randomUUID } from "crypto";

const execFileAsync = promisify(execFile);

const POETRY_VENV = "/Users/ashikaverma/Library/Caches/pypoetry/virtualenvs/transcript-editor-py-pSpjchbs-py3.14";
const PYTHON      = path.join(POETRY_VENV, "bin", "python");
const JUDGE       = path.join(process.cwd(), "scripts", "judge_plan.py");

async function runJudge(payload: unknown) {
  const tmpDir = path.join(process.cwd(), "tmp");
  mkdirSync(tmpDir, { recursive: true });
  const payloadPath = path.join(tmpDir, `${randomUUID()}_judge.json`);
  writeFileSync(payloadPath, JSON.stringify(payload), "utf8");
  try {
    const { stdout, stderr } = await execFileAsync(
      PYTHON,
      [JUDGE, payloadPath],
      {
        maxBuffer: 50 * 1024 * 1024,
        timeout: 600_000,
        env: { ...process.env },
      },
    );
    if (stderr) console.log("Judge:", stderr.slice(0, 1000));
    return JSON.parse(stdout);
  } finally {
    try { unlinkSync(payloadPath); } catch {}
  }
}

export async function handleJudge(req: NextRequest) {
  const { plan } = await req.json();

  if (!plan?.segments?.length) {
    return NextResponse.json({ error: "Edit plan not found" }, { status: 400 });
  }

  try {
    return NextResponse.json(await runJudge({ plan }));
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("Judge failed:", msg);
    return NextResponse.json(
      { error: "Judge failed: " + msg.slice(0, 300) },
      { status: 500 },
    );
  }
}
