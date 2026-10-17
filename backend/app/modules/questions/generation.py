"""Writing an experiment's question pool with a language model, and checking what comes back.

The model's output is treated like any other untrusted input: it is checked against the
rules before anything is saved, and a pool that breaks them is rejected and retried.
"""

from dataclasses import dataclass
from typing import Any

from app.ai.llm import LanguageModel
from app.ai.prompts.generate_questions import build_messages, response_schema

# Questions per (type, difficulty) slot. With four pool slots this gives the spec's 12
# questions per experiment, so students sitting together rarely share a question.
PER_SLOT = 3
# Material sent to the model per experiment. Keeps one request inside the free tier's
# tokens-per-minute limit (about 2,000 tokens of material).
MAX_MATERIAL_CHARS = 8000


class InvalidQuestionSet(ValueError):
    """The model's answer broke the rules. Worth retrying: the next answer may be fine."""


@dataclass(frozen=True)
class GeneratedQuestion:
    type: str
    difficulty: str
    question_text: str
    reference_answer: str
    key_points: list[str]
    hints: list[str]


def pool_slots(question_type_mix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The slots the pool covers. Live slots (own code) are written during the exam."""
    return [slot for slot in question_type_mix if not slot.get("live")]


def generate_question_set(
    model: LanguageModel,
    subject: str,
    experiment: str,
    slots: list[dict[str, Any]],
    chunks: list[str],
) -> list[GeneratedQuestion]:
    material = "\n\n".join(chunks)[:MAX_MATERIAL_CHARS]
    system, user = build_messages(subject, experiment, slots, PER_SLOT, material)
    types = sorted({slot["type"] for slot in slots})
    reply = model.generate_json(system, user, response_schema(types), "question_pool")
    return check_question_set(reply, slots)


def check_question_set(reply: dict[str, Any], slots: list[dict[str, Any]]) -> list[GeneratedQuestion]:
    """Keep PER_SLOT good questions per slot, or raise InvalidQuestionSet."""
    by_slot: dict[tuple[str, str], list[GeneratedQuestion]] = {(s["type"], s["difficulty"]): [] for s in slots}

    for item in reply.get("questions", []):
        slot = (item.get("type"), item.get("difficulty"))
        if slot not in by_slot:
            continue  # a question for a slot we did not ask for
        key_points = [p.strip() for p in item.get("key_points", []) if p and p.strip()]
        hints = [h.strip() for h in item.get("hints", []) if h and h.strip()]
        question_text = (item.get("question") or "").strip()
        reference_answer = (item.get("reference_answer") or "").strip()
        if not question_text or not reference_answer or not 1 <= len(key_points) <= 4 or not hints:
            continue
        by_slot[slot].append(
            GeneratedQuestion(
                type=slot[0],
                difficulty=slot[1],
                question_text=question_text,
                reference_answer=reference_answer,
                key_points=key_points,
                hints=hints[:2],
            )
        )

    short = [f"{t}/{d} ({len(found)} of {PER_SLOT})" for (t, d), found in by_slot.items() if len(found) < PER_SLOT]
    if short:
        raise InvalidQuestionSet("Too few usable questions for: " + ", ".join(short))
    return [question for found in by_slot.values() for question in found[:PER_SLOT]]
