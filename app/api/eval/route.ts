import { NextResponse } from "next/server";
import { readdir, readFile } from "fs/promises";
import path from "path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export interface FalseEntry {
  segment_index: number;
  text: string;
  reason: string;
}

export interface FixtureResult {
  fixture: string;
  cut_pct: number;
  coherence: number;
  preservation: number;
  conciseness: number;
  avg: number;
  false_positives: FalseEntry[];
  false_negatives: FalseEntry[];
  overall_notes: string;
}

export interface EvalRun {
  timestamp: string;
  model: string;
  results: FixtureResult[];
}

export async function GET() {
  const evalDir = path.join(process.cwd(), "eval_results");

  let files: string[];
  try {
    files = await readdir(evalDir);
  } catch (err: unknown) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") {
      return NextResponse.json({ runs: [] }, { status: 404 });
    }
    return NextResponse.json({ error: "Failed to read eval_results" }, { status: 500 });
  }

  const jsonFiles = files
    .filter((f) => f.endsWith(".json"))
    .sort()
    .reverse();

  if (jsonFiles.length === 0) {
    return NextResponse.json({ runs: [] });
  }

  const runs: EvalRun[] = [];

  for (const file of jsonFiles) {
    try {
      const raw = await readFile(path.join(evalDir, file), "utf-8");
      const parsed = JSON.parse(raw);
      const timestamp = file.replace(/\.json$/, "");
      runs.push({
        timestamp,
        model: parsed.model ?? "unknown",
        results: parsed.results ?? [],
      });
    } catch {
      // Skip malformed files
    }
  }

  return NextResponse.json({ runs });
}
