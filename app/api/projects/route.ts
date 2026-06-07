import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// POST /api/projects — save a project to Supabase
export async function POST(req: NextRequest) {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();

  if (!user) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  const { filePath, plan, projectId, projectName: suppliedName } = await req.json();

  if (!plan?.segments?.length) {
    return NextResponse.json({ error: "No plan to save" }, { status: 400 });
  }

  try {
    const projectName = suppliedName || plan.summary || `Project ${new Date().toLocaleDateString()}`;

    // Embed videoPath into the stored plan data so we can retrieve it on load
    const storedData = filePath ? { ...plan, videoPath: filePath } : plan;

    // If projectId provided, update existing project; otherwise insert new
    if (projectId) {
      const { error } = await supabase
        .from("projects")
        .update({
          name: projectName,
          data: storedData,
        })
        .eq("id", projectId)
        .eq("user_id", user.id);

      if (error) throw error;

      return NextResponse.json({ ok: true, projectId });
    } else {
      // Insert new project
      const { data, error } = await supabase
        .from("projects")
        .insert([
          {
            user_id: user.id,
            name: projectName,
            data: storedData,
          },
        ])
        .select()
        .single();

      if (error) throw error;

      return NextResponse.json({
        ok: true,
        projectId: data.id,
        project: data
      });
    }
  } catch (e) {
    console.error("Failed to save project:", e);
    return NextResponse.json({ error: "Failed to save project" }, { status: 500 });
  }
}
