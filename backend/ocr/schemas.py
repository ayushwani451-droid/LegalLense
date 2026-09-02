"""
Data contracts for the OCR layer.

These models describe TEXT EXTRACTION output only. Nothing in this module
makes, implies, or stores a compliance decision (compliant / non-compliant /
violation). That judgment belongs to the future deterministic Legal
Metrology rules engine, not to OCR.

Kept as pydantic models (already a transitive dependency of paddleocr) so
they can be reused as-is for FastAPI request/response models later without
rewriting the schema.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Detection(BaseModel):
    """A single recognized text line."""

    text: str = Field(..., description="Recognized text content")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Recognition confidence score")
    bbox: List[int] = Field(
        ..., min_length=4, max_length=4,
        description="Axis-aligned bounding box [x1, y1, x2, y2] in pixel coordinates",
    )
    polygon: List[List[int]] = Field(
        ..., description="Original detection polygon (typically 4 points: TL, TR, BR, BL), preserved as-is from PaddleOCR",
    )


class OCRError(BaseModel):
    """Structured error payload used when OCR could not be completed."""

    code: str = Field(..., description="Machine-readable error code, e.g. 'file_not_found'")
    message: str = Field(..., description="Human-readable explanation")


class OCRResult(BaseModel):
    """Stable output contract for one image passed through the OCR layer."""

    success: bool
    image: str = Field(..., description="Filename (basename) of the processed image")
    image_path: Optional[str] = Field(None, description="Full input path as given to the service")
    full_text: str = Field("", description="All detected text lines joined with newlines")
    detections: List[Detection] = Field(default_factory=list)
    detection_count: int = Field(0, description="Number of text detections returned")
    preprocessing_applied: bool = Field(False, description="Whether OpenCV preprocessing ran before OCR")
    error: Optional[OCRError] = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "success": True,
                "image": "product.jpg",
                "image_path": "test_images/product.jpg",
                "full_text": "MRP RS 999\nNET QTY 500G",
                "detections": [
                    {
                        "text": "MRP RS 999",
                        "confidence": 0.97,
                        "bbox": [10, 20, 120, 45],
                        "polygon": [[10, 20], [120, 20], [120, 45], [10, 45]],
                    }
                ],
                "detection_count": 1,
                "preprocessing_applied": False,
                "error": None,
            }
        }
    }
