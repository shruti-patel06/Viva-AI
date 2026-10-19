"""Teacher review and the report: read the class sheet, set marks, approve, export.

The exam moves  open -> closed -> approved. Marks can be reviewed only while it is closed:
before that answers are still arriving, and after approval the marks are locked so the
exported report stays true.
"""

from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.core.auth import CurrentUser, require_teacher
from app.core.db import transaction
from app.modules.reports.export import to_pdf, to_xlsx
from app.modules.reports.sheet import Sheet, load_sheet, sheet_to_dict

router = APIRouter(tags=["reports"])

Teacher = Annotated[CurrentUser, Depends(require_teacher)]


def _sheet(exam_id: UUID, teacher: CurrentUser) -> Sheet:
    with transaction() as conn:
        sheet = load_sheet(conn, exam_id, teacher.id)
    if sheet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found")
    return sheet


@router.get("/exams/{exam_id}/sheet")
def class_sheet(exam_id: UUID, teacher: Teacher) -> dict[str, Any]:
    return sheet_to_dict(_sheet(exam_id, teacher))


class Review(BaseModel):
    # The teacher's mark, or null to accept the AI's mark as it is.
    teacher_score: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    note: str | None = Field(default=None, max_length=1000)


@router.put("/answers/{answer_id}/review")
def review_answer(answer_id: UUID, review: Review, teacher: Teacher) -> dict[str, Any]:
    """Record the teacher's decision on one answer. The AI's score and reason are kept."""
    with transaction() as conn:
        # FOR SHARE on the exam: approval (which locks it FOR UPDATE) waits for this to finish.
        exam = conn.execute(
            """
            select e.state, e.config, sc.ai_score from public.answers a
            left join public.scores sc on sc.answer_id = a.id
            join public.session_questions sq on sq.id = a.session_question_id
            join public.sessions s on s.id = sq.session_id
            join public.exams e on e.id = s.exam_id
            where a.id = %s and e.teacher_id = %s
            for share of e
            """,
            (answer_id, teacher.id),
        ).fetchone()
        if exam is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Answer not found")
        if exam["state"] == "open":
            raise HTTPException(status.HTTP_409_CONFLICT, "Close the exam before reviewing marks")
        if exam["state"] == "approved":
            raise HTTPException(status.HTTP_409_CONFLICT, "The marks are approved and locked")
        out_of = Decimal(str(exam["config"]["marks_per_question"]))
        if review.teacher_score is not None and review.teacher_score > out_of:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"A question is worth at most {out_of:g} marks")
        if review.teacher_score is None and exam["ai_score"] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "There is no AI mark to accept; enter a mark")

        note = (review.note or "").strip() or None
        # An answer still waiting for its score can be marked by the teacher directly.
        conn.execute(
            """
            insert into public.scores (answer_id, teacher_score, teacher_note, reviewed_by, reviewed_at)
            values (%s, %s, %s, %s, now())
            on conflict (answer_id) do update set
                teacher_score = excluded.teacher_score, teacher_note = excluded.teacher_note,
                reviewed_by = excluded.reviewed_by, reviewed_at = now(), updated_at = now()
            """,
            (answer_id, review.teacher_score, note, teacher.id),
        )
    return {"answer_id": str(answer_id), "teacher_score": review.teacher_score, "note": note}


@router.post("/exams/{exam_id}/approve")
def approve(exam_id: UUID, teacher: Teacher) -> dict[str, str]:
    """Approve the batch. Refused while any answer is unscored or flagged and not yet reviewed."""
    with transaction() as conn:
        exam = conn.execute(
            "select state from public.exams where id = %s and teacher_id = %s for update", (exam_id, teacher.id)
        ).fetchone()
        if exam is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found")
        if exam["state"] == "approved":
            return {"id": str(exam_id), "state": "approved"}
        if exam["state"] != "closed":
            raise HTTPException(status.HTTP_409_CONFLICT, "Close the exam before approving its marks")
        waiting = load_sheet(conn, exam_id, teacher.id).waiting()
        if waiting:
            examples = ", ".join(f"{s.roll_number} Q{a.position}" for s, a in waiting[:5])
            more = f" and {len(waiting) - 5} more" if len(waiting) > 5 else ""
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{len(waiting)} answer(s) still need a mark: {examples}{more}",
            )
        conn.execute(
            "update public.exams set state = 'approved', approved_at = now(), approved_by = %s where id = %s",
            (teacher.id, exam_id),
        )
    return {"id": str(exam_id), "state": "approved"}


def _approved_sheet(exam_id: UUID, teacher: CurrentUser) -> Sheet:
    sheet = _sheet(exam_id, teacher)
    if sheet.state != "approved":
        raise HTTPException(status.HTTP_409_CONFLICT, "Approve the marks before exporting the report")
    return sheet


def _file_name(sheet: Sheet, extension: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in f"{sheet.title}-{sheet.section}")
    return f"{safe.strip('-')}.{extension}"


@router.get("/exams/{exam_id}/report.xlsx")
def report_xlsx(exam_id: UUID, teacher: Teacher) -> Response:
    sheet = _approved_sheet(exam_id, teacher)
    return Response(
        to_xlsx(sheet),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{_file_name(sheet, "xlsx")}"'},
    )


@router.get("/exams/{exam_id}/report.pdf")
def report_pdf(exam_id: UUID, teacher: Teacher) -> Response:
    sheet = _approved_sheet(exam_id, teacher)
    return Response(
        to_pdf(sheet),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_file_name(sheet, "pdf")}"'},
    )
