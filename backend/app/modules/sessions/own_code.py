"""Questions about the student's own program, written live during the viva.

Nobody can prepare these in advance, so they skip the teacher's approval. To contain that,
each student gets only one, and the review page and report label it "generated live".

The student pastes their program before starting. That queues an own_code_question job at
high priority, so the question is written while the student answers the earlier questions.
If the question is not ready when the student reaches it (the AI service is slow, busy or
down), a ready-made question about their program is used instead after a short wait, so the
viva never stalls on it.

The program is untrusted input: it is only ever read, by the model as data, and never run.
"""

from datetime import timedelta
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.ai.llm import RateLimited, get_language_model
from app.ai.prompts.own_code_question import PROMPT_VERSION, build_messages, response_schema
from app.core.db import transaction
from app.jobs.queue import RetryLater
from app.worker.runner import Handler

JOB_TYPE = "own_code_question"
# Ahead of scoring: a student will be asked this question within minutes.
PRIORITY = 10
MAX_CODE_CHARS = 20_000
MAX_TOKENS = 1500
# How long a student waits at the own-code question before the ready-made one is used.
LIVE_WAIT = timedelta(seconds=45)

FALLBACK_VERSION = "own_code_fallback/v1"
FALLBACK = {
    "question": "Walk through how your program works for this experiment, then name one edge case, "
                "such as an empty or full structure, and say what your program does in that case.",
    "reference_answer": "A clear account of the program's main steps, plus one specific edge case and how "
                        "the program behaves in it, including whether it is handled correctly.",
    "key_points": [
        "Describes the main steps the program follows",
        "Names a specific edge case for this experiment",
        "Says what the program does in that edge case",
    ],
    "hints": ["Start from where your program begins.", "Think about what happens when the structure is empty."],
}


class InvalidQuestion(ValueError):
    """The model's question broke the rules. Worth retrying."""


def live_slots(config: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    """(position, slot) for each slot written live, by its position in the exam's mix."""
    return [(n, slot) for n, slot in enumerate(config["slots"], start=1) if slot.get("live")]


def check(reply: dict[str, Any]) -> dict[str, Any]:
    question = str(reply.get("question", "")).strip()
    reference = str(reply.get("reference_answer", "")).strip()
    key_points = [p.strip() for p in reply.get("key_points", []) if p and p.strip()]
    hints = [h.strip() for h in reply.get("hints", []) if h and h.strip()]
    if not question or not reference or not 1 <= len(key_points) <= 4 or not hints:
        raise InvalidQuestion("The question, its answer, 1 to 4 key points and a hint are all needed")
    return {"question": question, "reference_answer": reference, "key_points": key_points, "hints": hints[:2]}


def add_live_question(
    conn: Connection, session_id: UUID | str, position: int, content: dict[str, Any], prompt_version: str, model: str | None
) -> bool:
    """Put the question at its position, unless something is already there. True if added.

    The session row is locked first, so the job's question and the fallback can never both land.
    """
    session = conn.execute(
        """
        select s.experiment_id, e.teacher_id, e.config from public.sessions s
        join public.exams e on e.id = s.exam_id where s.id = %s for update of s
        """,
        (session_id,),
    ).fetchone()
    if session is None:
        return False
    taken = conn.execute(
        "select 1 from public.session_questions where session_id = %s and position = %s", (session_id, position)
    ).fetchone()
    if taken:
        return False
    slot = session["config"]["slots"][position - 1]
    question_id = conn.execute(
        """
        insert into public.questions (teacher_id, experiment_id, type, difficulty, question_text, reference_answer,
                                      key_points, hints, status, session_id, prompt_version, model)
        values (%s, %s, %s, %s, %s, %s, %s, %s, 'live', %s, %s, %s)
        returning id
        """,
        (
            session["teacher_id"], session["experiment_id"], slot["type"], slot["difficulty"], content["question"],
            content["reference_answer"], Jsonb(content["key_points"]), Jsonb(content["hints"]), session_id,
            prompt_version, model,
        ),
    ).fetchone()["id"]
    conn.execute(
        "insert into public.session_questions (session_id, question_id, position) values (%s, %s, %s)",
        (session_id, question_id, position),
    )
    return True


def fill_after_waiting(conn: Connection, session_id: UUID, config: dict[str, Any], current_position: int) -> None:
    """If the student has waited too long at an own-code question that is not ready, use the
    ready-made one. Called whenever the student's page asks where they are."""
    if not any(position == current_position for position, _ in live_slots(config)):
        return
    reached = conn.execute(
        """
        select coalesce(
            (select a.submitted_at from public.answers a
             join public.session_questions sq on sq.id = a.session_question_id
             where sq.session_id = s.id and sq.position = %s),
            s.started_at
        ) < now() - %s as waited_too_long
        from public.sessions s where s.id = %s
        """,
        (current_position - 1, LIVE_WAIT, session_id),
    ).fetchone()
    if reached and reached["waited_too_long"]:
        add_live_question(conn, session_id, current_position, FALLBACK, FALLBACK_VERSION, None)


def run(payload: dict[str, Any]) -> None:
    with transaction() as conn:
        context = conn.execute(
            """
            select s.state, e.config, x.title as experiment, sub.name as subject, cs.code,
                   array(select position from public.session_questions where session_id = s.id) as filled
            from public.sessions s
            join public.exams e on e.id = s.exam_id
            join public.experiments x on x.id = s.experiment_id
            join public.subjects sub on sub.id = e.subject_id
            join public.code_submissions cs on cs.session_id = s.id
            where s.id = %s
            """,
            (payload["session_id"],),
        ).fetchone()
    if context is None or context["state"] == "completed":
        return  # the session is gone or over: nobody will be asked this question
    for position, slot in live_slots(context["config"]):
        if position in context["filled"]:
            continue
        model = get_language_model()
        system, user = build_messages(context["subject"], context["experiment"], slot.get("description", ""), context["code"])
        try:
            reply = model.generate_json(system, user, response_schema(), "own_code_question", max_tokens=MAX_TOKENS)
        except RateLimited as limit:
            raise RetryLater(timedelta(seconds=limit.retry_after + 1), str(limit))
        with transaction() as conn:
            add_live_question(conn, payload["session_id"], position, check(reply), PROMPT_VERSION, model.model_id)


def on_give_up(payload: dict[str, Any], reason: str) -> None:
    """The question could not be written: use the ready-made one, so the viva can go on."""
    with transaction() as conn:
        config = conn.execute(
            "select e.config from public.sessions s join public.exams e on e.id = s.exam_id where s.id = %s",
            (payload["session_id"],),
        ).fetchone()
        if config is None:
            return
        for position, _ in live_slots(config["config"]):
            add_live_question(conn, payload["session_id"], position, FALLBACK, FALLBACK_VERSION, None)


handler = Handler(run=run, on_give_up=on_give_up)
