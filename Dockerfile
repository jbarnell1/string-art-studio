# String Art Studio. One small image: FastAPI + numpy/OpenCV engines + static PWA.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SAS_DATA_DIR=/data \
    SAS_HOST=0.0.0.0 \
    SAS_PORT=8050

# libgomp is needed by opencv-python-headless; libheif codecs come bundled with pillow-heif wheels.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 studio

WORKDIR /app
COPY pyproject.toml README.md ./
COPY stringart ./stringart
RUN pip install . \
    && mkdir -p /data && chown -R studio:studio /data /app

USER studio
VOLUME ["/data"]
EXPOSE 8050
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8050/healthz || exit 1

CMD ["string-art-studio", "serve"]
