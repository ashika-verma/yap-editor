import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { readFileSync, readdirSync } from "fs";
import path from "path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function runMigration(user: any) {
  const projectsDir = path.join(process.cwd(), "projects");
  const projectIds = readdirSync(projectsDir);
  let count = 0;
  const supabase = await createClient();

  for (const id of projectIds) {
    const metaPath = path.join(projectsDir, id, "meta.json");
    const planPath = path.join(projectsDir, id, "plan.json");

    try {
      const metaContent = readFileSync(metaPath, "utf8");
      const planContent = readFileSync(planPath, "utf8");

      const meta = JSON.parse(metaContent);
      const plan = JSON.parse(planContent);

      const { error } = await supabase
        .from("projects")
        .insert([
          {
            user_id: user.id,
            name: meta.name || `Project ${new Date(meta.savedAt).toLocaleDateString()}`,
            data: plan,
          },
        ]);

      if (!error) {
        count++;
      }
    } catch {
      // Skip invalid projects
    }
  }

  return count;
}

export async function GET(req: NextRequest) {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();

  if (!user) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  try {
    const migrated = await runMigration(user);
    return NextResponse.json({ migrated, message: `Migrated ${migrated} projects!` });
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "Migration failed" },
      { status: 500 }
    );
  }
}

export async function POST(req: NextRequest) {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();

  if (!user) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  try {
    const migrated = await runMigration(user);
    return NextResponse.json({ migrated });
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "Migration failed" },
      { status: 500 }
    );
  }
}
