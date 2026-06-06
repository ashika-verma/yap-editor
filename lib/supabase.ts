import { createClient } from "@supabase/supabase-js";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

if (!supabaseUrl || !supabaseAnonKey) {
  throw new Error("Missing Supabase URL or anon key");
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey);

// For server-side operations (API routes only)
let supabaseAdmin: ReturnType<typeof createClient> | null = null;
if (typeof window === "undefined" && process.env.SUPABASE_SECRET_KEY) {
  supabaseAdmin = createClient(supabaseUrl, process.env.SUPABASE_SECRET_KEY);
}

export { supabaseAdmin };
