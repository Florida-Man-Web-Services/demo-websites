# Build from the REPO ROOT: docker build -f mcp-server/Dockerfile .
FROM python:3.12-slim

WORKDIR /app

COPY mcp-server/requirements.txt mcp-server/requirements.txt
RUN pip install --no-cache-dir -r mcp-server/requirements.txt

# git + gh + curl: SITE_PR push/PR/merge. Arch-aware gh apt repo (not a pinned amd64 tarball).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates gnupg \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
         -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
         > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Shared data layer + baked-in business data (refreshed on image rebuild).
COPY voice-agent/businesses.py voice-agent/config.py voice-agent/calldb.py voice-agent/
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
COPY mcp-server/activities_allowlist.json mcp-server/
COPY mcp-server/entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh \
    && git --version \
    && gh --version \
    && curl --version >/dev/null

# Call log + relational call DB live on a mounted volume so outcomes/transcripts
# survive restarts. Shallow site-PR clone lives under /data/demo-websites.
ENV CALL_LOG=/data/call-log.csv \
    CALL_DB=/data/call-log.db \
    SITE_PR_REPO_ROOT=/data/demo-websites \
    ACTIVITIES_PATH=/data/activities.json
RUN mkdir /data

EXPOSE 8036
ENTRYPOINT ["/entrypoint.sh"]
