"""
HTTP layer for OCR. This module owns request validation, temp-file handling,
job bookkeeping, and response shaping ONLY - all actual OCR inference is
delegated to the existing, already-tested PaddleOCRService. No OCR/PaddleOCR
logic is duplicated or modified here.

Two-step async flow (real inference takes 52-168s+ on this CPU-only setup,
which exceeds Railway's/most reverse-proxies' request timeout if run
synchronously - the original single-request design produced 502s):

    POST /api/ocr                  -> validates + schedules, returns 202 + job_id immediately
    GET  /api/ocr/status/{job_id}  -> poll this until status is "done" or "error"

Inference runs in a separate OS process, not a thread (confirmed via live
testing on the deployed backend: PaddleOCR/PaddleX's native inference does
not release Python's GIL for its full 52-168s duration, so the previous
BackgroundTasks-thread approach still froze the entire event loop -
including this same status-polling endpoint - even though the OCR call
itself was technically "in the background"). The single OCR worker process
is a concurrent.futures.ProcessPoolExecutor(max_workers=1) created once at
app startup and stored on app.state (see backend/main.py) - one worker
because PaddleOCR is memory-heavy and multiple concurrent OCR processes
would compete for RAM. POST /api/ocr submits work to it via
loop.run_in_executor() inside a fire-and-forget asyncio.create_task(), so
the request handler still returns 202 immediately without awaiting OCR.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

try:
    from backend.ocr.paddle_ocr_service import OCRInitializationError, PaddleOCRService
    from backend.ocr.preprocessing import PreprocessOptions
    from backend.ocr.schemas import OCRError, OCRResult
except ImportError:
    # See backend/main.py for why both import styles are supported.
    from ocr.paddle_ocr_service import OCRInitializationError, PaddleOCRService  # type: ignore[no-redef]
    from ocr.preprocessing import PreprocessOptions  # type: ignore[no-redef]
    from ocr.schemas import OCRError, OCRResult  # type: ignore[no-redef]

try:
    from backend.api.job_store import create_job, get_job, set_error, set_result, set_status
except ImportError:
    from api.job_store import create_job, get_job, set_error, set_result, set_status  # type: ignore[no-redef]

logger = logging.getLogger("legallense.api.ocr")

router = APIRouter()

# MAX_UPLOAD_MB is the single source of truth for the upload size limit.
# The frontend's own client-side check is early-UX-feedback only - this is
# the value that is actually enforced, and the one the frontend copy should
# match (see new_compliance_scan_legallense/code.html's "Max 15MB" text).
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "15")) * 1024 * 1024

# Kept configurable (default: disabled) because MKL-DNN was found to crash
# PaddleOCR's PIR executor on the Windows CPU this was developed/tested on
# (see backend/ocr/README.md). A Linux host may not hit the same bug and
# could re-enable it for a speed gain via OCR_ENABLE_MKLDNN=true, but the
# safe, verified-working default is preserved here rather than assumed.
#
# This module is re-imported fresh inside the ProcessPoolExecutor's worker
# process (see _get_worker_service() below), so this env lookup happens
# independently in both the main process and the worker process - there is
# no need to pass this value across the process boundary as an argument.
_ENABLE_MKLDNN = os.environ.get("OCR_ENABLE_MKLDNN", "false").strip().lower() in ("1", "true", "yes")

# Long-side downscale threshold applied to every real upload before
# inference (see _run_ocr_worker below) - deliberately NOT
# backend/ocr/preprocessing.py's own MAX_SIDE_DEFAULT (2000), which is too
# permissive for this app's actual memory budget: a real 1536x1024 test
# photo (backend/test_images/legal_label_test.png, comfortably under
# 2000px) was measured pushing a single worker process to ~571MB peak RSS
# during inference alone - on a memory-constrained host, that is enough to
# get the worker OOM-killed outright (surfaces as
# concurrent.futures.process.BrokenProcessPool). Downscaling that same
# image to max_side=1024 cut peak memory to ~55MB above baseline (a ~9.4x
# reduction) for a loss of exactly 1 of 60 detected text regions.
# Configurable via env var so it can be tuned from Railway without a code
# change if the container's memory limit changes.
_OCR_MAX_IMAGE_SIDE = int(os.environ.get("OCR_MAX_IMAGE_SIDE", "1024"))

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# Uploads are written into the OS temp dir under their own subfolder so we
# never write into (or serve from) the project tree, and never trust a
# client-supplied path.
_UPLOAD_TMP_DIR = Path(tempfile.gettempdir()) / "legallense_ocr_uploads"
_UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)


# --- response models (also drive the /docs OpenAPI schema) ----------------

class JobAcceptedResponse(BaseModel):
    job_id: str
    status: Literal["pending"] = "pending"


class JobStatusResponse(BaseModel):
    status: Literal["pending", "processing", "done", "error"]
    result: Optional[OCRResult] = None
    error: Optional[str] = None


def _error_response(status_code: int, image: str, code: str, message: str) -> JSONResponse:
    result = OCRResult(success=False, image=image, error=OCRError(code=code, message=message))
    return JSONResponse(status_code=status_code, content=result.model_dump())


# --- OCR worker-process code -------------------------------------------
# Everything in this section runs inside the ProcessPoolExecutor's worker
# process, not the main FastAPI process. It must stay self-contained:
# concurrent.futures pickles the submitted callable (by module + name) and
# its arguments/return value to move them across the process boundary, so
# only plain, picklable data can cross - no live PaddleOCRService/engine,
# no UploadFile, no FastAPI app/request object.

_worker_service: Optional[PaddleOCRService] = None


def _get_worker_service() -> PaddleOCRService:
    """Lazily build (and cache) this worker process's own PaddleOCRService.

    A PaddleOCRService that has already built its `_engine` wraps a live
    PaddleOCR/PaddleX C++ engine object, which is not picklable - so the
    main process's existing service instance can never be sent across the
    process boundary. Each worker process must instead construct its own.
    Because backend/main.py creates the executor with max_workers=1, the
    same single worker process (and therefore this same module-level
    cache) handles every OCR job for the life of the app, so the
    expensive model load still happens only once - just once per worker
    *process* instead of once per app.
    """
    global _worker_service
    if _worker_service is None:
        _worker_service = PaddleOCRService(enable_mkldnn=_ENABLE_MKLDNN)
    return _worker_service


def init_worker_engine() -> None:
    """Submitted once to the ProcessPoolExecutor at app startup (see the
    lifespan handler in backend/main.py) so the worker process's PaddleOCR
    engine is loaded before the first real request needs it, not during
    it - the same reasoning as the old single-process startup warm-up,
    just aimed at the process that now actually does the inference.
    """
    _get_worker_service()._get_engine()


def _run_ocr_worker(tmp_path_str: str, original_name: str) -> dict:
    """The actual OCR job, run inside the worker process.

    Takes only plain str arguments and returns a plain dict (never the
    live PaddleOCRService, nor the pydantic OCRResult object) so the
    result can be pickled back to the main process. PaddleOCRService.run()
    already never raises for expected failure modes (bad/missing/corrupt
    image, engine init failure, inference failure) - those all come back
    as a well-formed OCRResult(success=False, error=...), which is
    forwarded here as-is. Only a genuinely unexpected exception would
    propagate out of this function, to be pickled and re-raised in the
    main process by concurrent.futures (handled in _run_ocr_job below) -
    this includes the worker process itself dying (e.g. OOM-killed), which
    surfaces to the main process as concurrent.futures.process.BrokenProcessPool
    rather than as a return value from this function at all.

    preprocess_options requests a downscale (see backend/ocr/preprocessing.py's
    resize_if_large(), a no-op for images already at or under the
    threshold) before inference - large real-world photos (multi-MB,
    several-thousand-pixels-per-side) were found to OOM-kill this worker
    process; PaddleOCR's memory use scales with input pixel count, and
    label/document text stays perfectly readable well below this
    threshold, so this is a large memory-safety win for negligible
    accuracy cost.
    """
    result = _get_worker_service().run(
        tmp_path_str, preprocess_options=PreprocessOptions(resize=True, max_side=_OCR_MAX_IMAGE_SIDE)
    )
    # Swap the temp filename back out for the original, client-facing name,
    # and scrub the server-side temp path out of any error message so no
    # internal filesystem detail reaches the browser.
    result.image = original_name
    result.image_path = None
    if result.error is not None:
        result.error.message = result.error.message.replace(tmp_path_str, original_name)
    return result.model_dump()


# --- main-process orchestration -----------------------------------------

async def _run_ocr_job(job_id: str, tmp_path: Path, original_name: str, app: FastAPI) -> None:
    """Runs as a fire-and-forget asyncio Task on the main process's event
    loop (scheduled via asyncio.create_task in run_ocr() below - never
    awaited there, so POST /api/ocr still returns immediately).

    Takes the FastAPI `app` (rather than the executor directly) so it
    always submits to whichever ProcessPoolExecutor is CURRENTLY live on
    app.state.ocr_process_pool at the moment of submission - important
    because that pool can be swapped out mid-flight by
    recreate_ocr_process_pool() (backend/main.py) after a crash.

    Awaiting a ProcessPoolExecutor future yields control back to the event
    loop for the entire 52-168s the other process is working - unlike the
    previous BackgroundTasks-thread version, nothing here holds the GIL,
    so concurrent requests (including GET /api/ocr/status/{job_id}) keep
    getting served normally the whole time.

    Never lets an exception from the worker process propagate silently -
    always records either a "done" result or an "error" status for the
    job."""
    set_status(job_id, "processing")
    loop = asyncio.get_running_loop()
    executor: ProcessPoolExecutor = app.state.ocr_process_pool
    try:
        result_dict = await loop.run_in_executor(executor, _run_ocr_worker, str(tmp_path), original_name)

        logger.info(
            "OCR job %s for '%s' complete: success=%s detections=%s",
            job_id, original_name, result_dict.get("success"), result_dict.get("detection_count"),
        )
        # A well-formed OCRResult(success=False) - e.g. an unreadable image -
        # is still a normal completed job, not a job-level error.
        set_result(job_id, result_dict)

    except OCRInitializationError:
        logger.exception("PaddleOCR engine failed to initialize for job %s", job_id)
        set_error(job_id, "OCR engine failed to initialize")

    except BrokenProcessPool:
        # The worker process died mid-inference - most likely OOM-killed by
        # the host on a large/complex image. A ProcessPoolExecutor never
        # recovers from this on its own (every future already
        # queued/running on it fails the same way, and every future
        # submitted to it afterwards fails immediately too), so this job's
        # own retry is not attempted here - only a fresh pool for the NEXT
        # job. Full exception logged server-side; the client only gets a
        # clear, generic message, no internals.
        logger.exception(
            "OCR worker process crashed (likely OOM) while processing job %s for '%s' - replacing the worker pool",
            job_id, original_name,
        )
        set_error(job_id, "OCR worker crashed, possibly due to memory limits - please try again")
        try:
            await app.state.ocr_pool_recreate(app, executor)
        except Exception:
            logger.exception("Failed to recreate the OCR process pool after a crash")

    except Exception:
        logger.exception("Unexpected error while processing OCR job %s for %s", job_id, original_name)
        set_error(job_id, "Unexpected server error during OCR processing")

    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            logger.warning("Failed to clean up temp file %s", tmp_path, exc_info=True)


# --- routes -----------------------------------------------------------

@router.post("/api/ocr", response_model=JobAcceptedResponse, status_code=202)
async def run_ocr(request: Request, file: Optional[UploadFile] = File(None)):
    """Validate the upload and schedule PaddleOCR to run in the OCR worker
    process. Returns immediately with a job_id - poll GET
    /api/ocr/status/{job_id} for the result. Validation failures
    (bad/missing/oversized file) are still reported synchronously here,
    before any job is created."""

    if file is None or not file.filename:
        logger.warning("OCR request received with no file attached")
        return _error_response(400, "", "missing_file", "No file was uploaded")

    # Path(...).name strips any directory component the client might send
    # (e.g. "../../etc/passwd.jpg") - only the base filename is ever used,
    # and only for display; it is never used to build a filesystem path.
    original_name = Path(file.filename).name
    suffix = Path(original_name).suffix.lower()

    if suffix not in ALLOWED_EXTENSIONS:
        logger.warning("Rejected unsupported file type '%s' for %s", suffix, original_name)
        return _error_response(
            400, original_name, "unsupported_file_type",
            f"Unsupported file type '{suffix}'. Supported: {sorted(ALLOWED_EXTENSIONS)}",
        )

    contents = await file.read()
    if not contents:
        logger.warning("Rejected empty upload for %s", original_name)
        return _error_response(400, original_name, "empty_upload", "Uploaded file is empty")

    if len(contents) > MAX_UPLOAD_BYTES:
        logger.warning("Rejected oversized upload for %s (%d bytes)", original_name, len(contents))
        return _error_response(
            400, original_name, "file_too_large",
            f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit",
        )

    # Random server-generated filename - never derived from client input.
    tmp_path = _UPLOAD_TMP_DIR / f"{uuid.uuid4().hex}{suffix}"
    tmp_path.write_bytes(contents)
    logger.info("Saved upload '%s' (%d bytes) to temp file for OCR", original_name, len(contents))

    job_id = create_job()
    asyncio.create_task(_run_ocr_job(job_id, tmp_path, original_name, request.app))
    logger.info("Scheduled OCR job %s for '%s'", job_id, original_name)

    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "pending"})


@router.get("/api/ocr/status/{job_id}", response_model=JobStatusResponse)
async def get_ocr_status(job_id: str):
    """Poll this for the result of a job created by POST /api/ocr."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] == "done":
        return JobStatusResponse(status="done", result=job["result"])
    if job["status"] == "error":
        return JobStatusResponse(status="error", error=job["error"])
    return JobStatusResponse(status=job["status"])
