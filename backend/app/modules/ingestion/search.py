"""Search the processed lab material of one experiment. Run from backend/:

    python -m app.modules.ingestion.search "Stack Using Arrays" "what happens when you pop an empty stack"
"""

import sys

from app.core.db import close_pool, transaction
from app.modules.ingestion import vector_store


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    title, query = sys.argv[1], sys.argv[2]

    with transaction() as conn:
        row = conn.execute("select id from public.experiments where title ilike %s", (title,)).fetchone()
    close_pool()
    if row is None:
        sys.exit(f"No experiment titled {title!r}")

    for hit in vector_store.search(query, str(row["id"]), limit=3):
        print(f"[distance {hit['distance']:.3f}, chunk {hit['chunk_index']}]")
        print(hit["text"])
        print()


if __name__ == "__main__":
    main()
