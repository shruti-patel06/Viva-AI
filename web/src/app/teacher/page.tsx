"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import { currentRole, supabase } from "@/lib/supabase";

type Subject = { id: string; name: string };
type Document = {
  id: string;
  file_name: string;
  status: "uploaded" | "processing" | "ready" | "failed";
  error: string | null;
  created_at: string;
};

const POLL_MS = 3000;

const STATUS_STYLE: Record<Document["status"], string> = {
  uploaded: "bg-zinc-100 text-zinc-700",
  processing: "bg-amber-100 text-amber-800",
  ready: "bg-emerald-100 text-emerald-800",
  failed: "bg-red-100 text-red-800",
};

export default function TeacherPage() {
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [subjectId, setSubjectId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // Read straight from the database: RLS returns only this teacher's documents.
  const loadDocuments = useCallback(async () => {
    const { data } = await supabase
      .from("documents")
      .select("id, file_name, status, error, created_at")
      .order("created_at", { ascending: false });
    setDocuments(data ?? []);
  }, []);

  useEffect(() => {
    (async () => {
      if ((await currentRole()) !== "teacher") {
        router.replace("/login");
        return;
      }
      const { data: session } = await supabase.auth.getSession();
      setEmail(session.session?.user.email ?? null);
      const { data } = await supabase.from("subjects").select("id, name").order("name");
      setSubjects(data ?? []);
      if (data?.length) setSubjectId(data[0].id);
      await loadDocuments();
    })();
  }, [router, loadDocuments]);

  // The worker updates the status in the background; check again while anything is pending.
  const pending = documents.some((d) => d.status === "uploaded" || d.status === "processing");
  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(loadDocuments, POLL_MS);
    return () => clearInterval(timer);
  }, [pending, loadDocuments]);

  async function upload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || !subjectId) return;
    // React clears event.currentTarget once the handler awaits, so keep the form now.
    const formElement = event.currentTarget;
    setUploading(true);
    setUploadError(null);
    const body = new FormData();
    body.append("subject_id", subjectId);
    body.append("file", file);
    const response = await apiFetch("/documents", { method: "POST", body });
    setUploading(false);
    if (!response.ok) {
      setUploadError(await errorMessage(response));
      return;
    }
    formElement.reset();
    setFile(null);
    await loadDocuments();
  }

  async function signOut() {
    await supabase.auth.signOut();
    router.replace("/login");
  }

  return (
    <main className="mx-auto w-full max-w-3xl space-y-8 px-4 py-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Lab material</h1>
          <p className="text-sm text-zinc-500">{email}</p>
        </div>
        <button onClick={signOut} className="text-sm text-zinc-600 underline">
          Sign out
        </button>
      </header>

      <form onSubmit={upload} className="space-y-4 rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
        <h2 className="font-medium">Upload a lab manual</h2>
        <div className="flex flex-col gap-3 sm:flex-row">
          <select
            value={subjectId}
            onChange={(e) => setSubjectId(e.target.value)}
            className="rounded-md border border-zinc-300 px-3 py-2"
          >
            {subjects.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <input
            type="file"
            accept="application/pdf"
            required
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="flex-1 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-zinc-100 file:px-3 file:py-2"
          />
          <button
            type="submit"
            disabled={uploading || !file}
            className="rounded-md bg-zinc-900 px-4 py-2 text-white disabled:opacity-50"
          >
            {uploading ? "Uploading…" : "Upload"}
          </button>
        </div>
        {uploadError && <p className="text-sm text-red-600">{uploadError}</p>}
        <p className="text-xs text-zinc-500">
          The manual is split by the experiment titles of the subject, so each title must appear as a heading.
        </p>
      </form>

      <section className="space-y-3">
        <h2 className="font-medium">Your documents</h2>
        {documents.length === 0 && <p className="text-sm text-zinc-500">Nothing uploaded yet.</p>}
        <ul className="divide-y divide-zinc-200 rounded-xl border border-zinc-200 bg-white">
          {documents.map((d) => (
            <li key={d.id} className="space-y-1 px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <span className="truncate">{d.file_name}</span>
                <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_STYLE[d.status]}`}>
                  {d.status}
                </span>
              </div>
              <p className="text-xs text-zinc-500">{new Date(d.created_at).toLocaleString()}</p>
              {d.error && <p className="text-sm text-red-600">{d.error}</p>}
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
