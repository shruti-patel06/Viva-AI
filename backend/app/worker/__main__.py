"""The worker process. Run from backend/:

    python -m app.worker          keep polling for jobs until stopped (Ctrl+C)
    python -m app.worker --once   process every due job, then exit
"""

import argparse
import logging
import time

from app.core.db import close_pool, transaction
from app.jobs import queue
from app.worker.runner import Handler, run_one

POLL_SECONDS = 2

log = logging.getLogger("worker")


def handlers() -> dict[str, Handler]:
    from app.modules.ingestion import ingest_job

    return {"ingest": ingest_job.handler}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    parser.add_argument("--once", action="store_true", help="exit when no job is due")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    registered = handlers()
    log.info("worker started for job types: %s", ", ".join(registered) or "(none)")
    try:
        while True:
            with transaction() as conn:
                if count := queue.requeue_stale(conn):
                    log.warning("put %s abandoned job(s) back in the queue", count)
            if not run_one(registered):
                if args.once:
                    break
                time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        # A job cut off here stays "running" and is requeued once it goes stale.
        log.info("worker stopped")
    finally:
        close_pool()


if __name__ == "__main__":
    main()
