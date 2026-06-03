import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Finalize just validates and passes the plan through unchanged.
// The pipeline already ran surgeon/linter; re-running them here could silently
// override user edits made in the transcript editor.
export async function POST(req: NextRequest) {
  const { plan } = await req.json();

  if (!plan?.segments?.length) {
    return NextResponse.json({ error: "Edit plan not found" }, { status: 400 });
  }

  return NextResponse.json({ plan });
}
