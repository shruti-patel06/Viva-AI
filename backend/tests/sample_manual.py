"""A made-up Data Structures lab manual for tests. Never use a real manual with student data.

The contents page lists every experiment title, like a real manual, so the splitter has
to tell a contents line from the real heading.

Write it to a file with:  python -m tests.sample_manual sample_manual.pdf
"""

import sys

import pymupdf

EXPERIMENTS = {
    "Linear and Binary Search": (
        "Aim: To search for a key in an array using linear search and binary search.\n\n"
        "Binary search needs a sorted array. Each step compares the key with the middle "
        "element and discards one half, so it takes O(log n) comparisons. Linear search "
        "checks every element and takes O(n) time but works on unsorted data."
    ),
    "Sorting Algorithms": (
        "Aim: To sort an array using bubble sort, insertion sort and merge sort.\n\n"
        "Bubble sort repeatedly swaps adjacent elements that are out of order. Merge sort "
        "splits the array in half, sorts each half and merges them in O(n log n) time."
    ),
    "Singly Linked List": (
        "Aim: To implement insertion, deletion and traversal on a singly linked list.\n\n"
        "Each node holds data and a pointer to the next node. Inserting at the head takes "
        "O(1) time, while searching takes O(n) because nodes must be visited in order."
    ),
    "Stack Using Arrays": (
        "Aim: To implement push, pop and peek on a stack stored in an array.\n\n"
        "A stack is last in, first out. Push adds at the top, pop removes from the top. "
        "Popping an empty stack is an underflow; pushing onto a full array is an overflow."
    ),
    "Queue Using Arrays": (
        "Aim: To implement enqueue and dequeue on a queue stored in an array.\n\n"
        "A queue is first in, first out. A circular queue reuses freed slots at the front. "
        "Dequeue on an empty queue is an underflow and must be checked before removing."
    ),
    "Binary Search Tree": (
        "Aim: To insert, search and delete keys in a binary search tree.\n\n"
        "Every key in the left subtree is smaller and every key in the right subtree is "
        "larger. Insertion is O(log n) on a balanced tree but O(n) when keys arrive sorted."
    ),
    "Graph Traversal": (
        "Aim: To traverse a graph using breadth-first search and depth-first search.\n\n"
        "BFS uses a queue and visits nodes level by level. DFS uses a stack or recursion and "
        "goes as deep as possible before backtracking. Both take O(V + E) time."
    ),
}


def build_sample_manual(experiments: dict[str, str] = EXPERIMENTS) -> bytes:
    doc = pymupdf.open()

    contents = "Data Structures Lab Manual\n\nContents\n\n" + "\n".join(
        f"{n}. {title} .......... {n + 1}" for n, title in enumerate(experiments, start=1)
    )
    doc.new_page().insert_textbox(pymupdf.Rect(72, 72, 540, 770), contents, fontsize=11)

    for n, (title, body) in enumerate(experiments.items(), start=1):
        text = f"Experiment {n}: {title}\n\n{body}"
        doc.new_page().insert_textbox(pymupdf.Rect(72, 72, 540, 770), text, fontsize=11)

    return doc.tobytes()


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "sample_manual.pdf"
    with open(out, "wb") as f:
        f.write(build_sample_manual())
    print(f"Wrote {out}")
