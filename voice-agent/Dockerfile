# Build from the REPO ROOT: docker build -f voice-agent/Dockerfile .
FROM python:3.12-slim

WORKDIR /app

COPY voice-agent/requirements.txt voice-agent/requirements.txt
RUN pip install --no-cache-dir -r voice-agent/requirements.txt

# Business data: correspondences/*.csv are intentionally NOT baked in (see
# mcp-server/Dockerfile) — load_businesses() falls back to the committed JSON;
# mount real outreach data and point OUTREACH_CSV/CALL_ORDER_CSV at it.
COPY gainesville-no-website/gainesville_no_website.json gainesville-no-website/
# Demo pages: needed to compute hash-style URLs (DEMO_URL_STYLE=hash) — the
# URL is sha256(page)[:12], so the pages themselves must be readable.
COPY generated-sites generated-sites/
COPY voice-agent/*.py voice-agent/
# Product-loop + AI 411 inproc stores. agent.resolve_call_mode and
# /api/onboarding/* import customers from sibling mcp-server/; mcp_bridge
# imports knowledge/events/callers/broadcasts/lookup/changerequests/qotd/fomo.
# Without these, AGENT_MODE=auto accepts Twilio then crashes the WS (~1s hangup).
# Missing qotd/fomo fails the WHOLE inproc import set and stubs AI411 tools.
COPY mcp-server/customers.py \
     mcp-server/knowledge.py \
     mcp-server/events.py \
     mcp-server/callers.py \
     mcp-server/broadcasts.py \
     mcp-server/lookup.py \
     mcp-server/changerequests.py \
     mcp-server/calllog.py \
     mcp-server/qotd.py \
     mcp-server/fomo.py \
     mcp-server/personal_pages.py \
     mcp-server/
# Cron/CLI Visit Gainesville ingest (AI 411 events store)
COPY scripts/ingest_visitgainesville_events.py scripts/

# Call log and synthesized-audio cache live on a mounted volume so outcomes
# (including the permanent do-not-call list) survive restarts.
ENV CALL_LOG=/data/call-log.csv \
    CALL_DB=/data/call-log.db \
    AUDIO_CACHE_DIR=/data/audio_cache \
    CUSTOMERS_PATH=/data/customers.json \
    BUILDER_BRIEFS_DIR=/data/builder-briefs \
    MEMORY_DIR=/data/customer-memory \
    EVENTS_PATH=/data/events.json \
    PERSONAL_PAGES_REGISTRY=/data/personal-pages.json \
    PERSONAL_PAGES_DIR=/data/personal-pages \
    PERSONAL_PAGE_BASE_URL=https://voice.flmanbiosci.net/me
RUN mkdir -p /data /data/builder-briefs /data/customer-memory /data/audio_cache /data/personal-pages

EXPOSE 8035
WORKDIR /app/voice-agent
# Fail closed at build time if packaging drifts again.
RUN python -c "import sys; sys.path[:0]=['/app/mcp-server','/app/voice-agent']; import customers, knowledge, events, callers, broadcasts, lookup, changerequests, qotd, fomo, personal_pages; print('mcp-server ok', customers.MODE_AI411, 'qotd+fomo+personal_pages')"
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8035"]
