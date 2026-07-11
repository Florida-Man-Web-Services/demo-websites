# Serves generated-sites/ at content-hashed paths for floridamanweb.online:
#   https://floridamanweb.online/<sha256(page)[:12]>/
# Build from the REPO ROOT: docker build -f hosting/Dockerfile .
#
# The hash rule (sha256 of the file bytes, first 12 hex chars) must stay in
# lockstep with demo_site_hash() in voice-agent/businesses.py — that's how the
# voice agent and MCP server compute the URL they hand out.

FROM alpine:3 AS build
COPY generated-sites /src
RUN mkdir /out && for f in /src/*.html; do \
        h="$(sha256sum "$f" | cut -c1-12)"; \
        mkdir -p "/out/$h"; \
        cp "$f" "/out/$h/index.html"; \
    done

FROM nginx:alpine
COPY --from=build /out /usr/share/nginx/html
COPY hosting/root-index.html /usr/share/nginx/html/index.html
EXPOSE 80
