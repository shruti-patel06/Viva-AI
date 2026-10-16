"""The typed viva against the local database: joining, the draw, moving forward one question
at a time, hints, idempotent submits and time running out."""

import threading
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.core.auth import CurrentUser, require_student
from app.core.db import transaction
from app.modules.sessions.router import AnswerSubmit, submit_answer


def join(client, exam, roll="22CS001"):
    return client.post(
        "/sessions",
        json={"join_code": exam["join_code"], "roll_number": roll, "experiment_id": exam["experiment_id"]},
    )


def start(client, exam, roll="22CS001") -> str:
    session_id = join(client, exam, roll).json()["id"]
    assert client.post(f"/sessions/{session_id}/start").status_code == 200
    return session_id


def answer(client, session_id, position, text="My answer.", key=None):
    return client.post(
        f"/sessions/{session_id}/answers",
        json={"position": position, "text": text, "idempotency_key": key or str(uuid4())},
    )


def drawn(session_id) -> list[dict]:
    with transaction() as conn:
        return conn.execute(
            """
            select sq.position, sq.hints_used, q.id::text, q.type, q.difficulty, q.status
            from public.session_questions sq join public.questions q on q.id = sq.question_id
            where sq.session_id = %s order by sq.position
            """,
            (session_id,),
        ).fetchall()


def saved_answers(session_id) -> list[dict]:
    with transaction() as conn:
        return conn.execute(
            """
            select sq.position, a.text, a.hints_used
            from public.answers a join public.session_questions sq on sq.id = a.session_question_id
            where sq.session_id = %s order by sq.position
            """,
            (session_id,),
        ).fetchall()


def move_deadline(session_id, seconds_from_now: int) -> None:
    with transaction() as conn:
        conn.execute(
            "update public.sessions set ends_at = now() + make_interval(secs => %s) where id = %s",
            (seconds_from_now, session_id),
        )


def test_only_students_may_sit_a_viva():
    teacher = CurrentUser(id=uuid4(), role="teacher")
    with pytest.raises(HTTPException) as refused:
        require_student(teacher)
    assert refused.value.status_code == 403


def test_finding_an_exam_by_its_code(login, students, exam):
    client = login(students[0])

    found = client.get(f"/join/ {exam['join_code'].lower()} ")

    assert found.status_code == 200
    assert found.json()["title"] == "Test lab exam"
    assert [e["id"] for e in found.json()["experiments"]] == [exam["experiment_id"]]
    assert client.get("/join/NOSUCH").status_code == 404


def test_joining_draws_one_approved_question_per_slot_in_the_mix_order(login, students, exam, pool):
    client = login(students[0])

    response = join(client, exam)

    assert response.status_code == 200
    questions = drawn(response.json()["id"])
    assert [(q["type"], q["difficulty"]) for q in questions] == [(s["type"], s["difficulty"]) for s in pool["slots"]]
    assert [q["position"] for q in questions] == [1, 2, 3, 4]
    assert {q["status"] for q in questions} == {"approved"}
    view = client.get(f"/sessions/{response.json()['id']}").json()
    assert (view["state"], view["question_count"], view["question"]) == ("joined", 4, None)


def test_joining_again_returns_the_same_session_and_draws_nothing_new(login, students, exam):
    client = login(students[0])

    first = join(client, exam).json()["id"]
    second = join(client, exam).json()["id"]

    assert first == second
    assert len(drawn(first)) == 4


def test_a_roll_number_can_join_only_once(login, students, exam):
    assert join(login(students[0]), exam, roll="22cs001").status_code == 200

    response = join(login(students[1]), exam, roll=" 22CS001 ")

    assert response.status_code == 409
    assert "already joined" in response.json()["detail"]


def test_a_closed_exam_cannot_be_joined(login, students, exam):
    with transaction() as conn:
        conn.execute("update public.exams set state = 'closed' where id = %s", (exam["id"],))

    assert join(login(students[0]), exam).status_code == 404


def test_the_draw_spreads_the_pool_across_students(login, students, exam, pool):
    sessions = [join(login(student), exam, roll=f"22CS00{n}").json()["id"] for n, student in enumerate(students[:3])]

    for position, slot in enumerate(pool["slots"], start=1):
        given = {drawn(s)[position - 1]["id"] for s in sessions}
        # Three students, three approved questions in the slot: each gets a different one.
        assert given == set(pool["approved"][(slot["type"], slot["difficulty"])])


def test_a_join_that_cannot_draw_a_full_set_saves_nothing(login, students, exam, pool):
    slot = pool["slots"][2]
    with transaction() as conn:
        conn.execute(
            "update public.questions set status = 'rejected' where id = any(%s::uuid[])",
            (pool["approved"][(slot["type"], slot["difficulty"])],),
        )

    response = join(login(students[0]), exam)

    assert response.status_code == 409
    with transaction() as conn:
        assert conn.execute("select count(*) as n from public.sessions where exam_id = %s", (exam["id"],)).fetchone()["n"] == 0


def test_a_full_viva_moves_forward_to_completed(login, students, exam):
    client = login(students[0])
    session_id = join(client, exam).json()["id"]

    view = client.post(f"/sessions/{session_id}/start").json()
    assert view["state"] == "in_progress"
    assert 595 <= view["seconds_left"] <= 600
    assert view["question"]["position"] == 1
    # The page is sent the question, never what it is marked against.
    assert "Secret" not in str(view) and "secret" not in str(view)

    for position in range(1, 5):
        view = answer(client, session_id, position, text=f"Answer {position}").json()

    assert view["state"] == "completed"
    assert (view["answered"], view["question"]) == (4, None)
    assert [a["text"] for a in saved_answers(session_id)] == ["Answer 1", "Answer 2", "Answer 3", "Answer 4"]
    assert answer(client, session_id, 4).status_code == 409


def test_starting_twice_does_not_restart_the_clock(login, students, exam):
    client = login(students[0])
    session_id = start(client, exam)
    move_deadline(session_id, 120)

    assert client.post(f"/sessions/{session_id}/start").json()["seconds_left"] <= 120


def test_answers_must_wait_for_the_start_and_go_in_order(login, students, exam):
    client = login(students[0])
    session_id = join(client, exam).json()["id"]

    assert answer(client, session_id, 1).status_code == 409  # not started

    client.post(f"/sessions/{session_id}/start")
    assert answer(client, session_id, 2).status_code == 409  # skipping ahead
    assert answer(client, session_id, 1).status_code == 200
    assert answer(client, session_id, 1).status_code == 409  # going back, with a new key
    assert len(saved_answers(session_id)) == 1


def test_the_same_submit_twice_saves_one_answer(login, students, exam):
    client = login(students[0])
    session_id = start(client, exam)
    key = str(uuid4())

    first = answer(client, session_id, 1, key=key)
    again = answer(client, session_id, 1, key=key)

    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert again.json()["question"]["position"] == 2
    assert len(saved_answers(session_id)) == 1


def test_a_double_click_arriving_at_the_same_moment_saves_one_answer(login, students, exam):
    """The self-check question for this stage: two identical submits racing each other."""
    session_id = start(login(students[0]), exam)
    student = CurrentUser(id=UUID(students[0]), role="student")
    submit = AnswerSubmit(position=1, text="Last in, first out.", idempotency_key=str(uuid4()))
    both_ready = threading.Barrier(2)
    results, errors = [], []

    def click():
        both_ready.wait()
        try:
            results.append(submit_answer(UUID(session_id), submit, student))
        except Exception as error:  # noqa: BLE001 - the test reports whatever went wrong
            errors.append(error)

    clicks = [threading.Thread(target=click) for _ in range(2)]
    for thread in clicks:
        thread.start()
    for thread in clicks:
        thread.join()

    assert errors == []
    assert results[0] == results[1]
    assert len(saved_answers(session_id)) == 1


def test_another_student_cannot_see_or_answer_a_session(login, students, exam):
    session_id = start(login(students[0]), exam)
    other = login(students[1])

    assert other.get(f"/sessions/{session_id}").status_code == 404
    assert answer(other, session_id, 1).status_code == 404
    assert other.post(f"/sessions/{session_id}/hints", json={"position": 1, "hint_number": 1}).status_code == 404


def test_hints_are_shown_one_at_a_time_and_counted_once(login, students, exam):
    client = login(students[0])
    session_id = start(client, exam)

    def hint(number, position=1):
        return client.post(f"/sessions/{session_id}/hints", json={"position": position, "hint_number": number})

    view = hint(1).json()
    assert (view["question"]["hints"], view["question"]["hints_left"]) == (["gentle hint"], 1)
    assert hint(1).status_code == 200  # a double click
    assert drawn(session_id)[0]["hints_used"] == 1

    assert hint(2).json()["question"]["hints"] == ["gentle hint", "strong hint"]
    assert hint(3).status_code == 409  # only two hints

    # A refresh shows the hints already taken, and they stay counted.
    assert client.get(f"/sessions/{session_id}").json()["question"]["hints"] == ["gentle hint", "strong hint"]
    answer(client, session_id, 1)
    assert saved_answers(session_id)[0]["hints_used"] == 2
    assert hint(1, position=1).status_code == 409  # that question is answered
    assert client.get(f"/sessions/{session_id}").json()["question"]["hints"] == []


def test_hints_cannot_be_skipped_ahead(login, students, exam):
    client = login(students[0])
    session_id = start(client, exam)

    response = client.post(f"/sessions/{session_id}/hints", json={"position": 1, "hint_number": 2})

    assert response.status_code == 409
    assert drawn(session_id)[0]["hints_used"] == 0


def test_a_viva_past_its_time_is_completed_when_next_touched(login, students, exam):
    client = login(students[0])
    session_id = start(client, exam)
    move_deadline(session_id, -60)

    assert client.get(f"/sessions/{session_id}").json()["state"] == "completed"
    assert answer(client, session_id, 1).status_code == 409


def test_an_answer_sent_as_time_runs_out_is_saved_and_ends_the_viva(login, students, exam):
    client = login(students[0])
    session_id = start(client, exam)
    move_deadline(session_id, -5)  # inside the grace period

    view = answer(client, session_id, 1, text="Typed before the clock hit zero").json()

    assert view["state"] == "completed"
    assert [a["text"] for a in saved_answers(session_id)] == ["Typed before the clock hit zero"]


def test_an_empty_answer_skips_the_question(login, students, exam):
    client = login(students[0])
    session_id = start(client, exam)

    view = answer(client, session_id, 1, text="   ").json()

    assert view["question"]["position"] == 2
    assert saved_answers(session_id)[0]["text"] == ""
