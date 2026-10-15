"""The ingest job: PDF in storage -> text -> experiments -> chunks -> vectors in Chroma.

Safe to run twice: it re-reads everything from the source and replaces this document's
chunks, so a retry or a pick-up after a crash leaves exactly one copy.
"""

from typing import Any

from app.core import storage
from app.core.db import transaction
from app.jobs.queue import PermanentJobError
from app.modules.ingestion import vector_store
from app.modules.ingestion.pdf import extract_text
from app.modules.ingestion.splitting import Experiment, ExperimentsNotFound, chunk_text, split_by_experiment
from app.worker.runner import Handler


def run(payload: dict[str, Any]) -> None:
    document_id = payload["document_id"]

    with transaction() as conn:
        document = conn.execute(
            """
            update public.documents set status = 'processing', error = null
            where id = %s
            returning subject_id, storage_path
            """,
            (document_id,),
        ).fetchone()
        if document is None:
            raise PermanentJobError("The document no longer exists")
        experiments = [
            Experiment(id=str(row["id"]), position=row["position"], title=row["title"])
            for row in conn.execute(
                "select id, position, title from public.experiments where subject_id = %s",
                (document["subject_id"],),
            )
        ]
    if not experiments:
        raise PermanentJobError("This subject has no experiments yet")

    text = extract_text(storage.download(document["storage_path"]))
    try:
        sections = split_by_experiment(text, experiments)
    except ExperimentsNotFound as error:
        raise PermanentJobError(str(error))

    # Numbered across the whole document, since chunk ids are "<document_id>:<index>".
    chunks: list[vector_store.Chunk] = []
    for experiment_id, section in sections.items():
        for text_piece in chunk_text(section):
            chunks.append(vector_store.Chunk(experiment_id, len(chunks), text_piece))
    vector_store.replace_document_chunks(document_id, str(document["subject_id"]), chunks)

    with transaction() as conn:
        conn.execute(
            "update public.documents set status = 'ready', error = null where id = %s",
            (document_id,),
        )


def on_give_up(payload: dict[str, Any], reason: str) -> None:
    """Show the teacher why processing failed."""
    with transaction() as conn:
        conn.execute(
            "update public.documents set status = 'failed', error = %s where id = %s",
            (reason, payload["document_id"]),
        )


handler = Handler(run=run, on_give_up=on_give_up)
