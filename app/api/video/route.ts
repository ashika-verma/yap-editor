import { NextRequest, NextResponse } from "next/server";
import { createReadStream, statSync, existsSync } from "fs";
import path from "path";
import { Readable } from "stream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function mimeType(filePath: string): string {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".wav") return "audio/wav";
  if (ext === ".mp3") return "audio/mpeg";
  if (ext === ".m4a" || ext === ".aac") return "audio/aac";
  if (ext === ".png") return "image/png";
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".gif") return "image/gif";
  if (ext === ".webp") return "image/webp";
  return "video/mp4";
}

export async function GET(req: NextRequest) {
  const filePath = req.nextUrl.searchParams.get("path");
  const download = req.nextUrl.searchParams.get("download"); // filename for attachment

  if (!filePath) {
    return NextResponse.json({ error: "Missing path" }, { status: 400 });
  }

  // Security: only serve files from tmp/ or projects/
  const tmpDir = path.join(process.cwd(), "tmp");
  const projectsDir = path.join(process.cwd(), "projects");
  const resolved = path.resolve(filePath);
  if (!resolved.startsWith(tmpDir) && !resolved.startsWith(projectsDir)) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  if (!existsSync(resolved)) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const stat = statSync(resolved);
  const fileSize = stat.size;
  const contentType = mimeType(resolved);
  const rangeHeader = req.headers.get("range");
  const disposition = download
    ? `attachment; filename="${download}"`
    : undefined;

  if (rangeHeader) {
    const [, rangeStr] = rangeHeader.split("=");
    const [startStr, endStr] = rangeStr.split("-");
    const start = parseInt(startStr, 10);
    const end = endStr ? parseInt(endStr, 10) : fileSize - 1;
    const chunkSize = end - start + 1;

    const stream = createReadStream(resolved, { start, end });
    const webStream = Readable.toWeb(stream) as ReadableStream;

    return new NextResponse(webStream, {
      status: 206,
      headers: {
        "Content-Range": `bytes ${start}-${end}/${fileSize}`,
        "Accept-Ranges": "bytes",
        "Content-Length": chunkSize.toString(),
        "Content-Type": contentType,
        ...(disposition ? { "Content-Disposition": disposition } : {}),
      },
    });
  }

  const stream = createReadStream(resolved);
  const webStream = Readable.toWeb(stream) as ReadableStream;

  return new NextResponse(webStream, {
    status: 200,
    headers: {
      "Content-Length": fileSize.toString(),
      "Accept-Ranges": "bytes",
      "Content-Type": contentType,
      ...(disposition ? { "Content-Disposition": disposition } : {}),
    },
  });
}
