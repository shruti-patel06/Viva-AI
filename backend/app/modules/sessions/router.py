"""The viva session: joining, the question draw, and moving through the questions.

A session's whole state is in its database row: state, current_position and ends_at. The
API keeps nothing in memory between requests, so a refresh, a dropped connection or a
second copy of the API all pick up exactly where the student was.

States only move forward:

    joined --start--> in_progress --last answer, time up, or exam closed--> completed

Every change runs with the session row locked (SELECT ... FOR UPDATE), so two requests for
the same session, such as a double-clicked submit, run one after the other, never mixed.
"""

import logging
import re
from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from psycopg import Connection
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, Field, field_validator

from app.ai import speech
from app.core import storage
from app.core.auth import CurrentUser, require_student
from app.core.config import get_settings
from app.core.db import transaction
from app.jobs.queue import enqueue
from app.modules.scoring.score_job import JOB_TYPE as SCORE_JOB
from app.modules.sessions import own_code
from app.modules.sessions.draw import PoolIncomplete, draw_questions

router = APIRouter(tags=["sessions"])

Student = Annotated[CurrentUser, Depends(require_student)]

# When time runs out the page sends whatever was typed. An answer arriving this long after
# the deadline is still accepted, to allow for the trip over a slow lab network.
GRACE = timedelta(seconds=20)
ROLL_NUMBER = re.compile(r"^[A-Z0-9/-]{1,20}$")
# The page stops recording at 30 seconds, the most the speech service takes in one request;
# that much compressed speech is well under this.
MAX_RECORDING_BYTES = 2 * 1024 * 1024

log = logging.getLogger("sessions")


def _open_exam(conn: Connection, join_code: str, lock: bool = False) -> dict:
    # FOR SHARE stops the teacher closing the exam halfway through someone joining it.
    exam = conn.execute(
        "select id, teacher_id, title, section, config from public.exams where join_code = %s and state = 'open'"
        + (" for share" if lock else ""),
        (join_code.strip().upper(),),
    ).fetchone()
    if exam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No open exam has this code. Check it with your teacher.")
    return exam


@router.get("/join/{join_code}")
def find_exam(join_code: str, student: Student) -> dict[str, Any]:
    """What a student sees after typing the code: the exam, and the experiments to pick from."""
    with transaction() as conn:
        exam = _open_exam(conn, join_code)
        experiments = conn.execute(
            "select id, title from public.experiments where id = any(%s::uuid[]) order by position",
            (exam["config"]["experiment_ids"],),
        ).fetchall()
    return {
        "title": exam["title"],
        "section": exam["section"],
        "experiments": [{"id": str(e["id"]), "title": e["title"]} for e in experiments],
    }


class Join(BaseModel):
    join_code: str = Field(max_length=20)
    roll_number: str = Field(max_length=40)
    # The experiment the student performed in the lab; their questions come from it.
    experiment_id: UUID

    @field_validator("roll_number")
    @classmethod
    def clean_roll_number(cls, value: str) -> str:
        value = value.strip().upper()
        if not ROLL_NUMBER.match(value):
            raise ValueError("use up to 20 letters, digits, / or -")
        return value


@router.post("/sessions")
def join(request: Join, student: Student) -> dict[str, str]:
    """Join an exam and draw this student's questions.

    Joining again from the same login returns the same session, so a refresh or a second
    click never draws a second set of questions.
    """
    with transaction() as conn:
        exam = _open_exam(conn, request.join_code, lock=True)
        existing = _session_in_exam(conn, exam["id"], student)
        if existing:
            return existing
        if str(request.experiment_id) not in exam["config"]["experiment_ids"]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That experiment is not part of this exam")
        try:
            with conn.transaction():  # a savepoint, so a refused insert is all that is undone
                session_id = conn.execute(
                    """
                    insert into public.sessions (exam_id, student_id, roll_number, experiment_id)
                    values (%s, %s, %s, %s)
                    returning id
                    """,
                    (exam["id"], student.id, request.roll_number, request.experiment_id),
                ).fetchone()["id"]
        except UniqueViolation as error:
            if error.diag.constraint_name == "sessions_exam_id_student_id_key":
                # The same login joined in a request running alongside this one.
                return _session_in_exam(conn, exam["id"], student)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Roll number {request.roll_number} has already joined this exam. If that is you, tell your teacher.",
            )
        try:
            draw_questions(conn, session_id, exam, request.experiment_id)
        except PoolIncomplete as gap:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"This exam has no approved {gap} question yet. Tell your teacher."
            )
    return {"id": str(session_id), "state": "joined"}


def _session_in_exam(conn: Connection, exam_id: UUID, student: CurrentUser) -> dict[str, str] | None:
    row = conn.execute(
        "select id, state from public.sessions where exam_id = %s and student_id = %s",
        (exam_id, student.id),
    ).fetchone()
    return {"id": str(row["id"]), "state": row["state"]} if row else None


def _lock_session(conn: Connection, session_id: UUID, student: CurrentUser) -> dict:
    """The student's own session, locked until the transaction ends.

    Nothing runs a timer on the server. A session whose time ran out is completed here, the
    next time anyone touches it.
    """
    session = conn.execute(
        """
        select s.id, s.state, s.current_position, e.config,
               jsonb_array_length(e.config->'slots') as question_count,
               s.state = 'in_progress' and s.ends_at < now() as past_deadline,
               s.state = 'in_progress' and s.ends_at + %s < now() as time_up
        from public.sessions s
        join public.exams e on e.id = s.exam_id
        where s.id = %s and s.student_id = %s
        for update of s
        """,
        (GRACE, session_id, student.id),
    ).fetchone()
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    if session["time_up"]:
        _complete(conn, session_id)
        session["state"] = "completed"
    return session


def _complete(conn: Connection, session_id: UUID) -> None:
    conn.execute(
        "update public.sessions set state = 'completed', completed_at = now() where id = %s", (session_id,)
    )


def _current_question(conn: Connection, session: dict, position: int) -> dict:
    """The drawn question at `position`, if the student may act on it right now."""
    if session["state"] == "completed":
        raise HTTPException(status.HTTP_409_CONFLICT, "This viva has finished")
    if session["state"] != "in_progress":
        raise HTTPException(status.HTTP_409_CONFLICT, "Start the viva first")
    if position < session["current_position"]:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Question {position} has already been answered")
    if position > session["current_position"]:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Answer question {session['current_position']} first")
    drawn = conn.execute(
        """
        select sq.id, sq.hints_used, sq.transcript, q.hints
        from public.session_questions sq
        join public.questions q on q.id = sq.question_id
        where sq.session_id = %s and sq.position = %s
        """,
        (session["id"], position),
    ).fetchone()
    if drawn is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This question is still being prepared")
    return drawn


def _view(conn: Connection, session_id: UUID) -> dict[str, Any]:
    """Everything the viva page shows, and nothing a student should not see: the current
    question's text and the hints already taken, never answers or key points."""
    s = conn.execute(
        """
        select s.id, s.state, s.roll_number, s.current_position,
               case when s.ends_at is null then null
                    else greatest(0, ceil(extract(epoch from s.ends_at - now())))::int end as seconds_left,
               e.title as exam_title, e.config, x.title as experiment, cs.code,
               jsonb_array_length(e.config->'slots') as question_count,
               (select count(*) from public.answers a
                join public.session_questions sq on sq.id = a.session_question_id
                where sq.session_id = s.id) as answered
        from public.sessions s
        join public.exams e on e.id = s.exam_id
        join public.experiments x on x.id = s.experiment_id
        left join public.code_submissions cs on cs.session_id = s.id
        where s.id = %s
        """,
        (session_id,),
    ).fetchone()
    config = s["config"]
    view: dict[str, Any] = {
        "id": str(s["id"]),
        "state": s["state"],
        "exam_title": s["exam_title"],
        "experiment": s["experiment"],
        "roll_number": s["roll_number"],
        "question_count": s["question_count"],
        "answered": s["answered"],
        "seconds_left": s["seconds_left"],
        "duration_minutes": config["duration_minutes"],
        "marks_per_question": config["marks_per_question"],
        "hint_penalty": config["hint_penalty"],
        # The exam asks about the student's own program, which must be pasted before starting.
        "needs_code": bool(own_code.live_slots(config)),
        "code_saved": s["code"] is not None,
        "question": None,
        "preparing": False,
    }
    if s["state"] == "in_progress":
        q = conn.execute(
            """
            select sq.position, sq.hints_used, q.question_text, q.type, q.hints, q.status
            from public.session_questions sq
            join public.questions q on q.id = sq.question_id
            where sq.session_id = %s and sq.position = %s
            """,
            (session_id, s["current_position"]),
        ).fetchone()
        if q is None:  # a question about their program, still being written
            view["preparing"] = True
            return view
        view["question"] = {
            "position": q["position"],
            "text": q["question_text"],
            "type": q["type"],
            "answer_mode": config["slots"][q["position"] - 1].get("answer_mode"),
            "hints": q["hints"][: q["hints_used"]],
            "hints_left": len(q["hints"]) - q["hints_used"],
            # A question about their program is shown beside the program it asks about.
            "own_code": s["code"] if q["status"] == "live" else None,
        }
    return view


@router.get("/sessions/{session_id}")
def get_session(session_id: UUID, student: Student) -> dict[str, Any]:
    """Where the student is. The page loads this on every visit, which is what makes a
    refresh resume the viva instead of restarting it."""
    with transaction() as conn:
        session = _lock_session(conn, session_id, student)
        if session["state"] == "in_progress":
            own_code.fill_after_waiting(conn, session_id, session["config"], session["current_position"])
        return _view(conn, session_id)


@router.post("/sessions/{session_id}/start")
def start(session_id: UUID, student: Student) -> dict[str, Any]:
    """Start the clock. Starting again changes nothing: the clock started the first time."""
    with transaction() as conn:
        session = _lock_session(conn, session_id, student)
        if session["state"] == "joined":
            if own_code.live_slots(session["config"]) and not conn.execute(
                "select 1 from public.code_submissions where session_id = %s", (session_id,)
            ).fetchone():
                raise HTTPException(status.HTTP_409_CONFLICT, "Paste your program before you start")
            conn.execute(
                """
                update public.sessions
                set state = 'in_progress', started_at = now(), ends_at = now() + make_interval(mins => %s)
                where id = %s
                """,
                (session["config"]["duration_minutes"], session_id),
            )
        return _view(conn, session_id)


class HintRequest(BaseModel):
    position: int = Field(ge=1)
    # Which hint is wanted, counting from 1. Asking for a hint already shown returns it
    # again without counting it twice, so a double click costs one hint, not two.
    hint_number: int = Field(ge=1)


@router.post("/sessions/{session_id}/hints")
def take_hint(session_id: UUID, request: HintRequest, student: Student) -> dict[str, Any]:
    with transaction() as conn:
        session = _lock_session(conn, session_id, student)
        drawn = _current_question(conn, session, request.position)
        if request.hint_number > drawn["hints_used"]:
            if request.hint_number > len(drawn["hints"]):
                raise HTTPException(status.HTTP_409_CONFLICT, "There are no more hints for this question")
            if request.hint_number != drawn["hints_used"] + 1:
                raise HTTPException(status.HTTP_409_CONFLICT, "Hints are shown one at a time")
            conn.execute(
                "update public.session_questions set hints_used = %s where id = %s",
                (request.hint_number, drawn["id"]),
            )
        return _view(conn, session_id)


class AnswerSubmit(BaseModel):
    position: int = Field(ge=1)
    # Empty text means the student skipped the question.
    text: str = Field(max_length=4000)
    # Made by the page once per question and sent again with every retry of that submit.
    idempotency_key: str = Field(min_length=8, max_length=100)


@router.post("/sessions/{session_id}/answers")
def submit_answer(session_id: UUID, answer: AnswerSubmit, student: Student) -> dict[str, Any]:
    """Save the answer to the current question and move on to the next.

    The same submit sent twice (a double click, or a retry after the connection dropped
    before the reply came back) finds its answer already saved under its key, and gets the
    session as it now stands. The answer is saved once, not twice.
    """
    with transaction() as conn:
        session = _lock_session(conn, session_id, student)
        saved = conn.execute(
            """
            select sq.session_id from public.answers a
            join public.session_questions sq on sq.id = a.session_question_id
            where a.idempotency_key = %s
            """,
            (answer.idempotency_key,),
        ).fetchone()
        if saved:
            if saved["session_id"] != session_id:
                raise HTTPException(status.HTTP_409_CONFLICT, "This answer key has already been used")
            return _view(conn, session_id)

        drawn = _current_question(conn, session, answer.position)
        answer_id = conn.execute(
            """
            insert into public.answers (session_question_id, text, hints_used, idempotency_key, spoken_transcript)
            values (%s, %s, %s, %s, %s)
            returning id
            """,
            (drawn["id"], answer.text.strip(), drawn["hints_used"], answer.idempotency_key, drawn["transcript"]),
        ).fetchone()["id"]
        # Scoring happens later, in the worker. The job is saved in the same transaction as
        # the answer, so there is never an answer without its score job, or the other way round.
        enqueue(conn, SCORE_JOB, {"answer_id": str(answer_id)})
        conn.execute(
            "update public.sessions set current_position = current_position + 1 where id = %s", (session_id,)
        )
        # The last question, or an answer sent as time ran out: either way the viva is over.
        if answer.position >= session["question_count"] or session["past_deadline"]:
            _complete(conn, session_id)
        return _view(conn, session_id)


@router.get("/sessions/{session_id}/audio")
def question_audio(session_id: UUID, student: Student) -> dict[str, str]:
    """A short-lived link to the current question's stored audio. The audio was made when the
    teacher approved the question, so playing it costs no call to the speech service."""
    with transaction() as conn:
        session = _lock_session(conn, session_id, student)
        if session["state"] != "in_progress":
            raise HTTPException(status.HTTP_409_CONFLICT, "No question is being asked")
        row = conn.execute(
            """
            select q.audio_path from public.session_questions sq
            join public.questions q on q.id = sq.question_id
            where sq.session_id = %s and sq.position = %s
            """,
            (session_id, session["current_position"]),
        ).fetchone()
    if row is None or not row["audio_path"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This question has no audio; read it on screen")
    return {"url": storage.signed_url(get_settings().audio_bucket, row["audio_path"], expires_in_seconds=300)}


@router.post("/sessions/{session_id}/transcribe")
def transcribe_answer(
    session_id: UUID,
    student: Student,
    position: Annotated[int, Form(ge=1)],
    recording: Annotated[UploadFile, File()],
) -> dict[str, str]:
    """Turn one recording of the current question's answer into text, for the student to check
    and correct before submitting. A long answer can be recorded in several parts.

    This is the one AI call a student waits for, so a failure here never stops the viva: the
    reply tells the page to offer typing, and nothing about the session changes.
    """
    audio = recording.file.read(MAX_RECORDING_BYTES + 1)
    if len(audio) > MAX_RECORDING_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The recording is too long")
    if not audio:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The recording is empty")
    # Check the student may answer this question now, then let go of the row lock: the
    # speech service can take seconds, and nothing else should wait on it.
    with transaction() as conn:
        _current_question(conn, _lock_session(conn, session_id, student), position)

    try:
        heard = speech.transcribe(audio, recording.content_type or "application/octet-stream")
    except speech.SpeechUnavailable as error:
        log.warning("speech to text failed for session %s: %s", session_id, error)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Your recording could not be turned into text right now. Type your answer instead, or try recording again.",
        )

    if heard:
        with transaction() as conn:
            # Checked again: time may have run out while the speech service was working.
            drawn = _current_question(conn, _lock_session(conn, session_id, student), position)
            conn.execute(
                "update public.session_questions set transcript = concat_ws(' ', transcript, %s::text) where id = %s",
                (heard, drawn["id"]),
            )
    return {"transcript": heard}


class OwnCode(BaseModel):
    code: str = Field(min_length=1, max_length=own_code.MAX_CODE_CHARS)


@router.post("/sessions/{session_id}/code")
def save_code(session_id: UUID, submission: OwnCode, student: Student) -> dict[str, Any]:
    """Save the student's program, once, before they start, and queue the question about it.

    The program is kept as text. It is read by the model as data and is never run.
    """
    if not submission.code.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Paste your program")
    with transaction() as conn:
        session = _lock_session(conn, session_id, student)
        if not own_code.live_slots(session["config"]):
            raise HTTPException(status.HTTP_409_CONFLICT, "This exam has no question about your program")
        if session["state"] != "joined":
            raise HTTPException(status.HTTP_409_CONFLICT, "Your program is pasted before the viva starts")
        try:
            with conn.transaction():
                conn.execute(
                    "insert into public.code_submissions (session_id, code) values (%s, %s)",
                    (session_id, submission.code),
                )
        except UniqueViolation:
            raise HTTPException(status.HTTP_409_CONFLICT, "Your program has already been saved")
        enqueue(conn, own_code.JOB_TYPE, {"session_id": str(session_id)}, priority=own_code.PRIORITY)
        return _view(conn, session_id)
