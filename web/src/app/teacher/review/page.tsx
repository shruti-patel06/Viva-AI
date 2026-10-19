"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { TeacherHeader } from "@/components/TeacherHeader";
import { apiFetch, errorMessage } from "@/lib/api";
import { useTeacher } from "@/lib/useTeacher";

type Status = "unanswered" | "scoring" | "to_check" | "reviewed" | "scored";
type Hit = { key_point: string; verdict: "covered" | "partly" | "missed"; evidence: string };
type Answer = {
  answer_id: string | null;
  position: number;
  type: string;
  question: string;
  reference_answer: string;
  text: string | null;
  heard: string | null;
  hints_used: number;
  ai_score: number | null;
  ai_reason: string | null;
  key_points_hit: Hit[] | null;
  review_reason: string | null;
  teacher_score: number | null;
  teacher_note: string | null;
  reviewed_at: string | null;
  reviewed_by: string | null;
  final: number | null;
  status: Status;
};
type Student = { session_id: string; roll_number: string; experiment: string; total: number; answers: Answer[] };
type Sheet = {
  id: string;
  title: string;
  section: string;
  subject: string;
  state: "draft" | "open" | "closed" | "approved";
  marks_per_question: number;
  max_marks: number;
  approved_at: string | null;
  approved_by: string | null;
  waiting: number;
  students: Student[];
};

const POLL_MS = 5000;
const CELL_STYLE: Record<Status, string> = {
  unanswered: "text-zinc-400",
  scoring: "text-zinc-400",
  to_check: "bg-amber-100 text-amber-900",
  reviewed: "text-sky-800",
  scored: "",
};
const VERDICT_STYLE: Record<Hit["verdict"], string> = {
  covered: "bg-emerald-100 text-emerald-800",
  partly: "bg-amber-100 text-amber-800",
  missed: "bg-zinc-100 text-zinc-600",
};

export default function ReviewPage() {
  const email = useTeacher();
  return (
    <main className="mx-auto w-full max-w-5xl space-y-6 px-4 py-8">
      <TeacherHeader email={email} />
      {/* The exam comes from the address (?exam=...), which is only known in the browser. */}
      <Suspense fallback={<p className="text-sm text-zinc-500">Loading…</p>}>{email !== null && <Review />}</Suspense>
    </main>
  );
}

async function fetchSheet(examId: string): Promise<Sheet | string> {
  const response = await apiFetch(`/exams/${examId}/sheet`);
  return response.ok ? response.json() : errorMessage(response);
}

function Review() {
  const examId = useSearchParams().get("exam") ?? "";
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    const result = await fetchSheet(examId);
    if (typeof result === "string") setError(result);
    else setSheet(result);
  }, [examId]);

  useEffect(() => {
    let active = true; // ignore a reply that arrives after the page has gone
    fetchSheet(examId).then((result) => {
      if (!active) return;
      if (typeof result === "string") setError(result);
      else setSheet(result);
    });
    return () => {
      active = false;
    };
  }, [examId]);

  // Scores keep arriving from the worker for a while after the exam closes.
  const scoring = !!sheet?.students.some((s) => s.answers.some((a) => a.status === "scoring"));
  useEffect(() => {
    if (!scoring) return;
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [scoring, load]);

  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (!sheet) return <p className="text-sm text-zinc-500">Loading…</p>;

  const editable = sheet.state === "closed";
  const toCheck = sheet.students.flatMap((s) =>
    s.answers.filter((a) => a.status === "to_check" || a.status === "scoring").map((a) => ({ student: s, answer: a })),
  );
  const count = Math.max(0, ...sheet.students.map((s) => s.answers.length));

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/teacher/exams" className="text-sm text-zinc-500 underline">
            ← Exams
          </Link>
          <h1 className="text-xl font-semibold">{sheet.title}</h1>
          <p className="text-sm text-zinc-500">
            {sheet.subject} · section {sheet.section} · {sheet.students.length} students · {sheet.marks_per_question}{" "}
            marks a question
          </p>
        </div>
        <ApprovalBar sheet={sheet} onChange={load} />
      </div>

      {sheet.state === "open" && (
        <p className="rounded-lg bg-zinc-100 px-3 py-2 text-sm">
          The exam is still open. Close it on the Exams page to review marks.
        </p>
      )}

      {toCheck.length > 0 && (
        <section className="space-y-3">
          <h2 className="font-medium">To check ({toCheck.length})</h2>
          <p className="text-sm text-zinc-500">
            Flagged answers and answers the AI could not mark come first. Keep the AI&apos;s mark or give your own.
          </p>
          {toCheck.map(({ student, answer }) => (
            <AnswerCard
              key={`${student.session_id}-${answer.position}`}
              roll={student.roll_number}
              answer={answer}
              outOf={sheet.marks_per_question}
              editable={editable}
              onChange={load}
            />
          ))}
        </section>
      )}

      <section className="space-y-3">
        <h2 className="font-medium">Class sheet</h2>
        <div className="overflow-x-auto rounded-xl border border-zinc-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="text-zinc-500">
              <tr>
                <th className="px-3 py-2 font-normal">Roll number</th>
                <th className="px-3 py-2 font-normal">Experiment</th>
                {Array.from({ length: count }, (_, i) => (
                  <th key={i} className="px-3 py-2 font-normal">
                    Q{i + 1}
                  </th>
                ))}
                <th className="px-3 py-2 font-normal">Total</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100">
              {sheet.students.map((s) => (
                <tr key={s.session_id}>
                  <td className="px-3 py-2">
                    <button
                      onClick={() => setOpen(open === s.session_id ? null : s.session_id)}
                      className="font-mono underline"
                    >
                      {s.roll_number}
                    </button>
                  </td>
                  <td className="px-3 py-2">{s.experiment}</td>
                  {s.answers.map((a) => (
                    <td key={a.position} className={`px-3 py-2 ${CELL_STYLE[a.status]}`} title={a.status.replace("_", " ")}>
                      {a.final === null ? "…" : a.final}
                    </td>
                  ))}
                  <td className="px-3 py-2 font-medium">
                    {s.total} / {sheet.max_marks}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-zinc-500">
          Highlighted marks need your check; blue marks you have reviewed; … is still being scored. Click a roll number
          for that student&apos;s answers.
        </p>
      </section>

      {sheet.students
        .filter((s) => s.session_id === open)
        .map((s) => (
          <section key={s.session_id} className="space-y-3">
            <h2 className="font-medium">
              {s.roll_number} · {s.experiment} · {s.total} / {sheet.max_marks}
            </h2>
            {s.answers.map((a) => (
              <AnswerCard
                key={a.position}
                roll={s.roll_number}
                answer={a}
                outOf={sheet.marks_per_question}
                editable={editable}
                onChange={load}
              />
            ))}
          </section>
        ))}
    </div>
  );
}

function ApprovalBar({ sheet, onChange }: { sheet: Sheet; onChange: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function approve() {
    if (!confirm("Approve these marks? They are locked once approved.")) return;
    setBusy(true);
    setError(null);
    const response = await apiFetch(`/exams/${sheet.id}/approve`, { method: "POST" });
    setBusy(false);
    if (!response.ok) {
      setError(await errorMessage(response));
      return;
    }
    await onChange();
  }

  async function download(kind: "xlsx" | "pdf") {
    setError(null);
    const response = await apiFetch(`/exams/${sheet.id}/report.${kind}`);
    if (!response.ok) {
      setError(await errorMessage(response));
      return;
    }
    const name = /filename="([^"]+)"/.exec(response.headers.get("content-disposition") ?? "")?.[1] ?? `report.${kind}`;
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = name;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  return (
    <div className="space-y-1 text-right text-sm">
      {sheet.state === "closed" && (
        <button
          onClick={approve}
          disabled={busy || sheet.waiting > 0}
          className="rounded-md bg-emerald-700 px-4 py-2 text-white disabled:opacity-50"
        >
          Approve marks
        </button>
      )}
      {sheet.state === "closed" && sheet.waiting > 0 && (
        <p className="text-xs text-zinc-500">{sheet.waiting} answer(s) still need a mark</p>
      )}
      {sheet.state === "approved" && (
        <div className="space-y-2">
          <p className="text-zinc-600">
            Approved by {sheet.approved_by} on {sheet.approved_at && new Date(sheet.approved_at).toLocaleString()}
          </p>
          <div className="flex justify-end gap-2">
            <button onClick={() => download("xlsx")} className="rounded-md border border-zinc-300 px-3 py-1.5">
              Download Excel
            </button>
            <button onClick={() => download("pdf")} className="rounded-md border border-zinc-300 px-3 py-1.5">
              Download PDF
            </button>
          </div>
        </div>
      )}
      {error && <p className="text-red-600">{error}</p>}
    </div>
  );
}

function AnswerCard({
  roll,
  answer,
  outOf,
  editable,
  onChange,
}: {
  roll: string;
  answer: Answer;
  outOf: number;
  editable: boolean;
  onChange: () => Promise<void>;
}) {
  const [mark, setMark] = useState(answer.teacher_score === null ? "" : String(answer.teacher_score));
  const [note, setNote] = useState(answer.teacher_note ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(teacherScore: number | null) {
    setBusy(true);
    setError(null);
    const response = await apiFetch(`/answers/${answer.answer_id}/review`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ teacher_score: teacherScore, note }),
    });
    setBusy(false);
    if (!response.ok) {
      setError(await errorMessage(response));
      return;
    }
    await onChange();
  }

  const given = answer.answer_id === null ? "(not reached)" : answer.text || "(skipped)";
  return (
    <article className="space-y-3 rounded-xl border border-zinc-200 bg-white p-4 text-sm shadow-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-medium">
          <span className="font-mono">{roll}</span> · Q{answer.position} · {answer.type.replaceAll("_", " ")}
        </p>
        <p className="text-zinc-600">
          Final: <span className="font-medium text-zinc-900">{answer.final ?? "…"}</span> / {outOf}
        </p>
      </div>
      <p>{answer.question}</p>
      <dl className="grid gap-2 sm:grid-cols-[9rem_1fr]">
        <dt className="text-zinc-500">Student&apos;s answer</dt>
        <dd className="whitespace-pre-wrap">{given}</dd>
        {answer.heard && answer.heard !== answer.text && (
          <>
            <dt className="text-zinc-500">Speech heard</dt>
            <dd className="text-zinc-600">{answer.heard}</dd>
          </>
        )}
        {answer.hints_used > 0 && (
          <>
            <dt className="text-zinc-500">Hints</dt>
            <dd>{answer.hints_used}</dd>
          </>
        )}
        <dt className="text-zinc-500">Model answer</dt>
        <dd className="text-zinc-600">{answer.reference_answer}</dd>
        {answer.status === "scoring" ? (
          <>
            <dt className="text-zinc-500">AI</dt>
            <dd className="text-zinc-500">Still being scored…</dd>
          </>
        ) : (
          answer.answer_id && (
            <>
              <dt className="text-zinc-500">AI</dt>
              <dd className="space-y-1">
                <p>
                  <span className="font-medium">{answer.ai_score ?? "no mark"}</span>
                  {answer.ai_reason && ` · ${answer.ai_reason}`}
                </p>
                <ul className="space-y-1">
                  {(answer.key_points_hit ?? []).map((hit, i) => (
                    <li key={i}>
                      <span className={`mr-2 rounded px-1.5 py-0.5 text-xs ${VERDICT_STYLE[hit.verdict]}`}>{hit.verdict}</span>
                      {hit.key_point}
                      {hit.evidence && <span className="text-zinc-500"> · “{hit.evidence}”</span>}
                    </li>
                  ))}
                </ul>
                {answer.review_reason && <p className="text-amber-800">{answer.review_reason}</p>}
              </dd>
            </>
          )
        )}
        {answer.reviewed_at && (
          <>
            <dt className="text-zinc-500">Teacher</dt>
            <dd>
              {answer.teacher_score === null ? "Kept the AI's mark" : `Gave ${answer.teacher_score}`}
              {answer.teacher_note && ` · ${answer.teacher_note}`}
              <span className="text-zinc-500">
                {" "}
                ({answer.reviewed_by}, {new Date(answer.reviewed_at).toLocaleString()})
              </span>
            </dd>
          </>
        )}
      </dl>
      {editable && answer.answer_id && (
        <div className="flex flex-wrap items-center gap-2 border-t border-zinc-100 pt-3">
          <input
            type="number"
            min={0}
            max={outOf}
            step={0.25}
            value={mark}
            onChange={(e) => setMark(e.target.value)}
            placeholder="Your mark"
            className="w-28 rounded-md border border-zinc-300 px-2 py-1"
          />
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Why (optional)"
            className="min-w-48 flex-1 rounded-md border border-zinc-300 px-2 py-1"
          />
          <button
            disabled={busy || mark === ""}
            onClick={() => save(Number(mark))}
            className="rounded-md bg-zinc-900 px-3 py-1 text-white disabled:opacity-50"
          >
            Save my mark
          </button>
          <button
            disabled={busy || answer.ai_score === null}
            onClick={() => save(null)}
            className="rounded-md border border-zinc-300 px-3 py-1 disabled:opacity-50"
          >
            Keep AI mark
          </button>
        </div>
      )}
      {error && <p className="text-red-600">{error}</p>}
    </article>
  );
}
