import { createClient as createSupabaseClient } from "@supabase/supabase-js";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || "https://lmrfzlbwzdmfgyfglukj.supabase.co";
const supabaseKey = 
  process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY || 
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || 
  "sb_publishable_TnR2HwzA0Y9wuQIdmTELkA_QPaBruvG";

export const supabase = createSupabaseClient(supabaseUrl, supabaseKey);

export function createClient() {
  return supabase;
}

export default supabase;
