"""The generate_questions job: one experiment's material -> a pool of draft questions.

Safe to run twice: the drafts this document wrote for this experiment are replaced in the
same transaction that saves the new ones. Questions the teacher has already approved or
rejected are never touched.
"""

from datetime import timedelta
from typing import Any

from psycopg.types.json import Jsonb

from app.ai.llm import RateLimited, get_language_model
from app.ai.prompts.generate_questions import PROMPT_VERSION
from app.core.db import transaction
from app.jobs.queue import PermanentJobError, RetryLater
from app.modules.ingestion import vector_store
from app.modules.questions.generation import generate_question_set, pool_slots
from app.worker.runner import Handler

JOB_TYPE = "generate_questions"


def run(payload: dict[str, Any]) -> None:
    document_id, experiment_id = payload["document_id"], payload["experiment_id"]

    with transaction() as conn:
        context = conn.execute(
            """
            select d.teacher_id, d.status, s.name as subject, s.question_type_mix, e.title as experiment
            from public.documents d
            join public.subjects s on s.id = d.subject_id
            join public.experiments e on e.id = %s and e.subject_id = s.id
            where d.id = %s
            """,
            (experiment_id, document_id),
        ).fetchone()
    if context is None:
        raise PermanentJobError("The document or experiment no longer exists")
    if context["status"] != "ready":
        raise PermanentJobError("The document has not finished processing")

    chunks = vector_store.experiment_chunks(document_id, experiment_id)
    if not chunks:
        raise PermanentJobError(f"No material found for {context['experiment']}")

    model = get_language_model()
    try:
        questions = generate_question_set(
            model, context["subject"], context["experiment"], pool_slots(context["question_type_mix"]), chunks
        )
    except RateLimited as limit:
        raise RetryLater(timedelta(seconds=limit.retry_after + 1), str(limit))

    with transaction() as conn:
        conn.execute(
            """
            delete from public.questions
            where source_document_id = %s and experiment_id = %s and status = 'draft'
            """,
            (document_id, experiment_id),
        )
        for q in questions:
            conn.execute(
                """
                insert into public.questions (
                    teacher_id, experiment_id, source_document_id, type, difficulty, question_text,
                    reference_answer, key_points, hints, prompt_version, model
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    context["teacher_id"], experiment_id, document_id, q.type, q.difficulty, q.question_text,
                    q.reference_answer, Jsonb(q.key_points), Jsonb(q.hints), PROMPT_VERSION, model.model_id,
                ),
            )


def on_give_up(payload: dict[str, Any], reason: str) -> None:
    """Nothing extra to record: the teacher sees the failed job's reason on the status endpoint."""


handler = Handler(run=run, on_give_up=on_give_up)
