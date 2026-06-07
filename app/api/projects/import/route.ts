import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const formData = await req.formData();
    const file = formData.get("file") as File;

    if (!file) {
      return NextResponse.json({ error: "No file provided" }, { status: 400 });
    }

    const content = await file.text();
    const projectFile = JSON.parse(content);

    if (projectFile.version !== 1) {
      return NextResponse.json({ error: "Unsupported project file version" }, { status: 400 });
    }

    return NextResponse.json({
      plan: projectFile.plan,
      projectName: projectFile.projectName,
      exportedAt: projectFile.exportedAt,
    });
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "Failed to import project" },
      { status: 400 }
    );
  }
}
