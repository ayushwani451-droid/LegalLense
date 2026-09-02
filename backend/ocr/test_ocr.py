"""
CLI test/validation script for the PaddleOCR service.

Run a real image through real PaddleOCR, print what was detected, and save
the structured JSON result. This is a smoke test / manual validation tool,
not a compliance checker - it only proves the OCR layer works end to end.

Usage (from the `stitch_legallense_compliance_portal` directory, with the
backend virtualenv active):

    python -m backend.ocr.test_ocr path/to/product.jpg
    python -m backend.ocr.test_ocr path/to/product.jpg --preprocess
    python -m backend.ocr.test_ocr path/to/product.jpg --out backend/ocr_output/product_ocr.json

Or directly:

    backend\\.venv\\Scripts\\python.exe backend\\ocr\\test_ocr.py path\\to\\product.jpg
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    # Allow `python backend/ocr/test_ocr.py ...` in addition to `-m` usage.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from backend.ocr.paddle_ocr_service import PaddleOCRService
    from backend.ocr.preprocessing import PreprocessOptions
else:
    from .paddle_ocr_service import PaddleOCRService
    from .preprocessing import PreprocessOptions

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "ocr_output"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run PaddleOCR on a single product label image.")
    parser.add_argument("image", help="Path to a JPG/JPEG/PNG/WEBP product label image")
    parser.add_argument("--out", default=None, help="Path to write the JSON result (default: backend/ocr_output/<name>_ocr.json)")
    parser.add_argument("--lang", default="en", help="PaddleOCR language model to use (default: en)")
    parser.add_argument("--preprocess", action="store_true", help="Enable OpenCV preprocessing (resize + denoise + contrast)")
    parser.add_argument("--grayscale", action="store_true", help="Also convert to grayscale during preprocessing")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("legallense.ocr.test_ocr")

    preprocess_options = None
    if args.preprocess:
        preprocess_options = PreprocessOptions(
            resize=True, denoise=True, enhance_contrast=True, grayscale=args.grayscale,
        )

    service = PaddleOCRService(lang=args.lang)
    result = service.run(args.image, preprocess_options=preprocess_options)

    print("\n" + "=" * 60)
    print(f"Image:           {result.image}")
    print(f"Success:         {result.success}")
    print(f"Preprocessing:   {result.preprocessing_applied}")
    if result.error:
        print(f"Error:           [{result.error.code}] {result.error.message}")
    print(f"Detections:      {result.detection_count}")
    print("=" * 60)

    if result.detections:
        print("\n-- Detected text (with confidence + bbox) --")
        for i, det in enumerate(result.detections, start=1):
            print(f"[{i:02d}] conf={det.confidence:.4f}  bbox={det.bbox}  text={det.text!r}")

        print("\n-- Full extracted text --")
        print(result.full_text)

    out_path = Path(args.out) if args.out else DEFAULT_OUTPUT_DIR / f"{Path(args.image).stem}_ocr.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nJSON result written to: {out_path}")

    if not result.success and result.error and result.error.code != "empty_result":
        logger.error("OCR run failed: %s", result.error.message)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
