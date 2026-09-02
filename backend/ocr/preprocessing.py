"""
Optional OpenCV preprocessing for label images before OCR.

Every step here is opt-in and independently toggleable. Nothing is applied
"blindly" by default - callers ask for exactly what they want via
PreprocessOptions. The goal is more reliable text detection on noisy phone
photos, not aggressive image manipulation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger("legallense.ocr.preprocessing")

MAX_SIDE_DEFAULT = 2000


@dataclass
class PreprocessOptions:
    resize: bool = False
    max_side: int = MAX_SIDE_DEFAULT
    grayscale: bool = False
    enhance_contrast: bool = False
    denoise: bool = False


def resize_if_large(image: np.ndarray, max_side: int = MAX_SIDE_DEFAULT) -> np.ndarray:
    """Downscale so the longer side is at most `max_side`. Upscaling is not done here."""
    h, w = image.shape[:2]
    longest = max(h, w)
    if longest <= max_side:
        return image
    scale = max_side / float(longest)
    new_size = (int(w * scale), int(h * scale))
    logger.debug("Resizing image from %sx%s to %s", w, h, new_size)
    return cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)


def to_grayscale_3ch(image: np.ndarray) -> np.ndarray:
    """Convert to grayscale but keep 3 channels, since PaddleOCR expects a color image."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def enhance_contrast(image: np.ndarray) -> np.ndarray:
    """CLAHE contrast enhancement on the luminance channel only, to help faint/low-contrast labels."""
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)
    merged = cv2.merge((l_channel, a_channel, b_channel))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def denoise(image: np.ndarray) -> np.ndarray:
    """Light denoising for grainy phone-camera shots."""
    return cv2.fastNlMeansDenoisingColored(image, None, h=7, hColor=7, templateWindowSize=7, searchWindowSize=21)


def preprocess_image(image: np.ndarray, options: PreprocessOptions) -> np.ndarray:
    """Apply the requested steps, in a fixed, sensible order."""
    result = image
    if options.resize:
        result = resize_if_large(result, options.max_side)
    if options.denoise:
        result = denoise(result)
    if options.enhance_contrast:
        result = enhance_contrast(result)
    if options.grayscale:
        result = to_grayscale_3ch(result)
    return result
