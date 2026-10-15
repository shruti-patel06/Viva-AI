"""Splitting a manual's text into experiments, then into chunks small enough to embed."""

import re
from dataclasses import dataclass

# What may come before a title on a heading line: "Experiment 3:", "Exp 3 -", "Lab 3.",
# "Week 3)", or just "3.". Contents lines usually end in dots and a page number, so they
# do not match a heading, which has nothing after the title.
_HEADING_PREFIX = (
    r"(?:(?:experiment|exp\.?|lab|program|practical|week)\s*(?:no\.?\s*)?\d+\s*[:.\-–)]?\s*"
    r"|\d+\s*[:.\-–)]\s*)?"
)


@dataclass(frozen=True)
class Experiment:
    id: str
    position: int
    title: str


class ExperimentsNotFound(ValueError):
    def __init__(self, titles: list[str]):
        self.titles = titles
        super().__init__(
            "These experiment titles were not found as headings in the PDF: "
            + "; ".join(titles)
            + ". Check that the titles in the subject match the manual exactly."
        )


def _normalise(text: str) -> str:
    return " ".join(text.split()).lower()


def split_by_experiment(text: str, experiments: list[Experiment]) -> dict[str, str]:
    """Return {experiment_id: that experiment's text}.

    A title can appear more than once (contents page, then the real heading). Working from
    the last experiment backwards, each one takes its last heading that comes before the
    next experiment's start, which skips a contents page at the front.
    """
    lines = text.splitlines()
    normalised = [_normalise(line) for line in lines]
    ordered = sorted(experiments, key=lambda e: e.position)

    headings: dict[str, list[int]] = {}
    for experiment in ordered:
        pattern = re.compile(rf"^{_HEADING_PREFIX}{re.escape(_normalise(experiment.title))}$")
        headings[experiment.id] = [i for i, line in enumerate(normalised) if pattern.match(line)]

    missing = [e.title for e in ordered if not headings[e.id]]
    if missing:
        raise ExperimentsNotFound(missing)

    starts: dict[str, int] = {}
    next_start = len(lines)
    for experiment in reversed(ordered):
        before_next = [i for i in headings[experiment.id] if i < next_start]
        if not before_next:
            raise ExperimentsNotFound([f"{experiment.title} (appears out of order)"])
        starts[experiment.id] = next_start = before_next[-1]

    sections: dict[str, str] = {}
    for experiment, following in zip(ordered, ordered[1:] + [None]):
        end = starts[following.id] if following else len(lines)
        sections[experiment.id] = "\n".join(lines[starts[experiment.id] : end]).strip()
    return sections


def chunk_text(text: str, max_chars: int = 1000, overlap: int = 150) -> list[str]:
    """Pack paragraphs into chunks of at most max_chars.

    Each chunk after the first starts with the last ~overlap characters of the one before,
    so an idea cut at a boundary still appears whole in one of the two chunks.
    """
    pieces: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = " ".join(paragraph.split())
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            pieces.append(paragraph)
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            while len(sentence) > max_chars:
                pieces.append(sentence[:max_chars])
                sentence = sentence[max_chars:]
            if sentence:
                pieces.append(sentence)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}\n\n{piece}" if current else piece
        if len(candidate) <= max_chars:
            current = candidate
            continue
        chunks.append(current)
        tail = current[-overlap:]
        tail = tail[tail.find(" ") + 1 :] if " " in tail else tail
        current = f"{tail} {piece}" if len(tail) + 1 + len(piece) <= max_chars else piece
    if current:
        chunks.append(current)
    return chunks
