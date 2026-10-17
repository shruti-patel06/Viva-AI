import pytest

from app.modules.questions.generation import (
    MAX_MATERIAL_CHARS,
    PER_SLOT,
    InvalidQuestionSet,
    check_question_set,
    generate_question_set,
    pool_slots,
)
from tests.fakes import FakeLanguageModel, full_reply, question

MIX = [
    {"type": "concept", "difficulty": "easy", "description": "Ask why or how."},
    {"type": "trace_output", "difficulty": "easy", "description": "Ask for the exact output."},
    {"type": "complexity", "difficulty": "medium", "description": "Ask for the complexity."},
    {"type": "own_code", "difficulty": "hard", "live": True, "description": "About their own code."},
]
SLOTS = pool_slots(MIX)


def test_live_slots_are_left_out_of_the_pool():
    assert [s["type"] for s in SLOTS] == ["concept", "trace_output", "complexity"]


def test_prompt_carries_the_material_and_only_the_pool_slots():
    model = FakeLanguageModel(reply=full_reply(SLOTS))

    generate_question_set(model, "Data Structures", "Stacks", SLOTS, ["A stack is LIFO.", "Push adds."])

    system, user = model.calls[0]
    assert "Data Structures" in system
    assert "Experiment: Stacks" in user
    assert "<lab_material>\nA stack is LIFO.\n\nPush adds.\n</lab_material>" in user
    assert 'type "trace_output", difficulty "easy": Ask for the exact output.' in user
    assert "own_code" not in user


def test_material_is_capped_to_stay_inside_rate_limits():
    model = FakeLanguageModel(reply=full_reply(SLOTS))

    generate_question_set(model, "DS", "Stacks", SLOTS, ["~" * (MAX_MATERIAL_CHARS * 2)])

    assert model.calls[0][1].count("~") == MAX_MATERIAL_CHARS


def test_full_reply_gives_exactly_per_slot_questions_for_each_slot():
    reply = full_reply(SLOTS, per_slot=PER_SLOT + 2)  # the model wrote extra

    questions = check_question_set(reply, SLOTS)

    assert len(questions) == PER_SLOT * len(SLOTS)
    assert {(q.type, q.difficulty) for q in questions} == {(s["type"], s["difficulty"]) for s in SLOTS}


def test_reply_missing_a_slot_is_rejected_so_the_job_retries():
    reply = {"questions": [q for q in full_reply(SLOTS)["questions"] if q["type"] != "complexity"]}

    with pytest.raises(InvalidQuestionSet, match="complexity/medium"):
        check_question_set(reply, SLOTS)


def test_unusable_questions_are_dropped():
    reply = full_reply(SLOTS)
    reply["questions"] += [
        question("concept", "hard", 9),                      # a slot we did not ask for
        question("concept", "easy", 10, key_points=[]),       # nothing to mark against
        question("concept", "easy", 11, hints=["", " "]),     # no usable hint
        question("concept", "easy", 12, question="  "),       # empty question
    ]

    questions = check_question_set(reply, SLOTS)

    assert all(q.question_text.split()[-1] in {"1?", "2?", "3?"} for q in questions)


def test_hints_are_trimmed_to_two():
    reply = full_reply(SLOTS)
    reply["questions"][0]["hints"] = ["one", "two", "three"]

    assert check_question_set(reply, SLOTS)[0].hints == ["one", "two"]
