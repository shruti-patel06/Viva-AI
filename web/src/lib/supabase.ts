import { createClient } from "@supabase/supabase-js";

// The browser's Supabase client. It signs in, keeps the session, and reads tables
// directly; row-level security decides which rows come back.
export const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
);

// The role our token hook put in the token, checked against Supabase's public keys.
export async function currentRole(): Promise<string | null> {
  const { data } = await supabase.auth.getClaims();
  return (data?.claims?.user_role as string | undefined) ?? null;
}
