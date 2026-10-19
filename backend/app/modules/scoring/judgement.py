"""Reading the model's judgement of an answer, and checking it before it becomes a mark.

The reply is untrusted, like the model's output in stage 3: it must give exactly one verdict
for each key point, and the words it quotes as evidence must really be in the student's
answer. A verdict that quotes words the student never wrote is flagged for the teacher.
"""

import re
from dataclasses import dataclass
from typing import Any

from app.modules.scoring.marks import Verdict

# Below this the teacher is asked to check the mark.
LOW_CONFIDENCE = 0.6


class InvalidJudgement(ValueError):
    """The model's reply broke the rules. Worth retrying: the next reply may be fine."""


@dataclass(frozen=True)
class Judgement:
    verdicts: list[Verdict]
    evidence: list[str]
    confidence: float
    reason: str
    review_reason: str | None

    @property
    def needs_review(self) -> bool:
        return self.review_reason is not None


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def quoted_in(evidence: str, answer: str) -> bool:
    """True when the quoted words appear in the answer, ignoring case and punctuation."""
    quote = _words(evidence)
    return bool(quote) and f" {quote} " in f" {_words(answer)} "


def read_judgement(reply: dict[str, Any], key_points: list[str], answer: str) -> Judgement:
    by_point: dict[int, dict[str, Any]] = {}
    for item in reply.get("verdicts", []):
        n = item.get("key_point")
        if not isinstance(n, int) or not 1 <= n <= len(key_points) or n in by_point:
            raise InvalidJudgement(f"Unexpected or repeated key point number: {n!r}")
        if item.get("verdict") not in ("covered", "partly", "missed"):
            raise InvalidJudgement(f"Unknown verdict: {item.get('verdict')!r}")
        by_point[n] = item
    if len(by_point) != len(key_points):
        raise InvalidJudgement(f"Expected a verdict for each of the {len(key_points)} key points, got {len(by_point)}")
    reason = str(reply.get("reason", "")).strip()
    if not reason:
        raise InvalidJudgement("The reply gave no reason")

    items = [by_point[n] for n in range(1, len(key_points) + 1)]
    confidence = min(1.0, max(0.0, float(reply.get("confidence", 0))))
    unquoted = [
        str(n) for n, item in enumerate(items, start=1)
        if item["verdict"] != "missed" and not quoted_in(item.get("evidence", ""), answer)
    ]
    concerns = []
    if confidence < LOW_CONFIDENCE:
        concerns.append(f"The AI was unsure (confidence {confidence:.2f}).")
    if unquoted:
        concerns.append(f"The words quoted for key point {', '.join(unquoted)} are not in the answer.")
    return Judgement(
        verdicts=[item["verdict"] for item in items],
        evidence=[item.get("evidence", "") if item["verdict"] != "missed" else "" for item in items],
        confidence=confidence,
        reason=reason,
        review_reason=" ".join(concerns) or None,
    )
