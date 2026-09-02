"""
HTTP layer for OCR. This module owns request validation, temp-file handling,
and response shaping ONLY - all actual OCR inference is delegated to the
existing, already-tested PaddleOCRService. No OCR/PaddleOCR logic is
duplicated here.
"""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

from backend.ocr.paddle_ocr_service import OCRInitializationError, PaddleOCRService
from backend.ocr.schemas import OCRError, OCRResult

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
_ENABLE_MKLDNN = os.environ.get("OCR_ENABLE_MKLDNN", "false").strip().lower() in ("1", "true", "yes")

# One shared service instance: PaddleOCRService caches its underlying
# PaddleOCR engine internally, so the (slow) model load happens once per
# process, not once per request. get_service() lets other modules (e.g.
# the startup warm-up hook in main.py) reuse the exact same instance
# without duplicating construction logic.
_service = PaddleOCRService(enable_mkldnn=_ENABLE_MKLDNN)


def get_service() -> PaddleOCRService:
    return _service


ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# Uploads are written into the OS temp dir under their own subfolder so we
# never write into (or serve from) the project tree, and never trust a
# client-supplied path.
_UPLOAD_TMP_DIR = Path(tempfile.gettempdir()) / "legallense_ocr_uploads"
_UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)


def _error_response(status_code: int, image: str, code: str, message: str) -> JSONResponse:
    result = OCRResult(success=False, image=image, error=OCRError(code=code, message=message))
    return JSONResponse(status_code=status_code, content=result.model_dump())


@router.post("/api/ocr", response_model=OCRResult)
async def run_ocr(file: Optional[UploadFile] = File(None)):
    """Accept one image (JPG/JPEG/PNG/WEBP), run it through PaddleOCRService, return OCRResult JSON."""

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

    try:
        tmp_path.write_bytes(contents)
        logger.info("Saved upload '%s' (%d bytes) to temp file for OCR", original_name, len(contents))

        result = _service.run(str(tmp_path))
        # Swap the temp filename back out for the original, client-facing name,
        # and scrub the server-side temp path out of any error message so no
        # internal filesystem detail reaches the browser.
        result.image = original_name
        result.image_path = None
        if result.error is not None:
            result.error.message = result.error.message.replace(str(tmp_path), original_name)

        status_code = 200 if result.success else 422
        logger.info(
            "OCR request for '%s' complete: success=%s detections=%d",
            original_name, result.success, result.detection_count,
        )
        return JSONResponse(status_code=status_code, content=result.model_dump())

    except OCRInitializationError as exc:
        logger.exception("PaddleOCR engine failed to initialize")
        return _error_response(503, original_name, "engine_init_failed", "OCR engine failed to initialize")

    except Exception:
        logger.exception("Unexpected error while processing OCR request for %s", original_name)
        return _error_response(500, original_name, "internal_error", "Unexpected server error during OCR processing")

    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            logger.warning("Failed to clean up temp file %s", tmp_path, exc_info=True)
