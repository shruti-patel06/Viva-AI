"""The question_audio job: an approved question's text -> MP3 in storage, made once.

The file is named after a fingerprint (hash) of the exact text. If the teacher edits the
question, the text, and so the name, changes: old audio can never be played for new text.
Running the job again for unchanged text finds the file already recorded and does nothing.
"""

import hashlib
from datetime import timedelta
from typing import Any

from app.ai import speech
from app.ai.llm import RateLimited
from app.core import storage
from app.core.config import get_settings
from app.core.db import transaction
from app.jobs.queue import RetryLater
from app.worker.runner import Handler

JOB_TYPE = "question_audio"


def audio_path(teacher_id: str, question_id: str, text: str) -> str:
    fingerprint = hashlib.sha256(text.encode()).hexdigest()[:16]
    return f"{teacher_id}/{question_id}-{fingerprint}.mp3"


def run(payload: dict[str, Any]) -> None:
    with transaction() as conn:
        question = conn.execute(
            "select teacher_id, question_text, status, audio_path from public.questions where id = %s",
            (payload["question_id"],),
        ).fetchone()
    if question is None or question["status"] != "approved":
        return  # deleted, or sent back to draft since it was queued: nothing to speak

    text = question["question_text"]
    path = audio_path(str(question["teacher_id"]), payload["question_id"], text)
    if question["audio_path"] == path:
        return  # already made for exactly this text

    try:
        audio = speech.synthesize(text)
    except RateLimited as limit:
        raise RetryLater(timedelta(seconds=limit.retry_after + 1), str(limit))
    storage.upload(get_settings().audio_bucket, path, audio, "audio/mpeg", overwrite=True)

    with transaction() as conn:
        # Only if the text is still the one we spoke: an edit in the meantime wins.
        conn.execute(
            """
            update public.questions set audio_path = %s
            where id = %s and status = 'approved' and question_text = %s
            """,
            (path, payload["question_id"], text),
        )


def on_give_up(payload: dict[str, Any], reason: str) -> None:
    """The question stays approved without audio; approving it again retries."""


handler = Handler(run=run, on_give_up=on_give_up)
