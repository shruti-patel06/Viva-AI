import pytest

from app.modules.ingestion.pdf import extract_text
from app.modules.ingestion.splitting import Experiment, ExperimentsNotFound, chunk_text, split_by_experiment
from tests.sample_manual import EXPERIMENTS, build_sample_manual

SAMPLE_EXPERIMENTS = [
    Experiment(id=f"exp-{n}", position=n, title=title) for n, title in enumerate(EXPERIMENTS, start=1)
]


def test_manual_splits_into_one_section_per_experiment_skipping_contents():
    sections = split_by_experiment(extract_text(build_sample_manual()), SAMPLE_EXPERIMENTS)

    assert set(sections) == {e.id for e in SAMPLE_EXPERIMENTS}
    stack = sections["exp-4"]
    assert stack.startswith("Experiment 4: Stack Using Arrays")
    assert "last in, first out" in stack
    assert "first in, first out" not in stack  # the queue section starts after it
    assert "Contents" not in sections["exp-1"]


def test_titles_match_regardless_of_case_spacing_and_prefix():
    text = "intro\nLAB 1 -   linear and binary   search\nbody one\n2) Sorting Algorithms\nbody two"
    experiments = SAMPLE_EXPERIMENTS[:2]

    sections = split_by_experiment(text, experiments)

    assert sections["exp-1"].endswith("body one")
    assert sections["exp-2"].endswith("body two")


def test_missing_titles_are_all_named_in_the_error():
    text = "Experiment 1: Linear and Binary Search\nbody"

    with pytest.raises(ExperimentsNotFound) as error:
        split_by_experiment(text, SAMPLE_EXPERIMENTS[:3])

    assert error.value.titles == ["Sorting Algorithms", "Singly Linked List"]


def test_a_title_mentioned_inside_a_sentence_is_not_a_heading():
    text = "Experiment 1: Linear and Binary Search\nWe revisit sorting algorithms later.\nbody"

    with pytest.raises(ExperimentsNotFound):
        split_by_experiment(text, SAMPLE_EXPERIMENTS[:2])


def test_chunks_stay_under_the_limit_and_overlap():
    text = "\n\n".join(f"Paragraph {n}. " + "word " * 60 for n in range(10))

    chunks = chunk_text(text, max_chars=400, overlap=80)

    assert len(chunks) > 1
    assert all(len(chunk) <= 400 for chunk in chunks)
    assert chunks[1].split()[0] in chunks[0]  # the second chunk starts with the first's tail


def test_short_text_is_one_chunk():
    assert chunk_text("Aim: push and pop.\n\nA stack is LIFO.") == ["Aim: push and pop.\n\nA stack is LIFO."]
