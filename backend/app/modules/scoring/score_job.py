"""The score job: one submitted answer -> a suggested mark, the key points it covered, and a reason.

Three ways to mark, cheapest first:
- an empty answer (the student skipped) scores zero, with no AI call;
- an exact-match slot (one correct output) is compared in code, with no AI call;
- anything else is judged by the language model, key point by key point.

Safe to run twice: the score is written with an upsert on the answer, touching only the AI's
columns, so a rerun never duplicates a score or overwrites a teacher's mark.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from app.ai.llm import RateLimited, get_language_model
from app.ai.prompts.score_answer import PROMPT_VERSION, build_messages, response_schema
from app.core.db import transaction
from app.jobs.queue import RetryLater
from app.modules.scoring.judgement import read_judgement
from app.modules.scoring.marks import Verdict, compare_output, marks
from app.worker.runner import Handler

JOB_TYPE = "score"
# Versions for marks worked out in code, recorded where an AI score records its prompt.
EXACT_MATCH_VERSION = "exact_match/v1"
NO_ANSWER_VERSION = "no_answer/v1"
# A reply judging up to 4 key points with evidence uses about 200 to 350 tokens.
MAX_TOKENS = 1000


@dataclass(frozen=True)
class Result:
    verdicts: list[Verdict]
    evidence: list[str]
    confidence: float
    reason: str
    review_reason: str | None
    prompt_version: str
    model: str | None


def run(payload: dict[str, Any]) -> None:
    with transaction() as conn:
        answer = conn.execute(
            """
            select a.id, a.text, a.hints_used, sq.position,
                   q.question_text, q.reference_answer, q.key_points,
                   e.config, sub.name as subject
            from public.answers a
            join public.session_questions sq on sq.id = a.session_question_id
            join public.questions q on q.id = sq.question_id
            join public.sessions s on s.id = sq.session_id
            join public.exams e on e.id = s.exam_id
            join public.subjects sub on sub.id = e.subject_id
            where a.id = %s
            """,
            (payload["answer_id"],),
        ).fetchone()
    if answer is None:
        return  # the exam was deleted since: nothing left to score

    config = answer["config"]
    slot = config["slots"][answer["position"] - 1]
    key_points = answer["key_points"]
    if not answer["text"].strip():
        result = Result(
            ["missed"] * len(key_points), [""] * len(key_points), 1.0, "No answer was given.", None,
            NO_ANSWER_VERSION, None,
        )
    elif slot.get("exact_match"):
        result = _exact_match(answer["text"], answer["reference_answer"], len(key_points))
    else:
        result = _judge(answer, key_points)

    mark = marks(result.verdicts, config["marks_per_question"], answer["hints_used"], config["hint_penalty"])
    _save(payload["answer_id"], result, key_points, mark)


def _exact_match(text: str, expected: str, point_count: int) -> Result:
    outcome = compare_output(text, expected)
    if outcome == "match":
        return Result(["covered"] * point_count, [text.strip()] * point_count, 1.0,
                      "The output matches the expected output.", None, EXACT_MATCH_VERSION, None)
    if outcome == "inside":
        return Result(["missed"] * point_count, [""] * point_count, 0.5,
                      f"The expected output ({expected}) appears inside a longer answer.",
                      "Check whether the answer gives the right output with extra words.", EXACT_MATCH_VERSION, None)
    return Result(["missed"] * point_count, [""] * point_count, 1.0,
                  f"Expected {expected}.", None, EXACT_MATCH_VERSION, None)


def _judge(answer: dict[str, Any], key_points: list[str]) -> Result:
    model = get_language_model()
    system, user = build_messages(
        answer["subject"], answer["question_text"], answer["reference_answer"], key_points, answer["text"]
    )
    try:
        reply = model.generate_json(system, user, response_schema(), "answer_score", max_tokens=MAX_TOKENS)
    except RateLimited as limit:
        raise RetryLater(timedelta(seconds=limit.retry_after + 1), str(limit))
    judgement = read_judgement(reply, key_points, answer["text"])
    return Result(
        judgement.verdicts, judgement.evidence, judgement.confidence, judgement.reason,
        judgement.review_reason, PROMPT_VERSION, model.model_id,
    )


def _save(answer_id: str, result: Result, key_points: list[str], mark: Decimal) -> None:
    hits = [
        {"key_point": point, "verdict": verdict, "evidence": evidence}
        for point, verdict, evidence in zip(key_points, result.verdicts, result.evidence)
    ]
    with transaction() as conn:
        conn.execute(
            """
            insert into public.scores (answer_id, key_points_hit, ai_score, confidence, reason,
                                       needs_review, review_reason, prompt_version, model)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (answer_id) do update set
                key_points_hit = excluded.key_points_hit, ai_score = excluded.ai_score,
                confidence = excluded.confidence, reason = excluded.reason,
                needs_review = excluded.needs_review, review_reason = excluded.review_reason,
                prompt_version = excluded.prompt_version, model = excluded.model, updated_at = now()
            """,
            (
                answer_id, Jsonb(hits), mark, round(result.confidence, 2), result.reason,
                result.review_reason is not None, result.review_reason, result.prompt_version, result.model,
            ),
        )


def on_give_up(payload: dict[str, Any], reason: str) -> None:
    """Three failed attempts: record the answer as unscored and needing the teacher."""
    with transaction() as conn:
        conn.execute(
            """
            insert into public.scores (answer_id, needs_review, review_reason)
            values (%s, true, %s)
            on conflict (answer_id) do update set
                needs_review = true, review_reason = excluded.review_reason, updated_at = now()
            """,
            (payload["answer_id"], f"Could not be scored automatically: {reason}"),
        )


handler = Handler(run=run, on_give_up=on_give_up)
