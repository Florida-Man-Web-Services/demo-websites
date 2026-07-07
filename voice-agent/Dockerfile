# Voice sales agent — FastAPI server (Twilio <-> Claude <-> Sesame CSM-1B).
#
# Build from the REPO ROOT so the business data the agent falls back to
# (../gainesville-no-website/gainesville_no_website.json, resolved relative to
# the code in config.py) is inside the build context:
#
#   docker build -f voice-agent/Dockerfile -t demo-websites-voice-agent .
#
# or just `docker compose up` from the voice-agent/ directory.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # Persist the audio cache and call log outside the image layer so they
    # survive `docker run --rm` / container recreation (mount a volume at /data).
    AUDIO_CACHE_DIR=/data/audio_cache \
    CALL_LOG=/data/call-log.csv

WORKDIR /app/voice-agent

# Dependencies first, so code edits don't bust the pip layer.
COPY voice-agent/requirements.txt ./
RUN pip install -r requirements.txt

# Application code.
COPY voice-agent/ ./

# Fallback business list, kept at the repo-relative path config.py expects
# (REPO_ROOT/gainesville-no-website/...). config.py computes REPO_ROOT as the
# parent of this code directory, i.e. /app.
COPY gainesville-no-website/gainesville_no_website.json \
     /app/gainesville-no-website/gainesville_no_website.json

# Run unprivileged. /data is created in the image so a fresh named volume
# mounted there inherits appuser ownership and stays writable.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/audio_cache \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8035

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8035/health').status==200 else 1)"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8035"]
