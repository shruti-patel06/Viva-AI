"""The score job against the local database, with a fake language model.

These call the job's functions directly rather than through the worker's queue, so they
never pick up a real job that happens to be waiting in a developer's local database.
"""

from decimal import Decimal

import pytest

from app.ai.llm import RateLimited
from app.ai.prompts.score_answer import PROMPT_VERSION
from app.core.db import transaction
from app.jobs.queue import RetryLater
from app.modules.scoring import score_job
from app.modules.scoring.judgement import InvalidJudgement
from tests.fakes import FakeLanguageModel

# Positions in the Data Structures mix the pool fixture copies.
CONCEPT, TRACE_OUTPUT = 1, 2


def verdict_reply(verdict="covered", evidence="last in first out", confidence=0.9):
    return {
        "verdicts": [{"key_point": 1, "verdict": verdict, "evidence": evidence}],
        "confidence": confidence,
        "reason": "Names the stack's order.",
    }


@pytest.fixture
def model(monkeypatch):
    def use(fake: FakeLanguageModel) -> FakeLanguageModel:
        monkeypatch.setattr(score_job, "get_language_model", lambda: fake)
        return fake

    return use


@pytest.fixture
def viva(login, students, exam):
    """A started viva. answer(text, hints=0) answers the current question and returns its id."""
    client = login(students[0])
    session_id = client.post(
        "/sessions", json={"join_code": exam["join_code"], "roll_number": "22CS001", "experiment_id": exam["experiment_id"]}
    ).json()["id"]
    client.post(f"/sessions/{session_id}/start")

    def answer(text: str, hints: int = 0) -> str:
        position = client.get(f"/sessions/{session_id}").json()["question"]["position"]
        for number in range(1, hints + 1):
            client.post(f"/sessions/{session_id}/hints", json={"position": position, "hint_number": number})
        client.post(
            f"/sessions/{session_id}/answers",
            json={"position": position, "text": text, "idempotency_key": f"key-{session_id}-{position}"},
        )
        with transaction() as conn:
            return str(conn.execute(
                """
                select a.id from public.answers a join public.session_questions sq on sq.id = a.session_question_id
                where sq.session_id = %s and sq.position = %s
                """,
                (session_id, position),
            ).fetchone()["id"])

    answer.session_id = session_id
    return answer


def score(answer_id: str) -> dict:
    with transaction() as conn:
        return conn.execute("select * from public.scores where answer_id = %s", (answer_id,)).fetchone()


def set_expected_output(session_id: str, position: int, output: str) -> None:
    with transaction() as conn:
        conn.execute(
            """
            update public.questions set reference_answer = %s, key_points = %s
            where id = (select question_id from public.session_questions where session_id = %s and position = %s)
            """,
            (output, f'["{output}"]', session_id, position),
        )


def test_submitting_an_answer_queues_its_score_job(viva):
    answer_id = viva("Last in, first out.")

    with transaction() as conn:
        jobs = conn.execute(
            "select status from public.jobs where type = 'score' and payload->>'answer_id' = %s", (answer_id,)
        ).fetchall()
    assert [j["status"] for j in jobs] == ["queued"]


def test_a_judged_answer_gets_marks_from_the_verdicts_and_a_reason(viva, model):
    fake = model(FakeLanguageModel(reply=verdict_reply()))
    answer_id = viva("A stack is last in first out.", hints=1)

    score_job.run({"answer_id": answer_id})

    saved = score(answer_id)
    assert saved["ai_score"] == Decimal("3.00")  # 4 marks, the one key point covered, one hint at 25%
    assert saved["reason"] == "Names the stack's order."
    assert saved["key_points_hit"] == [
        {"key_point": "secret key point", "verdict": "covered", "evidence": "last in first out"}
    ]
    assert (saved["prompt_version"], saved["model"], saved["needs_review"]) == (PROMPT_VERSION, "fake-model", False)
    # The model is shown the answer as data inside its own tags.
    assert "<student_answer>\nA stack is last in first out.\n</student_answer>" in fake.calls[0][1]


def test_partly_covered_earns_half(viva, model):
    model(FakeLanguageModel(reply=verdict_reply(verdict="partly", evidence="stack")))
    answer_id = viva("Something about a stack.")

    score_job.run({"answer_id": answer_id})

    assert score(answer_id)["ai_score"] == Decimal("2.00")


def test_an_exact_output_is_marked_in_code_without_the_model(viva, model):
    model(FakeLanguageModel(error=AssertionError("the model must not be called")))
    viva("Concept answer.")
    set_expected_output(viva.session_id, TRACE_OUTPUT, "3, 7")

    answer_id = viva("[3,7]")
    score_job.run({"answer_id": answer_id})

    saved = score(answer_id)
    assert (saved["ai_score"], saved["model"], saved["prompt_version"]) == (Decimal("4.00"), None, "exact_match/v1")


def test_a_wrong_output_scores_zero_and_one_inside_a_sentence_goes_to_the_teacher(viva, model):
    model(FakeLanguageModel(error=AssertionError("the model must not be called")))
    viva("Concept answer.")
    set_expected_output(viva.session_id, TRACE_OUTPUT, "3, 7")
    answer_id = viva("The stack holds 3, 7.")

    score_job.run({"answer_id": answer_id})

    saved = score(answer_id)
    assert (saved["ai_score"], saved["needs_review"]) == (Decimal("0.00"), True)


def test_a_skipped_question_scores_zero_without_the_model(viva, model):
    model(FakeLanguageModel(error=AssertionError("the model must not be called")))
    answer_id = viva("")

    score_job.run({"answer_id": answer_id})

    saved = score(answer_id)
    assert (saved["ai_score"], saved["reason"], saved["needs_review"]) == (Decimal("0.00"), "No answer was given.", False)


def test_an_unsure_judgement_is_flagged_for_the_teacher(viva, model):
    model(FakeLanguageModel(reply=verdict_reply(confidence=0.3)))
    answer_id = viva("Last in first out, maybe.")

    score_job.run({"answer_id": answer_id})

    saved = score(answer_id)
    assert saved["needs_review"] and "unsure" in saved["review_reason"]


def test_scoring_again_keeps_one_score_and_the_teachers_mark(viva, model):
    model(FakeLanguageModel(reply=verdict_reply()))
    answer_id = viva("A stack is last in first out.")
    score_job.run({"answer_id": answer_id})
    with transaction() as conn:
        conn.execute("update public.scores set teacher_score = 3.5 where answer_id = %s", (answer_id,))

    score_job.run({"answer_id": answer_id})

    with transaction() as conn:
        rows = conn.execute("select teacher_score from public.scores where answer_id = %s", (answer_id,)).fetchall()
    assert [r["teacher_score"] for r in rows] == [Decimal("3.50")]


def test_a_rate_limited_score_waits_as_long_as_the_service_asked(viva, model):
    model(FakeLanguageModel(error=RateLimited(retry_after=7)))
    answer_id = viva("Last in first out.")

    with pytest.raises(RetryLater) as wait:
        score_job.run({"answer_id": answer_id})

    assert wait.value.delay.total_seconds() == 8
    assert score(answer_id) is None


def test_a_reply_that_breaks_the_rules_is_retried_then_left_for_the_teacher(viva, model):
    model(FakeLanguageModel(reply={"verdicts": [], "confidence": 0.9, "reason": "?"}))
    answer_id = viva("Last in first out.")

    with pytest.raises(InvalidJudgement):  # the worker retries this, waiting longer each time
        score_job.run({"answer_id": answer_id})
    score_job.on_give_up({"answer_id": answer_id}, "InvalidJudgement: no verdicts")  # after the third attempt

    saved = score(answer_id)
    assert saved["ai_score"] is None
    assert saved["needs_review"] and saved["review_reason"].startswith("Could not be scored automatically")
