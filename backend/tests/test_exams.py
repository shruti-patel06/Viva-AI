"""Creating, opening and closing exams, against the local database."""

from app.core.db import transaction
from app.modules.exams.router import CODE_ALPHABET
from tests.conftest import OTHER_TEACHER, TEACHER_ID


def create(client, subject, experiments, **overrides):
    body = {
        "subject_id": str(subject["id"]),
        "title": "DS lab exam",
        "section": "CSE-A",
        "experiment_ids": [subject["experiments"][title] for title in experiments],
        **overrides,
    }
    return client.post("/exams", json=body)


def exam_row(exam_id) -> dict:
    with transaction() as conn:
        return conn.execute("select state, config from public.exams where id = %s", (exam_id,)).fetchone()


def test_creating_an_exam_gives_a_join_code_and_copies_the_mix(login, subject, pool):
    response = create(login(TEACHER_ID), subject, ["Stack Using Arrays"], duration_minutes=15)

    assert response.status_code == 201
    exam = response.json()
    assert len(exam["join_code"]) == 6 and set(exam["join_code"]) <= set(CODE_ALPHABET)
    row = exam_row(exam["id"])
    assert row["state"] == "draft"
    assert row["config"]["slots"] == pool["slots"]  # the live own-code slot is left out
    assert row["config"]["duration_minutes"] == 15
    assert row["config"]["hint_penalty"] == 0.25


def test_an_exam_only_takes_experiments_of_its_subject(login, subject):
    client = login(TEACHER_ID)

    response = create(client, subject, [], experiment_ids=["00000000-0000-0000-0000-000000000000"])

    assert response.status_code == 422
    assert create(client, subject, []).status_code == 422
    assert create(client, subject, ["Stack Using Arrays"], title="  ").status_code == 422


def test_opening_is_refused_until_every_slot_has_an_approved_question(login, subject, pool):
    client = login(TEACHER_ID)
    exam_id = create(client, subject, ["Stack Using Arrays", "Queue Using Arrays"]).json()["id"]

    response = client.post(f"/exams/{exam_id}/open")

    assert response.status_code == 409
    assert "Queue Using Arrays: concept (easy)" in response.json()["detail"]
    assert "Stack Using Arrays" not in response.json()["detail"]
    assert exam_row(exam_id)["state"] == "draft"


def test_opening_with_a_full_pool(login, subject, pool):
    client = login(TEACHER_ID)
    exam_id = create(client, subject, ["Stack Using Arrays"]).json()["id"]

    assert client.post(f"/exams/{exam_id}/open").json()["state"] == "open"
    assert client.post(f"/exams/{exam_id}/open").status_code == 200  # opening twice is harmless
    assert exam_row(exam_id)["state"] == "open"


def test_another_teacher_cannot_open_or_close_the_exam(login, subject, pool):
    exam_id = create(login(TEACHER_ID), subject, ["Stack Using Arrays"]).json()["id"]
    other = login(OTHER_TEACHER)

    assert other.post(f"/exams/{exam_id}/open").status_code == 404
    assert other.post(f"/exams/{exam_id}/close").status_code == 404


def test_closing_ends_every_unfinished_viva(login, students, exam):
    student = login(students[0])
    session_id = student.post(
        "/sessions",
        json={"join_code": exam["join_code"], "roll_number": "22CS001", "experiment_id": exam["experiment_id"]},
    ).json()["id"]
    student.post(f"/sessions/{session_id}/start")

    response = login(TEACHER_ID).post(f"/exams/{exam['id']}/close")

    assert response.json() == {"id": exam["id"], "state": "closed", "sessions_ended": 1}
    assert student.get(f"/sessions/{session_id}").json()["state"] == "completed"
    assert student.get(f"/join/{exam['join_code']}").status_code == 404


def test_a_draft_exam_cannot_be_closed(login, subject, pool):
    client = login(TEACHER_ID)
    exam_id = create(client, subject, ["Stack Using Arrays"]).json()["id"]

    assert client.post(f"/exams/{exam_id}/close").status_code == 409
