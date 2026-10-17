"""The generate_questions job against the local database, with a fake language model."""

import pytest

from app.ai.llm import RateLimited
from app.ai.prompts.generate_questions import PROMPT_VERSION
from app.core.db import transaction
from app.jobs.queue import RetryLater
from app.modules.ingestion import ingest_job
from app.modules.questions import generate_job
from app.modules.questions.generation import PER_SLOT, pool_slots
from tests.fakes import FakeLanguageModel, full_reply
from tests.sample_manual import build_sample_manual


@pytest.fixture
def ready_manual(temp_chroma, subject, upload_manual):
    document_id = upload_manual(build_sample_manual())
    ingest_job.run({"document_id": document_id})
    with transaction() as conn:
        mix = conn.execute(
            "select question_type_mix from public.subjects where id = %s", (subject["id"],)
        ).fetchone()["question_type_mix"]
    return {
        "document_id": document_id,
        "experiment_id": subject["experiments"]["Stack Using Arrays"],
        "slots": pool_slots(mix),
    }


def use_model(monkeypatch, model: FakeLanguageModel) -> FakeLanguageModel:
    monkeypatch.setattr(generate_job, "get_language_model", lambda: model)
    return model


def questions_for(experiment_id: str) -> list[dict]:
    with transaction() as conn:
        return conn.execute(
            "select status, type, difficulty, prompt_version, model from public.questions where experiment_id = %s",
            (experiment_id,),
        ).fetchall()


def test_generation_saves_a_draft_pool_with_its_origin(monkeypatch, ready_manual):
    model = use_model(monkeypatch, FakeLanguageModel(reply=full_reply(ready_manual["slots"])))

    generate_job.run({"document_id": ready_manual["document_id"], "experiment_id": ready_manual["experiment_id"]})

    saved = questions_for(ready_manual["experiment_id"])
    assert len(saved) == PER_SLOT * len(ready_manual["slots"]) == 12
    assert {q["status"] for q in saved} == {"draft"}
    assert {(q["prompt_version"], q["model"]) for q in saved} == {(PROMPT_VERSION, "fake-model")}
    # The model was sent this experiment's material only.
    assert "Experiment 4: Stack Using Arrays" in model.calls[0][1]
    assert "first in, first out" not in model.calls[0][1]


def test_regenerating_replaces_drafts_but_keeps_the_teachers_decisions(monkeypatch, ready_manual):
    use_model(monkeypatch, FakeLanguageModel(reply=full_reply(ready_manual["slots"])))
    payload = {"document_id": ready_manual["document_id"], "experiment_id": ready_manual["experiment_id"]}
    generate_job.run(payload)
    with transaction() as conn:
        conn.execute(
            """
            update public.questions set status = 'approved'
            where id = (select id from public.questions where experiment_id = %s limit 1)
            """,
            (ready_manual["experiment_id"],),
        )

    generate_job.run(payload)  # a retry, or the teacher asking for fresh drafts

    statuses = [q["status"] for q in questions_for(ready_manual["experiment_id"])]
    assert statuses.count("approved") == 1
    assert statuses.count("draft") == 12


def test_rate_limit_becomes_a_postponement(monkeypatch, ready_manual):
    use_model(monkeypatch, FakeLanguageModel(error=RateLimited(retry_after=7)))

    with pytest.raises(RetryLater) as wait:
        generate_job.run({"document_id": ready_manual["document_id"], "experiment_id": ready_manual["experiment_id"]})

    assert wait.value.delay.total_seconds() == 8
    assert questions_for(ready_manual["experiment_id"]) == []
