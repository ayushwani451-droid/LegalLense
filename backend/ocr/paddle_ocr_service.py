"""
PaddleOCR text-extraction service for LegalLense.

Scope (deliberately narrow): image in -> recognized text, confidence,
bounding boxes out. This module does NOT interpret, validate, or judge
Legal Metrology compliance in any way - that is the job of a future
deterministic rules engine sitting downstream of this service.

Usage (framework-independent, ready for later FastAPI import):

    from backend.ocr.paddle_ocr_service import PaddleOCRService

    service = PaddleOCRService()
    result = service.run("path/to/label.jpg")
    if result.success:
        print(result.full_text)

The PaddleOCR engine is expensive to construct (it loads several models),
so PaddleOCRService lazily builds one engine per (lang, orientation-flag)
combination and reuses it across calls - important once this is called
repeatedly from a FastAPI process instead of a one-shot CLI script.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

from .preprocessing import PreprocessOptions, preprocess_image
from .schemas import Detection, OCRError, OCRResult

logger = logging.getLogger("legallense.ocr.service")

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# PP-OCRv6 (the current default models pulled in by lang="en") crashes on this
# CPU's oneDNN/PIR executor path in paddlepaddle 3.3.1 with:
#   NotImplementedError: (Unimplemented) ConvertPirAttribute2RuntimeAttribute
#   not support [pir::ArrayAttribute<pir::DoubleAttribute>]
# Disabling MKL-DNN sidesteps it entirely (confirmed working end-to-end) at a
# small inference-speed cost on CPU. See backend/ocr/README.md for details.
DEFAULT_ENABLE_MKLDNN = False


class OCRInitializationError(RuntimeError):
    """Raised when the underlying PaddleOCR engine fails to construct."""


class PaddleOCRService:
    """Thin, reusable wrapper around paddleocr.PaddleOCR."""

    _engine_cache: dict = {}

    def __init__(
        self,
        lang: str = "en",
        use_doc_orientation_classify: bool = False,
        use_doc_unwarping: bool = False,
        use_textline_orientation: bool = True,
        enable_mkldnn: bool = DEFAULT_ENABLE_MKLDNN,
        text_rec_score_thresh: Optional[float] = None,
    ) -> None:
        self.lang = lang
        self.use_doc_orientation_classify = use_doc_orientation_classify
        self.use_doc_unwarping = use_doc_unwarping
        self.use_textline_orientation = use_textline_orientation
        self.enable_mkldnn = enable_mkldnn
        self.text_rec_score_thresh = text_rec_score_thresh
        self._engine = None

    # -- engine lifecycle -------------------------------------------------

    def _cache_key(self):
        return (
            self.lang,
            self.use_doc_orientation_classify,
            self.use_doc_unwarping,
            self.use_textline_orientation,
            self.enable_mkldnn,
        )

    def _get_engine(self):
        if self._engine is not None:
            return self._engine

        key = self._cache_key()
        cached = PaddleOCRService._engine_cache.get(key)
        if cached is not None:
            self._engine = cached
            return self._engine

        try:
            from paddleocr import PaddleOCR  # imported lazily: heavy import
        except Exception as exc:  # pragma: no cover - environment issue
            raise OCRInitializationError(f"Failed to import paddleocr: {exc}") from exc

        logger.info(
            "Initializing PaddleOCR engine (lang=%s, mkldnn=%s, textline_orientation=%s)",
            self.lang, self.enable_mkldnn, self.use_textline_orientation,
        )
        try:
            engine = PaddleOCR(
                lang=self.lang,
                use_doc_orientation_classify=self.use_doc_orientation_classify,
                use_doc_unwarping=self.use_doc_unwarping,
                use_textline_orientation=self.use_textline_orientation,
                enable_mkldnn=self.enable_mkldnn,
            )
        except Exception as exc:
            logger.exception("PaddleOCR engine initialization failed")
            raise OCRInitializationError(f"PaddleOCR failed to initialize: {exc}") from exc

        PaddleOCRService._engine_cache[key] = engine
        self._engine = engine
        return engine

    # -- validation ---------------------------------------------------------

    @staticmethod
    def validate_image_path(image_path: str) -> Path:
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")
        if not path.is_file():
            raise FileNotFoundError(f"Path is not a file: {image_path}")
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file type '{path.suffix}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}"
            )
        return path

    @staticmethod
    def load_image(path: Path) -> np.ndarray:
        """Decode the image, raising a clear error on corrupt/unreadable files."""
        import cv2

        # cv2.imread never raises on a bad file - it silently returns None -
        # so an explicit check is required to avoid a confusing downstream crash.
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size == 0:
            raise ValueError(f"Image file is empty: {path}")
        image = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Could not decode image (corrupted or unreadable): {path}")
        return image

    # -- inference ------------------------------------------------------

    def run(
        self,
        image_path: str,
        preprocess_options: Optional[PreprocessOptions] = None,
    ) -> OCRResult:
        """Run OCR on a single image file and return a stable OCRResult.

        Never raises for expected failure modes (missing file, bad format,
        corrupt image, engine/inference errors) - those come back as
        `OCRResult(success=False, error=...)` so callers (including a future
        FastAPI endpoint) can handle them uniformly instead of catching
        exceptions.
        """
        image_name = Path(image_path).name

        try:
            path = self.validate_image_path(image_path)
        except (FileNotFoundError, ValueError) as exc:
            logger.warning("Input validation failed for %s: %s", image_path, exc)
            code = "file_not_found" if isinstance(exc, FileNotFoundError) else "unsupported_file_type"
            return OCRResult(
                success=False, image=image_name, image_path=str(image_path),
                error=OCRError(code=code, message=str(exc)),
            )

        logger.info("Received image: %s", path)

        try:
            image = self.load_image(path)
        except ValueError as exc:
            logger.warning("Failed to decode image %s: %s", path, exc)
            return OCRResult(
                success=False, image=image_name, image_path=str(path),
                error=OCRError(code="unreadable_image", message=str(exc)),
            )

        preprocessing_applied = False
        if preprocess_options is not None:
            try:
                image = preprocess_image(image, preprocess_options)
                preprocessing_applied = True
            except Exception as exc:
                logger.exception("Preprocessing failed for %s", path)
                return OCRResult(
                    success=False, image=image_name, image_path=str(path),
                    error=OCRError(code="preprocessing_failed", message=str(exc)),
                )

        try:
            engine = self._get_engine()
        except OCRInitializationError as exc:
            return OCRResult(
                success=False, image=image_name, image_path=str(path),
                error=OCRError(code="engine_init_failed", message=str(exc)),
            )

        logger.info("Running PaddleOCR inference on %s", path)
        try:
            predict_kwargs = {}
            if self.text_rec_score_thresh is not None:
                predict_kwargs["text_rec_score_thresh"] = self.text_rec_score_thresh
            raw_results = engine.predict(image, **predict_kwargs)
        except Exception as exc:
            logger.exception("PaddleOCR inference failed for %s", path)
            return OCRResult(
                success=False, image=image_name, image_path=str(path),
                preprocessing_applied=preprocessing_applied,
                error=OCRError(code="inference_failed", message=str(exc)),
            )

        detections = self._parse_result(raw_results)
        logger.info("OCR produced %d detection(s) for %s", len(detections), path)

        if not detections:
            return OCRResult(
                success=True, image=image_name, image_path=str(path),
                full_text="", detections=[], detection_count=0,
                preprocessing_applied=preprocessing_applied,
                error=OCRError(code="empty_result", message="No text detected in image"),
            )

        full_text = "\n".join(d.text for d in detections)
        return OCRResult(
            success=True, image=image_name, image_path=str(path),
            full_text=full_text, detections=detections, detection_count=len(detections),
            preprocessing_applied=preprocessing_applied,
        )

    @staticmethod
    def _parse_result(raw_results) -> list:
        """Convert PaddleOCR's internal OCRResult page objects into our Detection schema."""
        detections: list = []
        if not raw_results:
            return detections

        for page in raw_results:
            texts = page.get("rec_texts") or []
            scores = page.get("rec_scores") or []
            boxes = page.get("rec_boxes")
            polys = page.get("rec_polys")

            for i, text in enumerate(texts):
                if not text or not text.strip():
                    continue
                confidence = float(scores[i]) if i < len(scores) else 0.0

                if boxes is not None and i < len(boxes):
                    bbox = [int(v) for v in boxes[i]]
                else:
                    bbox = [0, 0, 0, 0]

                if polys is not None and i < len(polys):
                    polygon = [[int(x), int(y)] for x, y in polys[i]]
                else:
                    polygon = [
                        [bbox[0], bbox[1]], [bbox[2], bbox[1]],
                        [bbox[2], bbox[3]], [bbox[0], bbox[3]],
                    ]

                detections.append(Detection(
                    text=text.strip(), confidence=confidence, bbox=bbox, polygon=polygon,
                ))

        return detections
