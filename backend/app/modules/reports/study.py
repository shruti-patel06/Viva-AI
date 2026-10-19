"""The scoring study: does the AI mark like a teacher?

A teacher marks a random sample of answers without seeing the AI's scores, then the two are
compared. The spec's target: within 1 mark of the teacher on at least 80% of 30 answers.

Run from backend/:

    python -m app.modules.reports.study export <exam id> [<exam id> ...] --count 30 --out study.csv
        (open study.csv, fill in the your_mark column for every row, save it as CSV)
    python -m app.modules.reports.study compare study.csv

The CSV holds student answers: keep it out of git, and delete it once the result is recorded.
"""

import argparse
import csv
import random
import statistics
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.core.db import close_pool, transaction

COLUMNS = ["answer_id", "question", "model_answer", "key_points", "student_answer", "out_of", "your_mark"]
TARGET_WITHIN_ONE = 0.80


def _safe(text: str) -> str:
    """Spreadsheet apps run a cell starting with = + - or @ as a formula. Student text stays text."""
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def export(exam_ids: list[str], count: int, out: Path, seed: int | None = None) -> int:
    """Write a blind sample of AI-judged answers (no AI scores in the file). Returns how many."""
    with transaction() as conn:
        rows = conn.execute(
            """
            select a.id, q.question_text, q.reference_answer, q.key_points, a.text,
                   (e.config->>'marks_per_question')::numeric as out_of
            from public.answers a
            join public.scores sc on sc.answer_id = a.id
            join public.session_questions sq on sq.id = a.session_question_id
            join public.questions q on q.id = sq.question_id
            join public.sessions s on s.id = sq.session_id
            join public.exams e on e.id = s.exam_id
            where e.id = any(%s::uuid[]) and sc.ai_score is not null and sc.prompt_version like 'score_answer/%%'
            """,
            (exam_ids,),
        ).fetchall()
    sample = random.Random(seed).sample(rows, min(count, len(rows)))
    with out.open("w", newline="", encoding="utf-8-sig") as file:  # utf-8-sig so Excel reads it correctly
        writer = csv.writer(file)
        writer.writerow(COLUMNS)
        for r in sample:
            writer.writerow([r["id"], _safe(r["question_text"]), _safe(r["reference_answer"]),
                             _safe(" | ".join(r["key_points"])), _safe(r["text"]), f"{r['out_of']:g}", ""])
    return len(sample)


@dataclass
class Result:
    compared: int
    within_one: float
    exact: float
    mean_difference: float
    largest: list[tuple[str, Decimal, Decimal]]  # (answer id, teacher, AI), biggest gaps first

    @property
    def meets_target(self) -> bool:
        return self.compared > 0 and self.within_one >= TARGET_WITHIN_ONE


def compare(path: Path) -> Result:
    teacher: dict[str, Decimal] = {}
    with path.open(newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            mark = (row.get("your_mark") or "").strip()
            if not mark:
                continue
            try:
                teacher[row["answer_id"]] = Decimal(mark)
            except InvalidOperation:
                raise ValueError(f"Not a mark for {row['answer_id']}: {mark!r}")
    if not teacher:
        raise ValueError("No marks found: fill in the your_mark column first")
    with transaction() as conn:
        ai = {
            str(r["answer_id"]): r["ai_score"]
            for r in conn.execute(
                "select answer_id, ai_score from public.scores where answer_id = any(%s::uuid[])", (list(teacher),)
            ).fetchall()
        }
    pairs = [(answer_id, teacher[answer_id], ai[answer_id]) for answer_id in teacher if ai.get(answer_id) is not None]
    gaps = [abs(t - a) for _, t, a in pairs]
    return Result(
        compared=len(pairs),
        within_one=sum(gap <= 1 for gap in gaps) / len(pairs),
        exact=sum(gap == 0 for gap in gaps) / len(pairs),
        mean_difference=float(statistics.mean(gaps)),
        largest=sorted(pairs, key=lambda p: abs(p[1] - p[2]), reverse=True)[:5],
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.modules.reports.study")
    commands = parser.add_subparsers(dest="command", required=True)
    exporting = commands.add_parser("export", help="write a blind sample for a teacher to mark")
    exporting.add_argument("exam_ids", nargs="+")
    exporting.add_argument("--count", type=int, default=30)
    exporting.add_argument("--out", type=Path, default=Path("study.csv"))
    comparing = commands.add_parser("compare", help="compare the teacher's marks with the AI's")
    comparing.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "export":
            written = export(args.exam_ids, args.count, args.out)
            print(f"Wrote {written} answers to {args.out}. Mark each one in the your_mark column, without looking up the AI's score.")
            return 0
        result = compare(args.path)
        print(f"Compared {result.compared} answers")
        print(f"Within 1 mark of the teacher: {result.within_one:.0%} (target {TARGET_WITHIN_ONE:.0%})")
        print(f"Exactly the same mark:        {result.exact:.0%}")
        print(f"Mean difference:              {result.mean_difference:.2f} marks")
        print("Largest differences (answer, teacher, AI):")
        for answer_id, t, a in result.largest:
            print(f"  {answer_id}  {t:g}  {a:g}")
        print("MEETS the target" if result.meets_target else "Does NOT meet the target")
        return 0 if result.meets_target else 1
    finally:
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
