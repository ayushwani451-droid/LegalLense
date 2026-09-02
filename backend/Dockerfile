# LegalLense backend - FastAPI + OpenCV + PaddleOCR, for Railway.
#
# Docker was chosen over Railway's auto-detected Python/Nixpacks builder
# because PaddlePaddle/OpenCV dynamically link system libraries (libgomp,
# and OpenCV's image-codec libs) that a bare Python buildpack does not
# reliably install - this is a documented, common failure mode for these
# packages (missing libgomp.so.1 at `import paddle` time), not a
# hypothetical concern. A Dockerfile makes those system packages explicit
# and reproducible.
#
# Build/run this as Railway's Root Directory = backend (so this Dockerfile,
# requirements.txt, main.py etc. are all at the build context root).

FROM python:3.11-slim

# System libraries actually needed by paddlepaddle / opencv-python-headless
# on a minimal Debian base (documented PaddlePaddle/PaddleOCR Linux
# dependencies - not guessed):
#   libgomp1          - OpenMP runtime, required by paddlepaddle
#   libglib2.0-0       - required by some OpenCV image codecs
#   libsm6, libxext6, libxrender1 - commonly required transitively by OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first so this layer is cached across builds
# that only change application code, not requirements.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code - the same files already used locally, unchanged.
COPY __init__.py .
COPY main.py .
COPY api ./api
COPY ocr ./ocr

# PaddleOCR/PaddleX cache model weights under the running user's home
# directory on first use (see backend/ocr/README.md) - not baked into this
# image, since Railway's filesystem is ephemeral per deploy anyway and
# bundling ~140MB of model weights into every image build would only slow
# builds down without avoiding the download (a fresh container still needs
# them in memory either way).

EXPOSE 8000

# Shell form so $PORT (set by Railway at runtime) actually expands - the
# exec/JSON-array form would pass the literal string "$PORT" to uvicorn.
CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"
