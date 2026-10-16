"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import { newKey } from "@/lib/keys";
import { currentRole, supabase } from "@/lib/supabase";

type Question = {
  position: number;
  text: string;
  type: string;
  answer_mode: string | null;
  hints: string[];
  hints_left: number;
};
type View = {
  id: string;
  state: "joined" | "in_progress" | "completed";
  exam_title: string;
  experiment: string;
  roll_number: string;
  question_count: number;
  answered: number;
  seconds_left: number | null;
  duration_minutes: number;
  marks_per_question: number;
  hint_penalty: number;
  question: Question | null;
};

// The page keeps nothing that matters: the server says where the student is, so a refresh
// or a dropped connection resumes the viva. Only unsent typing is kept in the browser.
export default function VivaPage() {
  const router = useRouter();
  const [view, setView] = useState<View | null>(null);
  const [deadline, setDeadline] = useState<number | null>(null);
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const show = useCallback((next: View) => {
    setDeadline(next.seconds_left === null ? null : Date.now() + next.seconds_left * 1000);
    setView(next);
  }, []);

  const fetchView = useCallback(async (sessionId: string) => {
    const response = await apiFetch(`/sessions/${sessionId}`);
    if (!response.ok) throw new Error(await errorMessage(response));
    return (await response.json()) as View;
  }, []);

  useEffect(() => {
    let active = true; // ignore replies that arrive after the page has gone
    (async () => {
      if ((await currentRole()) !== "student") {
        router.replace("/join");
        return;
      }
      // RLS returns only this login's sessions; the newest is the one to resume.
      const { data } = await supabase.from("sessions").select("id").order("created_at", { ascending: false }).limit(1);
      if (!active) return;
      if (!data?.length) {
        setMissing(true);
        return;
      }
      try {
        const loaded = await fetchView(data[0].id);
        if (active) show(loaded);
      } catch (e) {
        if (active) setError((e as Error).message);
      }
    })();
    return () => {
      active = false;
    };
  }, [router, fetchView, show]);

  // Every action replies with the session as it now stands, and the page shows that.
  const send = useCallback(
    async (path: string, body?: object): Promise<boolean> => {
      if (!view) return false;
      setBusy(true);
      setError(null);
      try {
        const response = await apiFetch(`/sessions/${view.id}${path}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: body ? JSON.stringify(body) : undefined,
        });
        if (response.ok) {
          show(await response.json());
          return true;
        }
        setError(await errorMessage(response));
        // Refused, perhaps because another tab moved on: show what the server has now.
        show(await fetchView(view.id));
        return false;
      } catch {
        setError("Could not reach the server. Check the connection and press the button again; nothing is lost.");
        return false;
      } finally {
        setBusy(false);
      }
    },
    [view, show, fetchView],
  );

  if (missing) {
    return (
      <Shell>
        <p className="text-sm">
          You have not joined a viva on this browser.{" "}
          <Link href="/join" className="underline">
            Join one
          </Link>
          .
        </p>
      </Shell>
    );
  }
  if (!view) {
    return <Shell>{error ? <p className="text-sm text-red-600">{error}</p> : <p className="text-sm text-zinc-500">Loading…</p>}</Shell>;
  }

  const penalty = `${Math.round(view.hint_penalty * 100)}%`;

  return (
    <Shell>
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm text-zinc-500">
        <span>
          {view.exam_title} · {view.experiment}
        </span>
        <span className="font-mono">{view.roll_number}</span>
      </div>

      {view.state === "joined" && (
        <div className="space-y-4">
          <h1 className="text-xl font-semibold">Ready when you are</h1>
          <ul className="list-disc space-y-1 pl-5 text-sm text-zinc-700">
            <li>
              {view.question_count} questions, {view.duration_minutes} minutes in all. The clock starts when you press
              Start.
            </li>
            <li>Answer each question before the next one appears. You cannot go back.</li>
            <li>
              You may take a hint on any question. Each hint costs {penalty} of that question&apos;s{" "}
              {view.marks_per_question} marks.
            </li>
            <li>If the page reloads or the connection drops, open it again: you continue where you were.</li>
          </ul>
          <button
            disabled={busy}
            onClick={() => send("/start")}
            className="rounded-md bg-zinc-900 px-4 py-2 text-white disabled:opacity-50"
          >
            Start viva
          </button>
        </div>
      )}

      {view.state === "in_progress" && view.question && (
        <QuestionCard
          key={view.question.position}
          sessionId={view.id}
          question={view.question}
          total={view.question_count}
          deadline={deadline}
          penalty={penalty}
          busy={busy}
          send={send}
        />
      )}

      {view.state === "completed" && (
        <div className="space-y-2">
          <h1 className="text-xl font-semibold">Your viva is finished</h1>
          <p className="text-sm text-zinc-700">
            {view.answered} of {view.question_count} answers saved. Your teacher reviews them before any marks are
            given. You can close this page.
          </p>
        </div>
      )}

      {error && <p className="text-sm text-red-600">{error}</p>}
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto w-full max-w-2xl px-4 py-10">
      <div className="space-y-6 rounded-xl border border-zinc-200 bg-white p-6 shadow-sm">{children}</div>
    </main>
  );
}

function readDraft(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return ""; // storage blocked: typing still works, it just is not kept across a refresh
  }
}

function writeDraft(key: string, text: string) {
  try {
    if (text) localStorage.setItem(key, text);
    else localStorage.removeItem(key);
  } catch {}
}

function QuestionCard({
  sessionId,
  question,
  total,
  deadline,
  penalty,
  busy,
  send,
}: {
  sessionId: string;
  question: Question;
  total: number;
  deadline: number | null;
  penalty: string;
  busy: boolean;
  send: (path: string, body?: object) => Promise<boolean>;
}) {
  const draftKey = `viva-draft:${sessionId}:${question.position}`;
  const [text, setText] = useState(() => readDraft(draftKey));
  // One key per question, made once and sent with every retry, so a double click or a
  // resend after a dropped reply saves the answer once.
  const [idempotencyKey] = useState(newKey);
  const [now, setNow] = useState(() => Date.now());
  const sentAtZero = useRef(false);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const secondsLeft = deadline === null ? null : Math.max(0, Math.ceil((deadline - now) / 1000));

  const submit = useCallback(
    async (answer: string) => {
      const saved = await send("/answers", { position: question.position, text: answer, idempotency_key: idempotencyKey });
      if (saved) writeDraft(draftKey, "");
    },
    [send, question.position, idempotencyKey, draftKey],
  );

  // When the clock reaches zero, send what has been typed so far: the server accepts it for
  // a few seconds after the deadline, then ends the viva.
  useEffect(() => {
    if (secondsLeft === 0 && !sentAtZero.current) {
      sentAtZero.current = true;
      void submit(text);
    }
  }, [secondsLeft, submit, text]);

  const last = question.position === total;
  const minutes = secondsLeft === null ? "" : `${Math.floor(secondsLeft / 60)}:${String(secondsLeft % 60).padStart(2, "0")}`;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between text-sm">
        <span className="text-zinc-500">
          Question {question.position} of {total} · {question.type.replaceAll("_", " ")}
        </span>
        <span className={`font-mono text-base ${secondsLeft !== null && secondsLeft < 60 ? "text-red-600" : ""}`}>
          {minutes}
        </span>
      </div>

      <p className="text-lg font-medium">{question.text}</p>

      {question.hints.length > 0 && (
        <ol className="list-decimal space-y-1 rounded-lg bg-amber-50 py-2 pl-8 pr-3 text-sm text-amber-900">
          {question.hints.map((hint, i) => (
            <li key={i}>{hint}</li>
          ))}
        </ol>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit(text);
        }}
        className="space-y-3"
      >
        <textarea
          autoFocus
          rows={5}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            writeDraft(draftKey, e.target.value);
          }}
          placeholder={question.answer_mode === "typed" ? "Type the exact answer" : "Type your answer"}
          className="w-full rounded-md border border-zinc-300 px-3 py-2"
        />
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <button
            type="submit"
            disabled={busy || !text.trim()}
            className="rounded-md bg-zinc-900 px-4 py-2 text-white disabled:opacity-50"
          >
            {busy ? "Saving…" : last ? "Submit and finish" : "Submit answer"}
          </button>
          {question.hints_left > 0 && (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (confirm(`A hint costs ${penalty} of this question's marks. Take one?`)) {
                  void send("/hints", { position: question.position, hint_number: question.hints.length + 1 });
                }
              }}
              className="rounded-md border border-amber-300 px-3 py-2 text-amber-800 disabled:opacity-50"
            >
              Take a hint (−{penalty})
            </button>
          )}
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              if (confirm("Skip this question? You cannot come back to it.")) void submit("");
            }}
            className="ml-auto text-zinc-500 underline disabled:opacity-50"
          >
            Skip
          </button>
        </div>
      </form>
    </div>
  );
}
