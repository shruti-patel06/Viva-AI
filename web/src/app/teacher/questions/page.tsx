"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { TeacherHeader } from "@/components/TeacherHeader";
import { apiFetch, errorMessage } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { useTeacher } from "@/lib/useTeacher";

type Status = "draft" | "approved" | "rejected";
type Question = {
  id: string;
  type: string;
  difficulty: "easy" | "medium" | "hard";
  question_text: string;
  reference_answer: string;
  key_points: string[];
  hints: string[];
  status: Status;
  audio_path: string | null;
  experiments: { title: string; position: number };
};

const POLL_MS = 3000;
const FILTERS: { value: Status | "all"; label: string }[] = [
  { value: "draft", label: "To review" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "all", label: "All" },
];
const STATUS_STYLE: Record<Status, string> = {
  draft: "bg-amber-100 text-amber-800",
  approved: "bg-emerald-100 text-emerald-800",
  rejected: "bg-zinc-200 text-zinc-600",
};

// RLS returns only this teacher's questions; students can never read this table.
async function fetchQuestions(): Promise<Question[]> {
  const { data } = await supabase
    .from("questions")
    .select(
      "id, type, difficulty, question_text, reference_answer, key_points, hints, status, audio_path, experiments(title, position)",
    )
    // Questions written live from a student's own program belong to that viva, not the pool.
    .neq("status", "live")
    .order("created_at");
  return (data as unknown as Question[]) ?? [];
}

export default function QuestionsPage() {
  const email = useTeacher();
  const [questions, setQuestions] = useState<Question[]>([]);
  const [filter, setFilter] = useState<Status | "all">("draft");
  const [experiment, setExperiment] = useState("all");
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    setQuestions(await fetchQuestions());
    setLoaded(true);
  }, []);

  useEffect(() => {
    if (email === null) return;
    let active = true; // ignore a reply that arrives after the page has gone
    fetchQuestions().then((rows) => {
      if (!active) return;
      setQuestions(rows);
      setLoaded(true);
    });
    return () => {
      active = false;
    };
  }, [email]);

  // Audio is made in the background after approval; refresh until it has arrived.
  const awaitingAudio = questions.some((q) => q.status === "approved" && !q.audio_path);
  useEffect(() => {
    if (!awaitingAudio) return;
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [awaitingAudio, load]);

  const experiments = useMemo(() => {
    const seen = new Map<string, number>();
    questions.forEach((q) => seen.set(q.experiments.title, q.experiments.position));
    return [...seen.entries()].sort((a, b) => a[1] - b[1]).map(([title]) => title);
  }, [questions]);

  const counts = useMemo(() => {
    const inExperiment = questions.filter((q) => experiment === "all" || q.experiments.title === experiment);
    return Object.fromEntries(
      FILTERS.map((f) => [f.value, inExperiment.filter((q) => f.value === "all" || q.status === f.value).length]),
    );
  }, [questions, experiment]);

  const groups = useMemo(() => {
    const shown = questions.filter(
      (q) => (filter === "all" || q.status === filter) && (experiment === "all" || q.experiments.title === experiment),
    );
    const byExperiment = new Map<string, Map<string, Question[]>>();
    shown
      .sort((a, b) => a.experiments.position - b.experiments.position)
      .forEach((q) => {
        const slots = byExperiment.get(q.experiments.title) ?? new Map<string, Question[]>();
        const slot = `${q.type} · ${q.difficulty}`;
        slots.set(slot, [...(slots.get(slot) ?? []), q]);
        byExperiment.set(q.experiments.title, slots);
      });
    return byExperiment;
  }, [questions, filter, experiment]);

  return (
    <main className="mx-auto w-full max-w-4xl space-y-6 px-4 py-8">
      <TeacherHeader email={email} />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-1 rounded-lg bg-zinc-100 p-1">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setFilter(f.value)}
              className={`rounded-md px-3 py-1 text-sm ${filter === f.value ? "bg-white shadow-sm" : "text-zinc-600"}`}
            >
              {f.label} <span className="text-zinc-400">{counts[f.value] ?? 0}</span>
            </button>
          ))}
        </div>
        <select
          value={experiment}
          onChange={(e) => setExperiment(e.target.value)}
          className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm"
        >
          <option value="all">All experiments</option>
          {experiments.map((title) => (
            <option key={title} value={title}>
              {title}
            </option>
          ))}
        </select>
      </div>

      <p className="text-sm text-zinc-500">
        Nothing reaches a student until you approve it. Read each question, its answer and key points: the key points
        are what answers will be marked against. Editing an approved question sends it back for review.
      </p>

      {loaded && groups.size === 0 && <p className="text-sm text-zinc-500">No questions here.</p>}

      {[...groups.entries()].map(([title, slots]) => (
        <section key={title} className="space-y-4">
          <h2 className="text-lg font-semibold">{title}</h2>
          {[...slots.entries()].map(([slot, items]) => (
            <div key={slot} className="space-y-3">
              <h3 className="text-sm font-medium uppercase tracking-wide text-zinc-500">{slot}</h3>
              {items.map((q) => (
                <QuestionCard key={q.id} question={q} onChange={load} />
              ))}
            </div>
          ))}
        </section>
      ))}
    </main>
  );
}

function QuestionCard({ question, onChange }: { question: Question; onChange: () => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function act(path: string, init: RequestInit = { method: "POST" }) {
    setBusy(true);
    setError(null);
    const response = await apiFetch(path, init);
    setBusy(false);
    if (!response.ok) {
      setError(await errorMessage(response));
      return false;
    }
    await onChange();
    return true;
  }

  async function play() {
    const response = await apiFetch(`/questions/${question.id}/audio`);
    if (!response.ok) {
      setError(await errorMessage(response));
      return;
    }
    const { url } = await response.json();
    new Audio(url).play();
  }

  if (editing) {
    return (
      <EditForm
        question={question}
        busy={busy}
        error={error}
        onCancel={() => setEditing(false)}
        onSave={async (changes) => {
          const saved = await act(`/questions/${question.id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(changes),
          });
          if (saved) setEditing(false);
        }}
      />
    );
  }

  return (
    <article className="space-y-3 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <p className="font-medium">{question.question_text}</p>
        <span className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_STYLE[question.status]}`}>
          {question.status}
        </span>
      </div>
      <dl className="grid gap-2 text-sm sm:grid-cols-[8rem_1fr]">
        <dt className="text-zinc-500">Answer</dt>
        <dd>{question.reference_answer}</dd>
        <dt className="text-zinc-500">Key points</dt>
        <dd>
          <ul className="list-disc pl-5">
            {question.key_points.map((point, i) => (
              <li key={i}>{point}</li>
            ))}
          </ul>
        </dd>
        <dt className="text-zinc-500">Hints</dt>
        <dd>
          <ol className="list-decimal pl-5 text-zinc-600">
            {question.hints.map((hint, i) => (
              <li key={i}>{hint}</li>
            ))}
          </ol>
        </dd>
      </dl>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {question.status !== "approved" && (
          <button
            disabled={busy}
            onClick={() => act(`/questions/${question.id}/approve`)}
            className="rounded-md bg-emerald-700 px-3 py-1 text-white disabled:opacity-50"
          >
            Approve
          </button>
        )}
        <button
          disabled={busy}
          onClick={() => setEditing(true)}
          className="rounded-md border border-zinc-300 px-3 py-1 disabled:opacity-50"
        >
          Edit
        </button>
        {question.status !== "rejected" && (
          <button
            disabled={busy}
            onClick={() => act(`/questions/${question.id}/reject`)}
            className="rounded-md border border-zinc-300 px-3 py-1 text-red-700 disabled:opacity-50"
          >
            Reject
          </button>
        )}
        {question.status === "approved" &&
          (question.audio_path ? (
            <button onClick={play} className="rounded-md border border-zinc-300 px-3 py-1">
              ▶ Play audio
            </button>
          ) : (
            <span className="text-xs text-zinc-500">Making audio…</span>
          ))}
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
    </article>
  );
}

type Changes = Pick<Question, "question_text" | "reference_answer" | "key_points" | "hints" | "difficulty">;

function EditForm({
  question,
  busy,
  error,
  onCancel,
  onSave,
}: {
  question: Question;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onSave: (changes: Changes) => void;
}) {
  const [text, setText] = useState(question.question_text);
  const [answer, setAnswer] = useState(question.reference_answer);
  const [keyPoints, setKeyPoints] = useState(question.key_points.join("\n"));
  const [hints, setHints] = useState(question.hints.join("\n"));
  const [difficulty, setDifficulty] = useState(question.difficulty);
  const lines = (value: string) => value.split("\n").map((l) => l.trim()).filter(Boolean);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSave({
          question_text: text,
          reference_answer: answer,
          key_points: lines(keyPoints),
          hints: lines(hints),
          difficulty,
        });
      }}
      className="space-y-3 rounded-xl border-2 border-zinc-900 bg-white p-4 text-sm shadow-sm"
    >
      <label className="block">
        Question
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} className="mt-1 w-full rounded-md border border-zinc-300 px-3 py-2" />
      </label>
      <label className="block">
        Answer
        <textarea value={answer} onChange={(e) => setAnswer(e.target.value)} rows={2} className="mt-1 w-full rounded-md border border-zinc-300 px-3 py-2" />
      </label>
      <label className="block">
        Key points <span className="text-zinc-500">(one per line)</span>
        <textarea value={keyPoints} onChange={(e) => setKeyPoints(e.target.value)} rows={4} className="mt-1 w-full rounded-md border border-zinc-300 px-3 py-2" />
      </label>
      <label className="block">
        Hints <span className="text-zinc-500">(one per line, gentle first)</span>
        <textarea value={hints} onChange={(e) => setHints(e.target.value)} rows={2} className="mt-1 w-full rounded-md border border-zinc-300 px-3 py-2" />
      </label>
      <label className="block">
        Difficulty
        <select
          value={difficulty}
          onChange={(e) => setDifficulty(e.target.value as Question["difficulty"])}
          className="ml-2 rounded-md border border-zinc-300 px-2 py-1"
        >
          <option value="easy">easy</option>
          <option value="medium">medium</option>
          <option value="hard">hard</option>
        </select>
      </label>
      {error && <p className="text-red-600">{error}</p>}
      <div className="flex gap-2">
        <button type="submit" disabled={busy} className="rounded-md bg-zinc-900 px-3 py-1 text-white disabled:opacity-50">
          {busy ? "Saving…" : "Save"}
        </button>
        <button type="button" onClick={onCancel} className="rounded-md border border-zinc-300 px-3 py-1">
          Cancel
        </button>
      </div>
      <p className="text-xs text-zinc-500">Saving sends the question back for review; its audio is made again after approval.</p>
    </form>
  );
}
