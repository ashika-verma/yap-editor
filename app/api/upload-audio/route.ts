import { NextRequest, NextResponse } from "next/server";
import { createWriteStream, mkdirSync } from "fs";
import path from "path";
import { randomUUID } from "crypto";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  if (!req.body) {
    return NextResponse.json({ error: "No body" }, { status: 400 });
  }

  const contentType = req.headers.get("content-type") || "audio/wav";
  const ext = contentType.includes("mpeg") || contentType.includes("mp3")
    ? "mp3"
    : contentType.includes("aac") || contentType.includes("m4a")
    ? "m4a"
    : "wav";

  const tmpDir = path.join(process.cwd(), "tmp");
  mkdirSync(tmpDir, { recursive: true });
  const audioPath = path.join(tmpDir, `${randomUUID()}_custom.${ext}`);

  const writer = createWriteStream(audioPath);
  const reader = req.body.getReader();

  await new Promise<void>((resolve, reject) => {
    writer.on("error", reject);
    writer.on("finish", resolve);
    async function pump() {
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) { writer.end(); break; }
          if (!writer.write(value)) {
            await new Promise<void>((r) => writer.once("drain", r));
          }
        }
      } catch (err) {
        writer.destroy(err as Error);
        reject(err);
      }
    }
    pump();
  });

  return NextResponse.json({ audioPath });
}
