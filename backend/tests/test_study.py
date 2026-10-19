"""The scoring study tooling: a blind export, then the teacher's marks compared with the AI's."""

import csv
from decimal import Decimal

import pytest

from app.core.db import transaction
from app.modules.reports import study
from app.modules.scoring import score_job
from tests.fakes import FakeLanguageModel

COVERED = {"verdicts": [{"key_point": 1, "verdict": "covered", "evidence": "stack"}], "confidence": 0.9, "reason": "ok"}


@pytest.fixture
def scored(login, students, exam, monkeypatch):
    """Four answers: three judged by the AI (4 marks each) and one exact-match output."""
    monkeypatch.setattr(score_job, "get_language_model", lambda: FakeLanguageModel(reply=COVERED))
    client = login(students[0])
    sid = client.post("/sessions", json={"join_code": exam["join_code"], "roll_number": "22CS001",
                                         "experiment_id": exam["experiment_id"]}).json()["id"]
    client.post(f"/sessions/{sid}/start")
    texts = ["a stack", "3, 7", "=HYPERLINK(\"x\") stack", "stack underflow"]
    for position, text in enumerate(texts, start=1):
        client.post(f"/sessions/{sid}/answers", json={"position": position, "text": text, "idempotency_key": f"s-{sid}-{position}"})
    with transaction() as conn:
        ids = [str(r["id"]) for r in conn.execute(
            """select a.id from public.answers a join public.session_questions sq on sq.id = a.session_question_id
               where sq.session_id = %s order by sq.position""", (sid,)).fetchall()]
    for answer_id in ids:
        score_job.run({"answer_id": answer_id})
    return {"exam": exam["id"], "ids": ids}


def read(path):
    with path.open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def test_the_export_is_blind_and_only_has_answers_the_ai_judged(scored, tmp_path):
    out = tmp_path / "study.csv"

    written = study.export([scored["exam"]], count=30, out=out, seed=1)

    rows = read(out)
    assert written == len(rows) == 3  # the exact-match answer was not judged by the AI
    assert scored["ids"][1] not in {r["answer_id"] for r in rows}
    assert list(rows[0]) == study.COLUMNS  # no AI score anywhere in the file
    assert {r["out_of"] for r in rows} == {"4"}
    risky = next(r for r in rows if "HYPERLINK" in r["student_answer"])
    assert risky["student_answer"].startswith("'=")  # stays text in a spreadsheet


def test_comparing_reports_agreement_within_one_mark(scored, tmp_path):
    out = tmp_path / "study.csv"
    study.export([scored["exam"]], count=30, out=out, seed=1)
    rows = read(out)
    # The AI gave 4 to each. The teacher agrees on one, is 1 off on one, and 3 off on the last.
    for row, mark in zip(rows, ["4", "3", "1"]):
        row["your_mark"] = mark
    with out.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=study.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    result = study.compare(out)

    assert result.compared == 3
    assert result.within_one == pytest.approx(2 / 3)
    assert result.exact == pytest.approx(1 / 3)
    assert result.mean_difference == pytest.approx(4 / 3)
    assert result.largest[0][1:] == (Decimal("1"), Decimal("4.00"))  # the biggest gap is listed first
    assert not result.meets_target


def test_comparing_an_unmarked_file_says_so(scored, tmp_path):
    out = tmp_path / "study.csv"
    study.export([scored["exam"]], count=30, out=out)

    with pytest.raises(ValueError, match="No marks found"):
        study.compare(out)
