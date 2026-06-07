import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();

  if (!user) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  const { plan, projectName } = await req.json();

  if (!plan) {
    return NextResponse.json({ error: "No plan to export" }, { status: 400 });
  }

  const projectFile = {
    version: 1,
    exportedAt: new Date().toISOString(),
    projectName: projectName || "Untitled Project",
    plan,
  };

  // Return as downloadable JSON
  return new NextResponse(JSON.stringify(projectFile, null, 2), {
    headers: {
      "Content-Type": "application/json",
      "Content-Disposition": `attachment; filename="${(projectName || "project").replace(/[^a-z0-9]/gi, "_")}.yap"`,
    },
  });
}
