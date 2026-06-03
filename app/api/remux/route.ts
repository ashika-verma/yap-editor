import { NextRequest, NextResponse } from "next/server";
import { existsSync, createReadStream, statSync } from "fs";
import { execFile } from "child_process";
import { promisify } from "util";
import path from "path";
import { randomUUID } from "crypto";
import { Readable } from "stream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const execFileAsync = promisify(execFile);

export async function POST(req: NextRequest) {
  const { videoPath, audioPath } = await req.json() as {
    videoPath: string;
    audioPath: string;
  };

  const tmpDir = path.join(process.cwd(), "tmp");

  // Security: both files must be in tmp/
  for (const p of [videoPath, audioPath]) {
    if (!path.resolve(p).startsWith(tmpDir)) {
      return NextResponse.json({ error: "Forbidden" }, { status: 403 });
    }
    if (!existsSync(p)) {
      return NextResponse.json({ error: `File not found: ${path.basename(p)}` }, { status: 404 });
    }
  }

  const outputPath = path.join(tmpDir, `${randomUUID()}_remuxed.mp4`);

  try {
    // Copy video stream, re-encode the new audio track, done in seconds
    await execFileAsync("ffmpeg", [
      "-y",
      "-i", videoPath,
      "-i", audioPath,
      "-map", "0:v:0",
      "-map", "1:a:0",
      "-c:v", "copy",
      "-c:a", "aac", "-b:a", "192k",
      "-shortest",
      "-movflags", "+faststart",
      outputPath,
    ], { timeout: 120_000 });

    const stat = statSync(outputPath);
    const stream = Readable.toWeb(createReadStream(outputPath)) as ReadableStream;

    const response = new NextResponse(stream, {
      headers: {
        "Content-Type": "video/mp4",
        "Content-Length": stat.size.toString(),
        "Content-Disposition": `attachment; filename="edited_adobe_${Date.now()}.mp4"`,
      },
    });

    setTimeout(() => { try { require("fs").unlinkSync(outputPath); } catch {} }, 60_000);
    return response;
  } catch (err: unknown) {
    const e = err as { stderr?: string; message?: string };
    return NextResponse.json(
      { error: "Remux failed: " + (e.stderr ?? e.message ?? String(err)).slice(-500) },
      { status: 500 },
    );
  }
}
