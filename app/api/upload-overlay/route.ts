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

  const contentType = req.headers.get("content-type") || "image/png";
  const ext = contentType.includes("jpeg") || contentType.includes("jpg")
    ? "jpg"
    : contentType.includes("gif")
    ? "gif"
    : contentType.includes("webp")
    ? "webp"
    : "png";

  const tmpDir = path.join(process.cwd(), "tmp");
  mkdirSync(tmpDir, { recursive: true });
  const id = randomUUID();
  const imagePath = path.join(tmpDir, `${id}_overlay.${ext}`);

  const writer = createWriteStream(imagePath);
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

  const imageUrl = `/api/video?path=${encodeURIComponent(imagePath)}`;
  return NextResponse.json({ imagePath, imageUrl, id });
}
