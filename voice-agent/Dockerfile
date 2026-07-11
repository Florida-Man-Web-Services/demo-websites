# Build from the REPO ROOT: docker build -f voice-agent/Dockerfile .
FROM python:3.12-slim

WORKDIR /app

COPY voice-agent/requirements.txt voice-agent/requirements.txt
RUN pip install --no-cache-dir -r voice-agent/requirements.txt

# Business data: correspondences/*.csv are intentionally NOT baked in (see
# mcp-server/Dockerfile) — load_businesses() falls back to the committed JSON;
# mount real outreach data and point OUTREACH_CSV/CALL_ORDER_CSV at it.
COPY gainesville-no-website/gainesville_no_website.json gainesville-no-website/
COPY voice-agent/*.py voice-agent/

# Call log and synthesized-audio cache live on a mounted volume so outcomes
# (including the permanent do-not-call list) survive restarts.
ENV CALL_LOG=/data/call-log.csv \
    AUDIO_CACHE_DIR=/data/audio_cache
RUN mkdir -p /data

EXPOSE 8035
WORKDIR /app/voice-agent
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8035"]
