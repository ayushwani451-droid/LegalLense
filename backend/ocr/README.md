# LegalLense OCR Layer (PaddleOCR)

Text-extraction only. Given a product/label image, this module returns
recognized text, per-line confidence, and bounding boxes as structured JSON.
It makes **no compliance decisions** - that is the job of a future
deterministic Legal Metrology rules engine that will sit downstream of this
output.

```
Product Image -> optional OpenCV preprocessing -> PaddleOCR -> OCR JSON
```

This module is plain Python with no HTTP/FastAPI dependency, so it can be
imported directly by a FastAPI route later without modification:

```python
from backend.ocr.paddle_ocr_service import PaddleOCRService

service = PaddleOCRService()          # loads PaddleOCR models once, reused after
result = service.run("label.jpg")     # -> OCRResult (pydantic model)
result.model_dump()                   # -> plain dict, ready for JSONResponse
```

## Files

| File | Purpose |
|---|---|
| `schemas.py` | `Detection`, `OCRResult`, `OCRError` pydantic models - the stable output contract |
| `preprocessing.py` | Optional, individually-toggleable OpenCV steps (resize/grayscale/contrast/denoise) |
| `paddle_ocr_service.py` | `PaddleOCRService` - validates input, runs PaddleOCR, returns `OCRResult` |
| `test_ocr.py` | CLI to run OCR on one image and save the JSON result |

## Install

From `stitch_legallense_compliance_portal/` (this project's root):

```bash
uv venv --python 3.11 backend/.venv
uv pip install --python backend/.venv paddlepaddle paddleocr opencv-python-headless setuptools
```

(`setuptools` is required at runtime by `paddle.utils.cpp_extension` even
though nothing here compiles a C++ extension - paddle imports it unconditionally.)

Or with plain pip inside an activated venv:

```bash
python -m venv backend/.venv
backend\.venv\Scripts\activate
pip install -r backend/requirements.txt
```

`backend/requirements.txt` is a full `pip freeze` of a known-working
environment (CPU-only, Python 3.11.16). The packages installed intentionally
were just `paddlepaddle`, `paddleocr`, `opencv-python-headless`, and
`setuptools` - everything else in that file is a transitive dependency
pulled in automatically.

## Run

```bash
backend\.venv\Scripts\python.exe -m backend.ocr.test_ocr backend/test_images/synthetic_label.png
backend\.venv\Scripts\python.exe -m backend.ocr.test_ocr path\to\your\photo.jpg --preprocess
```

This prints every detection (text + confidence + bbox) and writes
`backend/ocr_output/<image_name>_ocr.json`.

## Known CPU issue on this machine (and the fix already applied)

With `paddlepaddle==3.3.1` + the default PP-OCRv6 models, running on this
CPU's oneDNN backend crashes with:

```
NotImplementedError: (Unimplemented) ConvertPirAttribute2RuntimeAttribute
not support [pir::ArrayAttribute<pir::DoubleAttribute>]
```

This is a real PaddlePaddle/PaddleX oneDNN-PIR-executor incompatibility, not
an OCR logic bug. The fix that was verified to work is disabling MKL-DNN:

```python
PaddleOCR(..., enable_mkldnn=False)
```

`PaddleOCRService` sets `enable_mkldnn=False` by default for exactly this
reason (`DEFAULT_ENABLE_MKLDNN` in `paddle_ocr_service.py`). It costs some
CPU inference speed versus MKL-DNN acceleration. If you upgrade
`paddlepaddle` later and this is fixed upstream, flip the default back to
`True` and re-test - `PaddleOCRService(enable_mkldnn=True)` is fully
supported as an override in the meantime.

## Test image

`backend/test_images/synthetic_label.png` is a **synthetically generated**
label (drawn with PIL: product name, manufacturer, net quantity, MRP, mfg
date, consumer care, country of origin) used to validate the pipeline
end-to-end without a real product photo on hand. It is not a photograph and
should not be treated as a real-world OCR accuracy benchmark - it exists to
prove text detection, confidence scoring, bounding boxes, and JSON
serialization all work correctly. **Run a real photographed label through
`test_ocr.py` before trusting this for actual product images** - real
photos introduce glare, blur, skew, and curved surfaces that a rendered
image does not.

## Preprocessing

Off by default. Enable selectively:

```python
from backend.ocr.preprocessing import PreprocessOptions

opts = PreprocessOptions(resize=True, denoise=True, enhance_contrast=True, grayscale=False)
result = service.run("photo.jpg", preprocess_options=opts)
```

- `resize`: downscale only if the longer side exceeds `max_side` (default 2000px) - keeps inference fast on huge phone photos, never upscales.
- `denoise`: `cv2.fastNlMeansDenoisingColored` - helps grainy/low-light shots.
- `enhance_contrast`: CLAHE on the L channel (LAB color space) - helps faint/low-contrast print.
- `grayscale`: converts to grayscale (kept 3-channel since PaddleOCR expects color input).

## Output shape

```json
{
  "success": true,
  "image": "product.jpg",
  "image_path": "backend/test_images/product.jpg",
  "full_text": "HERBAL GLOW HANDWASH\nNet Quantity: 250 ml\n...",
  "detections": [
    {
      "text": "Net Quantity: 250 ml",
      "confidence": 0.9998,
      "bbox": [38, 218, 300, 245],
      "polygon": [[38, 218], [300, 218], [300, 245], [38, 245]]
    }
  ],
  "detection_count": 10,
  "preprocessing_applied": false,
  "error": null
}
```

`bbox` is the axis-aligned rectangle `[x1, y1, x2, y2]`; `polygon` preserves
PaddleOCR's original (usually 4-point) detection quadrilateral so no spatial
information from rotated/skewed text is lost. Both are int pixel
coordinates in the (possibly preprocessed/resized) image that was actually
fed to the model.

## Error handling

`PaddleOCRService.run()` never raises for expected failure modes - it always
returns an `OCRResult`, with `success=False` and a populated `error` for:

- `file_not_found` - missing path
- `unsupported_file_type` - extension not in `.jpg/.jpeg/.png/.webp`
- `unreadable_image` - empty file or corrupt/undecodable image data
- `preprocessing_failed` - an OpenCV step raised
- `engine_init_failed` - PaddleOCR/PaddlePaddle failed to construct
- `inference_failed` - `.predict()` raised
- `empty_result` - OCR ran successfully but found no text (`success=True`, since this isn't an error condition, just an empty label/photo)

## Explicitly out of scope for this module

- Legal Metrology rule evaluation / compliance verdicts
- Qwen3-VL structured field extraction
- Any FastAPI routes (this is framework-independent by design; wire it into
  a route later with `PaddleOCRService().run(...)` and
  `result.model_dump()`)
