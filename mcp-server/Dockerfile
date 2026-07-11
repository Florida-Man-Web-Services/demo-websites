# Build from the REPO ROOT: docker build -f mcp-server/Dockerfile .
FROM python:3.12-slim

WORKDIR /app

COPY mcp-server/requirements.txt mcp-server/requirements.txt
RUN pip install --no-cache-dir -r mcp-server/requirements.txt

# Shared data layer + baked-in business data (refreshed on image rebuild).
COPY voice-agent/businesses.py voice-agent/config.py voice-agent/
# NOTE: correspondences/*.csv (outreach + call-order) are intentionally NOT baked
# into the image (they're uncommitted business data). load_businesses() falls back
# to gainesville-no-website/gainesville_no_website.json and call_queue() returns []
# when they're absent. Supply real data at runtime via the OUTREACH_CSV /
# CALL_ORDER_CSV env vars pointing at a mounted volume.
COPY gainesville-no-website/gainesville_no_website.json gainesville-no-website/
# Demo pages: needed to compute hash-style URLs (DEMO_URL_STYLE=hash) — the
# URL is sha256(page)[:12], so the pages themselves must be readable.
COPY generated-sites generated-sites/
COPY mcp-server/*.py mcp-server/

# Call log lives on a mounted volume so outcomes survive restarts.
ENV CALL_LOG=/data/call-log.csv
RUN mkdir /data

EXPOSE 8036
CMD ["python", "mcp-server/server.py"]
