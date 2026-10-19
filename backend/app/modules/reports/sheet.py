"""The class sheet: every student's answers in one exam, with both marks and how each was reached.

This is the one place that decides an answer's final mark, so the review page, the approval
check and both exports always agree:

    final mark = the teacher's mark if they set one, otherwise the AI's
    no answer  = 0

The AI's score is never overwritten. A teacher's mark sits beside it, so the record shows
what the AI suggested, what the teacher decided, and why.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from psycopg import Connection

Status = Literal["unanswered", "scoring", "to_check", "reviewed", "scored"]


@dataclass
class AnswerRow:
    position: int
    type: str
    question: str
    reference_answer: str
    key_points: list[str]
    answer_id: str | None
    text: str | None
    heard: str | None  # what speech to text heard, when the answer was spoken
    hints_used: int
    ai_score: Decimal | None
    confidence: Decimal | None
    ai_reason: str | None
    key_points_hit: list[dict[str, str]] | None
    needs_review: bool
    review_reason: str | None
    prompt_version: str | None
    model: str | None
    scored: bool  # a score row exists, even one where the AI gave up
    teacher_score: Decimal | None
    teacher_note: str | None
    reviewed_at: datetime | None
    reviewed_by: str | None

    @property
    def final(self) -> Decimal | None:
        if self.answer_id is None:
            return Decimal("0")
        return self.teacher_score if self.teacher_score is not None else self.ai_score

    @property
    def status(self) -> Status:
        if self.answer_id is None:
            return "unanswered"
        if not self.scored:
            return "scoring"  # its score job has not run yet
        if self.final is None:
            return "to_check"  # the AI could not mark it and the teacher has not yet
        if self.reviewed_at is not None:
            return "reviewed"
        if self.needs_review:
            return "to_check"
        return "scored"


@dataclass
class StudentRow:
    session_id: str
    roll_number: str
    experiment: str
    answers: list[AnswerRow] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((a.final or Decimal("0") for a in self.answers), Decimal("0"))


@dataclass
class Sheet:
    id: str
    title: str
    section: str
    subject: str
    state: str
    marks_per_question: Decimal
    hint_penalty: Decimal
    approved_at: datetime | None
    approved_by: str | None
    students: list[StudentRow]

    @property
    def max_marks(self) -> Decimal:
        count = max((len(s.answers) for s in self.students), default=0)
        return self.marks_per_question * count

    def waiting(self) -> list[tuple[StudentRow, AnswerRow]]:
        """Answers that still need a mark before the batch can be approved."""
        return [(s, a) for s in self.students for a in s.answers if a.status in ("scoring", "to_check")]


def load_sheet(conn: Connection, exam_id: UUID, teacher_id: UUID) -> Sheet | None:
    """The exam's sheet, or None if it is not this teacher's exam."""
    exam = conn.execute(
        """
        select e.id, e.title, e.section, e.state, e.config, e.approved_at, sub.name as subject, u.email as approved_by
        from public.exams e
        join public.subjects sub on sub.id = e.subject_id
        left join public.users u on u.id = e.approved_by
        where e.id = %s and e.teacher_id = %s
        """,
        (exam_id, teacher_id),
    ).fetchone()
    if exam is None:
        return None
    rows = conn.execute(
        """
        select s.id as session_id, s.roll_number, x.title as experiment,
               sq.position, q.type, q.question_text, q.reference_answer, q.key_points,
               a.id as answer_id, a.text, a.spoken_transcript, a.hints_used,
               sc.id as score_id, sc.ai_score, sc.confidence, sc.reason, sc.key_points_hit, sc.needs_review,
               sc.review_reason, sc.prompt_version, sc.model, sc.teacher_score, sc.teacher_note,
               sc.reviewed_at, reviewer.email as reviewed_by
        from public.sessions s
        join public.experiments x on x.id = s.experiment_id
        join public.session_questions sq on sq.session_id = s.id
        join public.questions q on q.id = sq.question_id
        left join public.answers a on a.session_question_id = sq.id
        left join public.scores sc on sc.answer_id = a.id
        left join public.users reviewer on reviewer.id = sc.reviewed_by
        where s.exam_id = %s
        order by s.roll_number, sq.position
        """,
        (exam_id,),
    ).fetchall()

    students: dict[str, StudentRow] = {}
    for r in rows:
        student = students.setdefault(
            str(r["session_id"]), StudentRow(str(r["session_id"]), r["roll_number"], r["experiment"])
        )
        student.answers.append(
            AnswerRow(
                position=r["position"], type=r["type"], question=r["question_text"],
                reference_answer=r["reference_answer"], key_points=r["key_points"],
                answer_id=str(r["answer_id"]) if r["answer_id"] else None, text=r["text"],
                heard=r["spoken_transcript"], hints_used=r["hints_used"] or 0,
                ai_score=r["ai_score"], confidence=r["confidence"], ai_reason=r["reason"],
                key_points_hit=r["key_points_hit"], needs_review=bool(r["needs_review"]),
                review_reason=r["review_reason"], prompt_version=r["prompt_version"], model=r["model"],
                scored=r["score_id"] is not None, teacher_score=r["teacher_score"], teacher_note=r["teacher_note"],
                reviewed_at=r["reviewed_at"], reviewed_by=r["reviewed_by"],
            )
        )
    config = exam["config"]
    return Sheet(
        id=str(exam["id"]), title=exam["title"], section=exam["section"], subject=exam["subject"],
        state=exam["state"], marks_per_question=Decimal(str(config["marks_per_question"])),
        hint_penalty=Decimal(str(config["hint_penalty"])), approved_at=exam["approved_at"],
        approved_by=exam["approved_by"], students=list(students.values()),
    )


def _number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def sheet_to_dict(sheet: Sheet) -> dict[str, Any]:
    """The sheet as JSON for the review page."""
    return {
        "id": sheet.id, "title": sheet.title, "section": sheet.section, "subject": sheet.subject,
        "state": sheet.state, "marks_per_question": _number(sheet.marks_per_question),
        "max_marks": _number(sheet.max_marks), "approved_at": sheet.approved_at, "approved_by": sheet.approved_by,
        "waiting": len(sheet.waiting()),
        "students": [
            {
                "session_id": s.session_id, "roll_number": s.roll_number, "experiment": s.experiment,
                "total": _number(s.total),
                "answers": [
                    {
                        "answer_id": a.answer_id, "position": a.position, "type": a.type, "question": a.question,
                        "reference_answer": a.reference_answer, "key_points": a.key_points, "text": a.text,
                        "heard": a.heard, "hints_used": a.hints_used, "ai_score": _number(a.ai_score),
                        "confidence": _number(a.confidence), "ai_reason": a.ai_reason,
                        "key_points_hit": a.key_points_hit, "review_reason": a.review_reason,
                        "prompt_version": a.prompt_version, "model": a.model,
                        "teacher_score": _number(a.teacher_score), "teacher_note": a.teacher_note,
                        "reviewed_at": a.reviewed_at, "reviewed_by": a.reviewed_by,
                        "final": _number(a.final), "status": a.status,
                    }
                    for a in s.answers
                ],
            }
            for s in sheet.students
        ],
    }
