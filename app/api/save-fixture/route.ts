import { NextRequest, NextResponse } from "next/server";
import { existsSync, mkdirSync, writeFileSync } from "fs";
import { createHash } from "crypto";
import path from "path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const FIXTURES_DIR = path.join(process.cwd(), "fixtures");

export async function POST(req: NextRequest) {
  const { filePath, plan, originalPlan, humanTranscript } = await req.json();

  if (!filePath || !plan || !originalPlan) {
    return NextResponse.json({ error: "Missing required fields" }, { status: 400 });
  }

  if (!existsSync(FIXTURES_DIR)) mkdirSync(FIXTURES_DIR, { recursive: true });

  // Match existing fixture naming: {8-char hash of videoPath}_{timestamp}.json
  const hash = createHash("md5").update(filePath).digest("hex").slice(0, 8);
  const ts = new Date().toISOString().replace(/[-T:]/g, "").slice(0, 15).replace(".", "_");
  const filename = `${hash}_${ts}_human.json`;
  const outPath = path.join(FIXTURES_DIR, filename);

  const fixture = {
    metadata: {
      source: "ui_save",
      captured_at: new Date().toISOString(),
      videoPath: filePath,
      whisper_model: originalPlan.settings?.whisperModel ?? "unknown",
      filler_sensitivity: originalPlan.settings?.fillerSensitivity ?? "balanced",
      no_vision: originalPlan.settings?.noVision ?? true,
      version: 1,
    },
    // Original AI plan — same structure as --save-fixture so eval.py can score it
    plan: originalPlan,
    // User's final approved edits — for measuring AI accuracy vs human judgment
    humanEdits: plan,
    // Flat final transcript text for quick inspection
    humanTranscript: humanTranscript ?? "",
  };

  writeFileSync(outPath, JSON.stringify(fixture, null, 2));

  return NextResponse.json({ ok: true, filename });
}
