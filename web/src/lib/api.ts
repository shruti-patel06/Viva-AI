import { supabase } from "./supabase";

// Calls to our FastAPI app, which handles every write. The login token goes along so
// the API knows who is asking.
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const { data } = await supabase.auth.getSession();
  const headers = new Headers(init.headers);
  if (data.session) {
    headers.set("Authorization", `Bearer ${data.session.access_token}`);
  }
  return fetch(`${process.env.NEXT_PUBLIC_API_URL}${path}`, { ...init, headers });
}

export async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}
