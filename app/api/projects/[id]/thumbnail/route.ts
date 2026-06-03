import { NextRequest, NextResponse } from "next/server";
import { existsSync, readFileSync } from "fs";
import path from "path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const PROJECTS_DIR = path.join(process.cwd(), "projects");

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const thumbPath = path.join(PROJECTS_DIR, id, "thumbnail.jpg");

  if (!existsSync(thumbPath)) {
    return new NextResponse(null, { status: 404 });
  }

  const buf = readFileSync(thumbPath);
  return new NextResponse(buf, {
    headers: { "Content-Type": "image/jpeg", "Cache-Control": "public, max-age=31536000, immutable" },
  });
}
