"use client";

import { useCallback, useEffect, useState } from "react";
import { TeacherHeader } from "@/components/TeacherHeader";
import { apiFetch, errorMessage } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { useTeacher } from "@/lib/useTeacher";

type Subject = { id: string; name: string };
type Experiment = { id: string; subject_id: string; title: string; position: number };
type ExamState = "draft" | "open" | "closed" | "approved";
type Exam = {
  id: string;
  title: string;
  section: string;
  join_code: string;
  state: ExamState;
  config: { experiment_ids: string[]; duration_minutes: number; marks_per_question: number; hint_penalty: number };
  created_at: string;
  subjects: { name: string };
};
type Session = {
  id: string;
  roll_number: string;
  state: "joined" | "in_progress" | "completed";
  current_position: number;
  ends_at: string | null;
  experiments: { title: string } | null;
  session_questions: { answers: OneOrMany<Answer> }[];
};
type Score = { ai_score: number | null; needs_review: boolean };
type Answer = { id: string; scores: OneOrMany<Score> };
// An embedded one-to-one row (each answer has at most one score) may come back as an object
// or a one-item array, depending on how the relationship is detected.
type OneOrMany<T> = T | T[] | null;

function one<T>(value: OneOrMany<T>): T | null {
  return Array.isArray(value) ? (value[0] ?? null) : value;
}

const POLL_MS = 5000;
const STATE_STYLE: Record<ExamState, string> = {
  draft: "bg-zinc-100 text-zinc-700",
  open: "bg-emerald-100 text-emerald-800",
  closed: "bg-zinc-200 text-zinc-600",
  approved: "bg-sky-100 text-sky-800",
};

// RLS returns only this teacher's exams.
async function fetchExams(): Promise<Exam[]> {
  const { data } = await supabase
    .from("exams")
    .select("id, title, section, join_code, state, config, created_at, subjects(name)")
    .order("created_at", { ascending: false });
  return (data as unknown as Exam[]) ?? [];
}

export default function ExamsPage() {
  const email = useTeacher();
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [exams, setExams] = useState<Exam[]>([]);

  const load = useCallback(async () => setExams(await fetchExams()), []);

  useEffect(() => {
    if (email === null) return;
    let active = true; // ignore replies that arrive after the page has gone
    (async () => {
      const [subjectRows, experimentRows, examRows] = await Promise.all([
        supabase.from("subjects").select("id, name").order("name"),
        supabase.from("experiments").select("id, subject_id, title, position").order("position"),
        fetchExams(),
      ]);
      if (!active) return;
      setSubjects(subjectRows.data ?? []);
      setExperiments(experimentRows.data ?? []);
      setExams(examRows);
    })();
    return () => {
      active = false;
    };
  }, [email]);

  return (
    <main className="mx-auto w-full max-w-4xl space-y-8 px-4 py-8">
      <TeacherHeader email={email} />
      {subjects.length > 0 && <NewExamForm subjects={subjects} experiments={experiments} onCreated={load} />}
      <section className="space-y-4">
        <h2 className="font-medium">Your exams</h2>
        {exams.length === 0 && <p className="text-sm text-zinc-500">No exams yet.</p>}
        {exams.map((exam) => (
          <ExamCard key={exam.id} exam={exam} experiments={experiments} onChange={load} />
        ))}
      </section>
    </main>
  );
}

function NewExamForm({
  subjects,
  experiments,
  onCreated,
}: {
  subjects: Subject[];
  experiments: Experiment[];
  onCreated: () => Promise<void>;
}) {
  const [subjectId, setSubjectId] = useState(subjects[0].id);
  const [title, setTitle] = useState("");
  const [section, setSection] = useState("");
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [duration, setDuration] = useState(10);
  const [marks, setMarks] = useState(4);
  const [penalty, setPenalty] = useState(25);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const subjectExperiments = experiments.filter((e) => e.subject_id === subjectId);

  function toggle(id: string) {
    const next = new Set(chosen);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setChosen(next);
  }

  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget; // React clears currentTarget once the handler awaits
    setBusy(true);
    setError(null);
    const response = await apiFetch("/exams", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        subject_id: subjectId,
        title,
        section,
        experiment_ids: subjectExperiments.filter((e) => chosen.has(e.id)).map((e) => e.id),
        duration_minutes: duration,
        marks_per_question: marks,
        hint_penalty: penalty / 100,
      }),
    });
    setBusy(false);
    if (!response.ok) {
      setError(await errorMessage(response));
      return;
    }
    formElement.reset();
    setTitle("");
    setSection("");
    setChosen(new Set());
    await onCreated();
  }

  return (
    <form onSubmit={create} className="space-y-4 rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
      <h2 className="font-medium">New exam</h2>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block text-sm sm:col-span-2">
          Title
          <input
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="DS lab internal, batch 1"
            className="mt-1 w-full rounded-md border border-zinc-300 px-3 py-2"
          />
        </label>
        <label className="block text-sm">
          Section
          <input
            required
            value={section}
            onChange={(e) => setSection(e.target.value)}
            placeholder="CSE-A"
            className="mt-1 w-full rounded-md border border-zinc-300 px-3 py-2"
          />
        </label>
      </div>
      <label className="block text-sm">
        Subject
        <select
          value={subjectId}
          onChange={(e) => {
            setSubjectId(e.target.value);
            setChosen(new Set());
          }}
          className="ml-2 rounded-md border border-zinc-300 px-2 py-1"
        >
          {subjects.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
      <fieldset className="space-y-2 text-sm">
        <legend>Experiments in this exam</legend>
        <p className="text-xs text-zinc-500">
          Each student picks the experiment they performed and is asked about that one.
        </p>
        <div className="grid gap-1 sm:grid-cols-2">
          {subjectExperiments.map((e) => (
            <label key={e.id} className="flex items-center gap-2">
              <input type="checkbox" checked={chosen.has(e.id)} onChange={() => toggle(e.id)} />
              {e.title}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="flex flex-wrap gap-4 text-sm">
        <label>
          Minutes
          <input
            type="number"
            min={1}
            max={60}
            value={duration}
            onChange={(e) => setDuration(Number(e.target.value))}
            className="ml-2 w-20 rounded-md border border-zinc-300 px-2 py-1"
          />
        </label>
        <label>
          Marks per question
          <input
            type="number"
            min={1}
            max={20}
            value={marks}
            onChange={(e) => setMarks(Number(e.target.value))}
            className="ml-2 w-20 rounded-md border border-zinc-300 px-2 py-1"
          />
        </label>
        <label>
          Hint penalty %
          <input
            type="number"
            min={0}
            max={50}
            value={penalty}
            onChange={(e) => setPenalty(Number(e.target.value))}
            className="ml-2 w-20 rounded-md border border-zinc-300 px-2 py-1"
          />
        </label>
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button
        type="submit"
        disabled={busy || chosen.size === 0}
        className="rounded-md bg-zinc-900 px-4 py-2 text-white disabled:opacity-50"
      >
        {busy ? "Creating…" : "Create exam"}
      </button>
    </form>
  );
}

function ExamCard({
  exam,
  experiments,
  onChange,
}: {
  exam: Exam;
  experiments: Experiment[];
  onChange: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titles = experiments.filter((e) => exam.config.experiment_ids.includes(e.id)).map((e) => e.title);

  async function act(action: "open" | "close") {
    if (action === "close" && !confirm("Close this exam? Every viva still running ends with the answers it has.")) {
      return;
    }
    setBusy(true);
    setError(null);
    const response = await apiFetch(`/exams/${exam.id}/${action}`, { method: "POST" });
    setBusy(false);
    if (!response.ok) {
      setError(await errorMessage(response));
      return;
    }
    await onChange();
  }

  return (
    <article className="space-y-3 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-medium">{exam.title}</p>
          <p className="text-sm text-zinc-500">
            {exam.subjects.name} · {exam.section} · {exam.config.duration_minutes} min ·{" "}
            {exam.config.marks_per_question} marks a question · {Math.round(exam.config.hint_penalty * 100)}% per hint
          </p>
          <p className="text-xs text-zinc-500">{titles.join(", ")}</p>
        </div>
        <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${STATE_STYLE[exam.state]}`}>
          {exam.state}
        </span>
      </div>

      {exam.state === "open" && (
        <p className="text-sm">
          Students go to <span className="font-mono">/join</span> and enter{" "}
          <span className="rounded bg-zinc-900 px-2 py-1 font-mono text-lg tracking-widest text-white">
            {exam.join_code}
          </span>
        </p>
      )}

      <div className="flex gap-2 text-sm">
        {exam.state === "draft" && (
          <button
            disabled={busy}
            onClick={() => act("open")}
            className="rounded-md bg-emerald-700 px-3 py-1 text-white disabled:opacity-50"
          >
            Open for students
          </button>
        )}
        {exam.state === "open" && (
          <button
            disabled={busy}
            onClick={() => act("close")}
            className="rounded-md border border-zinc-300 px-3 py-1 text-red-700 disabled:opacity-50"
          >
            Close exam
          </button>
        )}
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      {exam.state !== "draft" && (
        <Sessions examId={exam.id} live={exam.state === "open"} marksPerQuestion={exam.config.marks_per_question} />
      )}
    </article>
  );
}

async function fetchSessions(examId: string): Promise<Session[]> {
  const { data } = await supabase
    .from("sessions")
    .select(
      "id, roll_number, state, current_position, ends_at, experiments(title), session_questions(answers(id, scores(ai_score, needs_review)))",
    )
    .eq("exam_id", examId)
    .order("roll_number");
  return (data as unknown as Session[]) ?? [];
}

function progress(session: Session): string {
  const total = session.session_questions.length;
  if (session.state === "joined") return "Joined, not started";
  if (session.state === "completed") return "Finished";
  if (session.ends_at && new Date(session.ends_at) < new Date()) return "Time up";
  return `Question ${session.current_position} of ${total}`;
}

// The AI's suggested marks so far. Scores arrive a little after each answer, at the rate the
// AI service allows, so some answers can still be waiting.
function suggested(session: Session) {
  const answers = session.session_questions.map((sq) => one(sq.answers)).filter((a): a is Answer => a !== null);
  const scores = answers.map((a) => one(a.scores));
  return {
    marks: scores.reduce((sum, s) => sum + Number(s?.ai_score ?? 0), 0),
    waiting: scores.filter((s) => s === null).length,
    toReview: scores.filter((s) => s?.needs_review).length,
  };
}

function Sessions({ examId, live, marksPerQuestion }: { examId: string; live: boolean; marksPerQuestion: number }) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const scoring = sessions.some((s) => suggested(s).waiting > 0);

  useEffect(() => {
    let active = true;
    const refresh = () =>
      fetchSessions(examId).then((rows) => {
        if (active) setSessions(rows);
      });
    refresh();
    // While the exam is open students join and move on, and scores keep arriving after it
    // closes; check again every few seconds until both have settled.
    const timer = live || scoring ? setInterval(refresh, POLL_MS) : undefined;
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [examId, live, scoring]);

  if (sessions.length === 0) return <p className="text-sm text-zinc-500">No students have joined yet.</p>;

  const finished = sessions.filter((s) => s.state === "completed").length;
  const waiting = sessions.reduce((sum, s) => sum + suggested(s).waiting, 0);
  return (
    <div className="space-y-2">
      <p className="text-sm text-zinc-600">
        {sessions.length} joined · {finished} finished
        {waiting > 0 && ` · ${waiting} answer${waiting === 1 ? "" : "s"} waiting to be scored`}
      </p>
      <table className="w-full text-left text-sm">
        <thead className="text-zinc-500">
          <tr>
            <th className="py-1 font-normal">Roll number</th>
            <th className="py-1 font-normal">Experiment</th>
            <th className="py-1 font-normal">Progress</th>
            <th className="py-1 font-normal">Suggested marks</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100">
          {sessions.map((s) => {
            const score = suggested(s);
            return (
              <tr key={s.id}>
                <td className="py-1 font-mono">{s.roll_number}</td>
                <td className="py-1">{s.experiments?.title}</td>
                <td className="py-1">{progress(s)}</td>
                <td className="py-1">
                  {score.marks.toFixed(1)} / {s.session_questions.length * marksPerQuestion}
                  {score.waiting > 0 && <span className="text-zinc-500"> · scoring {score.waiting}…</span>}
                  {score.toReview > 0 && (
                    <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
                      {score.toReview} to check
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="text-xs text-zinc-500">
        Marks are the AI&apos;s suggestions until you review them. Answers marked &quot;to check&quot; need your judgement.
      </p>
    </div>
  );
}
