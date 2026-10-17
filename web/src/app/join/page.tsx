"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import { currentRole, supabase } from "@/lib/supabase";

type FoundExam = { title: string; section: string; experiments: { id: string; title: string }[] };

export default function JoinPage() {
  const router = useRouter();
  const [checking, setChecking] = useState(true);
  const [isTeacher, setIsTeacher] = useState(false);
  const [code, setCode] = useState("");
  const [exam, setExam] = useState<FoundExam | null>(null);
  const [experimentId, setExperimentId] = useState("");
  const [rollNumber, setRollNumber] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A student who already has a viva running (say, after closing the tab) goes straight back to it.
  useEffect(() => {
    (async () => {
      const role = await currentRole();
      if (role === "student") {
        const { data } = await supabase.from("sessions").select("id").neq("state", "completed").limit(1);
        if (data?.length) {
          router.replace("/viva");
          return;
        }
      }
      setIsTeacher(role === "teacher");
      setChecking(false);
    })();
  }, [router]);

  async function findExam(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    // Students have no account: each browser gets an anonymous login the first time it joins.
    if ((await currentRole()) !== "student") {
      const { error } = await supabase.auth.signInAnonymously();
      if (error) {
        setError(error.message);
        setBusy(false);
        return;
      }
    }
    const response = await apiFetch(`/join/${encodeURIComponent(code.trim())}`);
    setBusy(false);
    if (!response.ok) {
      setError(await errorMessage(response));
      return;
    }
    const found: FoundExam = await response.json();
    setExam(found);
    if (found.experiments.length === 1) setExperimentId(found.experiments[0].id);
  }

  async function join(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const response = await apiFetch("/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ join_code: code.trim(), roll_number: rollNumber, experiment_id: experimentId }),
    });
    if (!response.ok) {
      setError(await errorMessage(response));
      setBusy(false);
      return;
    }
    router.replace("/viva");
  }

  async function signOut() {
    await supabase.auth.signOut();
    setIsTeacher(false);
  }

  if (checking) return null;

  return (
    <main className="flex flex-1 items-center justify-center px-4">
      <div className="w-full max-w-sm space-y-4 rounded-xl border border-zinc-200 bg-white p-6 shadow-sm">
        <div>
          <h1 className="text-xl font-semibold">VivaAI</h1>
          <p className="text-sm text-zinc-500">Join your lab viva</p>
        </div>

        {isTeacher ? (
          <p className="text-sm">
            You are signed in as a teacher.{" "}
            <button onClick={signOut} className="underline">
              Sign out
            </button>{" "}
            to join as a student.
          </p>
        ) : exam === null ? (
          <form onSubmit={findExam} className="space-y-4">
            <label className="block text-sm">
              Exam code
              <input
                required
                autoFocus
                value={code}
                onChange={(e) => setCode(e.target.value.toUpperCase())}
                placeholder="ABC234"
                className="mt-1 w-full rounded-md border border-zinc-300 px-3 py-2 font-mono tracking-widest"
              />
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <button type="submit" disabled={busy} className="w-full rounded-md bg-zinc-900 px-3 py-2 text-white disabled:opacity-50">
              {busy ? "Checking…" : "Continue"}
            </button>
          </form>
        ) : (
          <form onSubmit={join} className="space-y-4">
            <div className="rounded-lg bg-zinc-50 px-3 py-2 text-sm">
              <p className="font-medium">{exam.title}</p>
              <p className="text-zinc-500">Section {exam.section}</p>
            </div>
            <label className="block text-sm">
              Roll number
              <input
                required
                autoFocus
                value={rollNumber}
                onChange={(e) => setRollNumber(e.target.value.toUpperCase())}
                className="mt-1 w-full rounded-md border border-zinc-300 px-3 py-2 font-mono"
              />
            </label>
            <label className="block text-sm">
              The experiment you performed
              <select
                required
                value={experimentId}
                onChange={(e) => setExperimentId(e.target.value)}
                className="mt-1 w-full rounded-md border border-zinc-300 px-3 py-2"
              >
                <option value="" disabled>
                  Choose…
                </option>
                {exam.experiments.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.title}
                  </option>
                ))}
              </select>
            </label>
            <p className="text-xs text-zinc-500">Check your roll number: you can join only once.</p>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <button type="submit" disabled={busy} className="w-full rounded-md bg-zinc-900 px-3 py-2 text-white disabled:opacity-50">
              {busy ? "Joining…" : "Join viva"}
            </button>
          </form>
        )}
      </div>
    </main>
  );
}
