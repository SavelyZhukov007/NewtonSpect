from __future__ import annotations

import socket
import time
from uuid import uuid4

from app.bootstrap import build_container


def main() -> None:
    container = build_container()
    pipeline = container.build_pipeline()
    worker_id = f"{socket.gethostname()}-{str(uuid4())[:8]}"
    print(f"[worker] started id={worker_id}")

    while True:
        container.repository.requeue_stuck_running_jobs(
            older_than_seconds=container.settings.worker_stuck_timeout_seconds
        )
        job = container.repository.claim_next_job(worker_id=worker_id)
        if job is None:
            time.sleep(container.settings.worker_poll_seconds)
            continue

        print(f"[worker] processing job={job.id}")
        try:
            pipeline.process(job)
            print(f"[worker] completed job={job.id}")
        except Exception as exc:  # noqa: BLE001
            container.repository.update_job_status(
                job.id,
                status="failed",
                progress=job.progress,
                step="failed",
                error_message=str(exc),
            )
            container.repository.add_event(job.id, "failed", str(exc), job.progress, level="error")
            print(f"[worker] failed job={job.id}: {exc}")


if __name__ == "__main__":
    main()

