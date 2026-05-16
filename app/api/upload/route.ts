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

  const contentType = req.headers.get("content-type") || "video/mp4";
  const ext = contentType.includes("webm")
    ? "webm"
    : contentType.includes("quicktime")
    ? "mov"
    : contentType.includes("avi")
    ? "avi"
    : "mp4";

  const id = randomUUID();
  const tmpDir = path.join(process.cwd(), "tmp");
  mkdirSync(tmpDir, { recursive: true });
  const filePath = path.join(tmpDir, `${id}.${ext}`);

  // Pipe the web ReadableStream to disk
  const writer = createWriteStream(filePath);

  // Use getReader() directly for compatibility
  const reader = req.body.getReader();
  await new Promise<void>((resolve, reject) => {
    writer.on("error", reject);
    writer.on("finish", resolve);

    async function pump() {
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) {
            writer.end();
            break;
          }
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

  return NextResponse.json({ id, filePath, ext });
}
