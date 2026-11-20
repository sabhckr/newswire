"""Collection and scheduling worker: `python -m app.worker`."""

from __future__ import annotations

import logging
import signal
import sys

from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.scheduler.runner import WORKER_ID, build_scheduler
from app.services.bootstrap import bootstrap

log = logging.getLogger("newswire.worker")


def main() -> int:
    configure_logging("worker")
    db = SessionLocal()
    try:
        bootstrap(db)
    finally:
        db.close()
    scheduler = build_scheduler(blocking=True)

    def stop(signum, _frame):
        log.info("Received signal %s, shutting down worker", signum)
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("Samyabadi worker %s started", WORKER_ID)
    scheduler.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
