"""Stand-ins for AI services, so tests are fast, free and give the same answer every time."""

from typing import Any


def question(type_: str, difficulty: str, n: int, **overrides: Any) -> dict[str, Any]:
    item = {
        "type": type_,
        "difficulty": difficulty,
        "question": f"{type_} question {n}?",
        "reference_answer": f"Reference answer {n}.",
        "key_points": [f"point {n}a", f"point {n}b"],
        "hints": [f"gentle hint {n}", f"strong hint {n}"],
    }
    item.update(overrides)
    return item


def full_reply(slots: list[dict[str, Any]], per_slot: int = 3) -> dict[str, Any]:
    return {
        "questions": [
            question(slot["type"], slot["difficulty"], n) for slot in slots for n in range(1, per_slot + 1)
        ]
    }


class FakeLanguageModel:
    model_id = "fake-model"

    def __init__(self, reply: dict[str, Any] | None = None, error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def generate_json(self, system: str, user: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
        self.calls.append((system, user))
        if self.error:
            raise self.error
        return self.reply
