"""Uploading lab material. The slow work happens later, in the worker."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.core import storage
from app.core.auth import CurrentUser, require_teacher
from app.core.config import get_settings
from app.core.db import transaction
from app.jobs.queue import enqueue

router = APIRouter(tags=["ingestion"])


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED)
def upload_document(
    teacher: Annotated[CurrentUser, Depends(require_teacher)],
    subject_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
) -> dict[str, str]:
    """Store the PDF, record it, queue it for processing, and reply straight away.

    202 Accepted means "received, not finished": the teacher watches documents.status
    move from uploaded to processing to ready.
    """
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    content = file.file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "PDF is too large")
    # Check the file's first bytes, not its name or the browser's content type, both of
    # which the client controls.
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only PDF files are accepted")

    with transaction() as conn:
        subject = conn.execute("select id from public.subjects where id = %s", (subject_id,)).fetchone()
    if subject is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown subject")

    document_id = uuid4()
    storage_path = f"{teacher.id}/{document_id}.pdf"
    file_name = (file.filename or "manual.pdf")[:200]

    # File first, then rows. If the rows fail, an unused file is left behind, which is
    # harmless. The other order could queue a job for a file that was never stored.
    storage.upload(storage_path, content, "application/pdf")

    with transaction() as conn:
        conn.execute(
            """
            insert into public.documents (id, teacher_id, subject_id, file_name, storage_path)
            values (%s, %s, %s, %s, %s)
            """,
            (document_id, teacher.id, subject_id, file_name, storage_path),
        )
        enqueue(conn, "ingest", {"document_id": str(document_id)})

    return {"id": str(document_id), "status": "uploaded"}
