# Piper text-to-speech for String Art Studio's build mode, as its own small container.
# Studio talks to it over HTTP on the compose network only (POST /synthesize, GET /voices); every
# clip is cached by Studio under data/voice, so this box does each phrase once.
#
# Piper (piper1-gpl) is GPL-3.0; it runs as a separate process and image, so the MIT licence of
# the studio is untouched. Voice models have their own licences: see the MODEL_CARD next to each
# voice at https://huggingface.co/rhasspy/piper-voices.
FROM python:3.12-slim-bookworm

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PIPER_VOICE=en_US-lessac-medium

RUN pip install "piper-tts[http]"

# Voices baked into the image so the container works offline. Override at build time:
#   docker compose build --build-arg PIPER_VOICES="en_US-lessac-medium en_GB-alba-medium" piper
ARG PIPER_VOICES="en_US-lessac-medium en_US-amy-medium en_GB-alan-medium"
RUN mkdir -p /voices && for v in $PIPER_VOICES; do python -m piper.download_voices --data-dir /voices "$v"; done

EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/voices', timeout=4)" || exit 1

CMD python -m piper.http_server --host 0.0.0.0 --port 5000 --data-dir /voices -m "$PIPER_VOICE"
