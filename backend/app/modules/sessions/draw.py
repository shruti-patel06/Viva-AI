"""Drawing a student's questions from the teacher's approved pool.

Every student gets one question from each slot of the exam's mix, so everyone faces the
same types and difficulties in the same order. Within a slot the draw prefers the question
given out least often in this exam so far, choosing at random among those, so students
sitting side by side rarely share a question.
"""

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb


class PoolIncomplete(Exception):
    """A slot has no approved question to draw from."""


def missing_slots(conn: Connection, teacher_id: UUID, experiment_ids: list[str], slots: list[dict[str, Any]]) -> list[str]:
    """Each slot with no approved question to draw, as "Experiment: type (difficulty)"."""
    rows = conn.execute(
        """
        select x.title, slot.type, slot.difficulty
        from public.experiments x
        cross join jsonb_to_recordset(%s) as slot (position int, type text, difficulty text)
        where x.id = any(%s::uuid[])
          and not exists (
              select 1 from public.questions q
              where q.teacher_id = %s and q.experiment_id = x.id and q.status = 'approved'
                and q.type = slot.type and q.difficulty = slot.difficulty
          )
        order by x.position, slot.position
        """,
        (Jsonb(slots), experiment_ids, teacher_id),
    ).fetchall()
    return [f"{row['title']}: {row['type']} ({row['difficulty']})" for row in rows]


def draw_questions(conn: Connection, session_id: UUID, exam: dict[str, Any], experiment_id: UUID) -> None:
    """Add one session_questions row per slot, in the slot order of the exam's mix."""
    for position, slot in enumerate(exam["config"]["slots"], start=1):
        drawn = conn.execute(
            """
            insert into public.session_questions (session_id, question_id, position)
            select %s, q.id, %s
            from public.questions q
            where q.teacher_id = %s and q.experiment_id = %s and q.status = 'approved'
              and q.type = %s and q.difficulty = %s
            order by (
                select count(*) from public.session_questions sq
                join public.sessions s on s.id = sq.session_id
                where sq.question_id = q.id and s.exam_id = %s
            ), random()
            limit 1
            returning id
            """,
            (
                session_id, position, exam["teacher_id"], experiment_id,
                slot["type"], slot["difficulty"], exam["id"],
            ),
        ).fetchone()
        if drawn is None:
            raise PoolIncomplete(f"{slot['type']} ({slot['difficulty']})")
