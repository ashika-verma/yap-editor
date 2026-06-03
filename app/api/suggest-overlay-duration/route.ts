import { NextRequest, NextResponse } from "next/server";
import { spawn } from "child_process";
import path from "path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 60;

const PYTHON = path.join(process.cwd(), ".venv", "bin", "python3");
const SCRIPT = path.join(process.cwd(), "scripts", "suggest_overlay_duration.py");

function spawnWithStdin(input: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const proc = spawn(PYTHON, [SCRIPT]);
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => { proc.kill(); reject(new Error("timeout")); }, 55_000);
    proc.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
    proc.stderr.on("data", (d: Buffer) => { stderr += d.toString(); process.stderr.write(d); });
    proc.on("error", (err) => { clearTimeout(timer); reject(err); });
    proc.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) reject(new Error(stderr.slice(-300) || `exit ${code}`));
      else resolve(stdout);
    });
    proc.stdin.write(input, "utf8");
    proc.stdin.end();
  });
}

export async function POST(req: NextRequest) {
  const { sourceAttachSec, segments, imagePath } = await req.json();

  if (typeof sourceAttachSec !== "number" || !Array.isArray(segments)) {
    return NextResponse.json({ error: "Invalid input" }, { status: 400 });
  }

  // Trim to context window before sending to subprocess
  const ctxStart = sourceAttachSec - 5;
  const ctxEnd   = sourceAttachSec + 90;
  const contextSegments = segments.filter(
    (s: { startSec: number; endSec: number }) => s.endSec >= ctxStart && s.startSec <= ctxEnd,
  );

  try {
    const raw = await spawnWithStdin(JSON.stringify({ sourceAttachSec, segments: contextSegments, imagePath: imagePath ?? "" }));
    const result = JSON.parse(raw.trim());
    const duration = Math.max(2, Math.min(30, Number(result.durationSec) || 4));
    return NextResponse.json({
      durationSec: Math.round(duration * 10) / 10,
      sourceEndSec: typeof result.sourceEndSec === "number" ? result.sourceEndSec : null,
      reasoning: result.reasoning ?? "",
      source: result.source ?? "ai",
      error: result.error ?? null,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("suggest_overlay_duration:", msg);
    return NextResponse.json({ durationSec: 4, source: "fallback", error: msg });
  }
}
