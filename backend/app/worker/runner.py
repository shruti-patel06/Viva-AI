"""Running one job: claim it, call its handler, then record done, retry or failed."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.db import transaction
from app.jobs import queue
from app.jobs.queue import MAX_ATTEMPTS, PermanentJobError, RetryLater

log = logging.getLogger("worker")


@dataclass(frozen=True)
class Handler:
    # Does the work. Must be safe to run twice, because a job can be retried or abandoned
    # halfway and picked up again.
    run: Callable[[dict[str, Any]], None]
    # Called once when the job is given up on, to record the failure where users see it
    # (for ingest: mark the document failed with the reason).
    on_give_up: Callable[[dict[str, Any], str], None]


def run_one(handlers: dict[str, Handler]) -> bool:
    """Process one due job. Returns False when there was nothing to do."""
    # Claim in its own short transaction, so the row lock is released straight away and
    # the job shows as "running" while the slow work happens.
    with transaction() as conn:
        job = queue.claim_next(conn, list(handlers))
    if job is None:
        return False

    handler = handlers[job.type]
    log.info("job %s (%s) attempt %s started", job.id, job.type, job.attempts)

    if job.attempts > MAX_ATTEMPTS:
        # Only reachable when the job was abandoned on its last attempt.
        _give_up(handler, job, "gave up after the worker stopped during the last attempt")
        return True

    try:
        handler.run(job.payload)
    except RetryLater as wait:
        log.info("job %s postponed %ss: %s", job.id, int(wait.delay.total_seconds()), wait)
        with transaction() as conn:
            queue.postpone(conn, job, wait.delay, str(wait))
    except PermanentJobError as error:
        _give_up(handler, job, str(error))
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        if job.attempts >= MAX_ATTEMPTS:
            _give_up(handler, job, message)
        else:
            log.warning("job %s attempt %s failed, will retry: %s", job.id, job.attempts, message)
            with transaction() as conn:
                queue.retry_later(conn, job, message)
    else:
        with transaction() as conn:
            queue.complete(conn, job.id)
        log.info("job %s done", job.id)
    return True


def _give_up(handler: Handler, job: queue.Job, reason: str) -> None:
    log.error("job %s failed: %s", job.id, reason)
    with transaction() as conn:
        queue.fail(conn, job.id, reason)
    handler.on_give_up(job.payload, reason)
