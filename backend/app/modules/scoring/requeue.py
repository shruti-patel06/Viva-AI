"""Queue a score job for every answer that has no score and no score job waiting.

Answers saved before scoring existed (stage 4) never got a job. Run from backend/:

    python -m app.modules.scoring.requeue
"""

from app.core.db import close_pool, transaction
from app.modules.scoring.score_job import JOB_TYPE


def requeue_unscored() -> int:
    with transaction() as conn:
        return conn.execute(
            """
            insert into public.jobs (type, payload)
            select %s, jsonb_build_object('answer_id', a.id::text)
            from public.answers a
            where not exists (select 1 from public.scores s where s.answer_id = a.id)
              and not exists (
                  select 1 from public.jobs j
                  where j.type = %s and j.payload->>'answer_id' = a.id::text and j.status in ('queued', 'running')
              )
            """,
            (JOB_TYPE, JOB_TYPE),
        ).rowcount


if __name__ == "__main__":
    try:
        print(f"Queued {requeue_unscored()} answer(s) for scoring.")
    finally:
        close_pool()
