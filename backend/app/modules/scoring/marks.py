"""The marking rule. The code, not the model, turns verdicts into marks, so every answer is
marked the same way and the whole rule is here:

    marks = marks_per_question x (credit for the key points / number of key points)
                               x (1 - hint_penalty x hints used)

A key point covered earns 1, partly covered 0.5, missed 0. The spec's worked example:
4 marks, 3 key points, 2 covered and one hint at 25% gives 4 x 2/3 x 0.75 = 2.0.

Fractions keep the sum exact (2/3 of 4 is not a tidy float); only the final mark is rounded.
"""

import re
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Literal

Verdict = Literal["covered", "partly", "missed"]
CREDIT: dict[str, Fraction] = {"covered": Fraction(1), "partly": Fraction(1, 2), "missed": Fraction(0)}


def marks(verdicts: list[Verdict], marks_per_question: float, hints_used: int, hint_penalty: float) -> Decimal:
    if not verdicts:
        return Decimal("0.00")
    covered = sum((CREDIT[v] for v in verdicts), Fraction(0)) / len(verdicts)
    # Enough hints can take a question to zero, never below.
    after_hints = max(Fraction(0), 1 - Fraction(str(hint_penalty)) * hints_used)
    exact = Fraction(str(marks_per_question)) * covered * after_hints
    return (Decimal(exact.numerator) / Decimal(exact.denominator)).quantize(Decimal("0.01"), ROUND_HALF_UP)


# The values in an output: numbers (with a sign or decimals) and words. Commas, brackets,
# arrows and spacing between them do not matter, so "3, 7", "3 7" and "[3,7]" all read as 3, 7.
_VALUE = re.compile(r"-?\d+(?:\.\d+)?|[a-z]+")


def output_values(text: str) -> list[str]:
    return _VALUE.findall(text.lower())


def _squash(text: str) -> str:
    return "".join(text.lower().split())


def compare_output(answer: str, expected: str) -> Literal["match", "inside", "different"]:
    """Compare an answer with the one correct output, ignoring case, spacing and punctuation.

    "inside" means the expected output appears within a longer answer ("the stack holds 3, 7"),
    which a teacher should look at rather than the code guessing.
    """
    given, wanted = output_values(answer), output_values(expected)
    if not wanted:  # an output with no values at all, such as "[]": compare the characters
        return "match" if _squash(answer) == _squash(expected) else "different"
    if given == wanted:
        return "match"
    n = len(wanted)
    if any(given[i : i + n] == wanted for i in range(len(given) - n + 1)):
        return "inside"
    return "different"
