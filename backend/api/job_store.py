"""
Minimal in-memory job store for the async OCR flow.

Deliberately simple, per the actual need right now: a dict guarded by a
plain lock. Known, accepted limitations (not solved here):
  - Does not persist across process restarts/redeploys.
  - Does not work across multiple worker processes - if this app is ever
    run with more than one Uvicorn/Gunicorn worker, a job created on one
    worker is invisible to a status poll that lands on another. A real
    shared store (Redis, a DB row) would be needed at that point.
Both are fine for a single-process deployment, which is what this backend
currently is.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

JobStatus = Literal["pending", "processing", "done", "error"]

_jobs: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def create_job() -> str:
    """Create a new job in "pending" state and return its id."""
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {
            "status": "pending",
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    return job_id


def set_status(job_id: str, status: JobStatus) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = status


def set_result(job_id: str, result: dict) -> None:
    """Mark a job done with its (already-serializable) OCRResult payload."""
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["result"] = result


def set_error(job_id: str, message: str) -> None:
    """Mark a job failed - reserved for genuine exceptions from the OCR
    service, not for a well-formed OCRResult(success=False) (e.g. an
    unreadable image), which is still a normal "done" outcome."""
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = message


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job is not None else None
