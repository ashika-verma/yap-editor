import { NextRequest, NextResponse } from "next/server";
import { existsSync } from "fs";
import { execFile } from "child_process";
import { promisify } from "util";
import path from "path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const execFileAsync = promisify(execFile);
const PYTHON = path.join(process.cwd(), ".venv", "bin", "python3");
const SCRIPT  = path.join(process.cwd(), "scripts", "detect_face.py");

export async function GET(req: NextRequest) {
  const filePath = req.nextUrl.searchParams.get("path");
  if (!filePath || !existsSync(filePath)) {
    return NextResponse.json({ face_x: 0.5, detected: false, error: "file not found" });
  }
  try {
    const { stdout } = await execFileAsync(PYTHON, [SCRIPT, filePath], { timeout: 30_000 });
    const result = JSON.parse(stdout.trim());
    return NextResponse.json(result);
  } catch (e) {
    return NextResponse.json({ face_x: 0.5, detected: false, error: String(e) });
  }
}
