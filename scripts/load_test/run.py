"""The 60-student load test: a whole batch takes a typed viva at once against the running API.

Run from backend/ with the venv active, while the API runs (uvicorn app.main:app):

    python ../scripts/load_test/run.py                # 60 students
    python ../scripts/load_test/run.py --students 20 --think 1

It builds its own throwaway subject, question pool and open exam, so it touches no real data
and makes no AI calls (scoring is measured separately; see docs/decisions/stage-5.md). Each
student is a real anonymous login. Everyone joins and starts at the same moment, the worst
case, then answers with a random pause. Some students double-click submit, and some lose the
reply and send the same submit again, as happens on weak lab Wi-Fi.

Afterwards it checks the database: every answer saved exactly once, every answer has its score
job, every viva completed. Then it removes everything it made. Local Supabase allows 200
anonymous sign-ins an hour per address (supabase/config.toml), so about three full runs an hour.
"""

import argparse
import asyncio
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

import httpx
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.core.config import get_settings  # noqa: E402
from app.core.db import close_pool, transaction  # noqa: E402

API = "http://127.0.0.1:8000"
SLOTS = [
    {"position": 1, "type": "concept", "difficulty": "easy", "answer_mode": "voice"},
    {"position": 2, "type": "trace_output", "difficulty": "easy", "answer_mode": "typed", "exact_match": True},
    {"position": 3, "type": "complexity", "difficulty": "medium", "answer_mode": "voice"},
    {"position": 4, "type": "compare_what_if", "difficulty": "medium", "answer_mode": "voice"},
]
# The spec's target: "Saved" shown within 2 seconds of submitting.
SUBMIT_TARGET_SECONDS = 2.0


class Timings:
    def __init__(self):
        self.seconds: dict[str, list[float]] = defaultdict(list)
        self.failures: dict[str, list[str]] = defaultdict(list)

    async def call(self, client: httpx.AsyncClient, label: str, method: str, path: str, **kwargs) -> httpx.Response:
        started = time.perf_counter()
        response = await client.request(method, path, **kwargs)
        self.seconds[label].append(time.perf_counter() - started)
        if response.status_code >= 400:
            self.failures[label].append(f"{response.status_code} {response.text[:120]}")
        return response


def set_up(teacher_id: str) -> dict:
    with transaction() as conn:
        conn.execute(
            "insert into auth.users (id, email, is_anonymous, raw_app_meta_data) values (%s, %s, false, %s)",
            (teacher_id, f"load.{teacher_id[:8]}@test.local", Jsonb({"role": "teacher"})),
        )
        subject = conn.execute(
            "insert into public.subjects (name, question_type_mix) values (%s, %s) returning id",
            (f"Load test {teacher_id[:8]}", Jsonb(SLOTS)),
        ).fetchone()["id"]
        experiment = conn.execute(
            "insert into public.experiments (subject_id, position, title) values (%s, 1, 'Load test experiment') returning id",
            (subject,),
        ).fetchone()["id"]
        for slot in SLOTS:
            for n in range(3):
                conn.execute(
                    """insert into public.questions (teacher_id, experiment_id, type, difficulty, question_text,
                       reference_answer, key_points, hints, status) values (%s,%s,%s,%s,%s,'3, 7',%s,%s,'approved')""",
                    (teacher_id, experiment, slot["type"], slot["difficulty"], f"Load test {slot['type']} {n}?",
                     Jsonb(["a key point"]), Jsonb(["a hint", "a stronger hint"])),
                )
        code = uuid4().hex[:6].upper()
        exam = conn.execute(
            """insert into public.exams (teacher_id, subject_id, section, title, join_code, config, state)
               values (%s, %s, 'LT', 'Load test', %s, %s, 'open') returning id""",
            (teacher_id, subject, code, Jsonb({"experiment_ids": [str(experiment)], "slots": SLOTS, "duration_minutes": 10,
                                               "marks_per_question": 4, "hint_penalty": 0.25})),
        ).fetchone()["id"]
    return {"subject": subject, "experiment": str(experiment), "exam": exam, "join_code": code}


def tear_down(teacher_id: str, setup: dict | None) -> None:
    with transaction() as conn:
        if setup:
            students = [str(r["student_id"]) for r in conn.execute(
                "select student_id from public.sessions where exam_id = %s", (setup["exam"],)).fetchall()]
            conn.execute(
                """delete from public.jobs where type = 'score' and payload->>'answer_id' in (
                     select a.id::text from public.answers a join public.session_questions sq on sq.id = a.session_question_id
                     join public.sessions s on s.id = sq.session_id where s.exam_id = %s)""",
                (setup["exam"],),
            )
            conn.execute("delete from public.exams where id = %s", (setup["exam"],))
            conn.execute("delete from public.questions where teacher_id = %s", (teacher_id,))
            conn.execute("delete from public.subjects where id = %s", (setup["subject"],))
            conn.execute("delete from auth.users where id = any(%s::uuid[])", (students,))
        conn.execute("delete from auth.users where id = %s", (teacher_id,))


async def student(n: int, setup: dict, timings: Timings, think: float, start_together: asyncio.Barrier,
                  sign_up: asyncio.Semaphore) -> dict:
    settings = get_settings()
    async with sign_up:  # sign-ups are spread a little; the viva itself starts all at once
        async with httpx.AsyncClient(timeout=60) as auth:
            login = (await auth.post(f"{settings.supabase_url}/auth/v1/signup",
                                     headers={"apikey": settings.supabase_anon_key}, json={})).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    outcome = {"submits": 0, "doubles": 0, "resends": 0}
    async with httpx.AsyncClient(base_url=API, headers=headers, timeout=60) as client:
        await start_together.wait()
        joined = await timings.call(client, "join", "POST", "/sessions", json={
            "join_code": setup["join_code"], "roll_number": f"LT{n:03d}", "experiment_id": setup["experiment"]})
        session_id = joined.json()["id"]
        await timings.call(client, "start", "POST", f"/sessions/{session_id}/start")
        for position in range(1, len(SLOTS) + 1):
            await asyncio.sleep(random.uniform(0, think))
            if random.random() < 0.2:
                await timings.call(client, "hint", "POST", f"/sessions/{session_id}/hints",
                                   json={"position": position, "hint_number": 1})
            body = {"position": position, "text": f"answer {position} from LT{n:03d}", "idempotency_key": uuid4().hex}
            roll = random.random()
            if roll < 0.2:  # a double click: the same submit twice at once
                await asyncio.gather(*(timings.call(client, "submit", "POST", f"/sessions/{session_id}/answers", json=body)
                                       for _ in range(2)))
                outcome["doubles"] += 1
            elif roll < 0.3:  # the reply was lost, so the page sends the same submit again
                await timings.call(client, "submit", "POST", f"/sessions/{session_id}/answers", json=body)
                await timings.call(client, "submit", "POST", f"/sessions/{session_id}/answers", json=body)
                outcome["resends"] += 1
            else:
                await timings.call(client, "submit", "POST", f"/sessions/{session_id}/answers", json=body)
            outcome["submits"] += 1
        await timings.call(client, "refresh", "GET", f"/sessions/{session_id}")
    return outcome


def check_database(setup: dict, students: int) -> dict:
    with transaction() as conn:
        return conn.execute(
            """
            select
                (select count(*) from public.sessions where exam_id = %(exam)s) as sessions,
                (select count(*) from public.sessions where exam_id = %(exam)s and state = 'completed') as completed,
                (select count(*) from public.answers a join public.session_questions sq on sq.id = a.session_question_id
                 join public.sessions s on s.id = sq.session_id where s.exam_id = %(exam)s) as answers,
                (select count(*) from public.jobs j where j.type = 'score' and j.payload->>'answer_id' in (
                    select a.id::text from public.answers a join public.session_questions sq on sq.id = a.session_question_id
                    join public.sessions s on s.id = sq.session_id where s.exam_id = %(exam)s)) as score_jobs
            """,
            {"exam": setup["exam"]},
        ).fetchone()


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


async def main(args) -> int:
    teacher_id = str(uuid4())
    setup = None
    try:
        setup = set_up(teacher_id)
        timings = Timings()
        barrier = asyncio.Barrier(args.students)
        sign_up = asyncio.Semaphore(10)
        print(f"{args.students} students, up to {args.think:g}s thinking per question ...")
        started = time.perf_counter()
        outcomes = await asyncio.gather(*(student(n, setup, timings, args.think, barrier, sign_up)
                                          for n in range(1, args.students + 1)), return_exceptions=True)
        took = time.perf_counter() - started
        crashed = [o for o in outcomes if isinstance(o, BaseException)]
        done = [o for o in outcomes if not isinstance(o, BaseException)]
        db = check_database(setup, args.students)
        expected = len(done) * len(SLOTS)

        print(f"\nfinished in {took:.1f}s; {len(done)} students completed their run, {len(crashed)} crashed")
        for o in crashed[:3]:
            print("  crash:", repr(o)[:200])
        print(f"double clicks: {sum(o['doubles'] for o in done)}, resent after a lost reply: {sum(o['resends'] for o in done)}")
        print(f"\n{'request':<8} {'count':>6} {'p50 s':>7} {'p95 s':>7} {'max s':>7} {'errors':>7}")
        for label, values in timings.seconds.items():
            print(f"{label:<8} {len(values):>6} {statistics.median(values):>7.3f} {percentile(values, 0.95):>7.3f} "
                  f"{max(values):>7.3f} {len(timings.failures[label]):>7}")
        for label, failures in timings.failures.items():
            for failure in failures[:3]:
                print(f"  {label} failed: {failure}")

        submit_p95 = percentile(timings.seconds["submit"], 0.95)
        results = [
            ("every student finished", not crashed and len(done) == args.students),
            (f"answers saved: {db['answers']} of {expected} submitted (no loss, no duplicates)", db["answers"] == expected),
            (f"every viva completed: {db['completed']} of {db['sessions']}", db["completed"] == db["sessions"] == len(done)),
            (f"every answer has its score job: {db['score_jobs']}", db["score_jobs"] == db["answers"]),
            (f"no request failed", not any(timings.failures.values())),
            (f"submit p95 {submit_p95:.2f}s, target {SUBMIT_TARGET_SECONDS:g}s", submit_p95 <= SUBMIT_TARGET_SECONDS),
        ]
        print()
        for label, ok in results:
            print(("PASS " if ok else "FAIL ") + label)
        return 0 if all(ok for _, ok in results) else 1
    finally:
        tear_down(teacher_id, setup)
        close_pool()
        print("\nremoved the load test's exam, questions, subject and logins")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python ../scripts/load_test/run.py")
    parser.add_argument("--students", type=int, default=60)
    parser.add_argument("--think", type=float, default=3, help="most seconds a student pauses before each answer")
    sys.exit(asyncio.run(main(parser.parse_args())))
