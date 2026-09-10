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
        slug="$(basename "$f" .html)"; \
        if [ -d "/src/$slug" ]; then cp -a "/src/$slug" "/out/$h/$slug"; fi; \
        if [ "$slug" = "impacto" ] && [ -d /src/impacto-pages ]; then cp -a /src/impacto-pages/. "/out/$h/"; fi; \
    done

FROM nginx:alpine
COPY --from=build /out /usr/share/nginx/html
COPY hosting/root-index.html /usr/share/nginx/html/index.html
# AI 411 landing (also serve under /ai411/ on the apex until ai411.* DNS is live)
COPY hosting/ai411/index.html /usr/share/nginx/html/ai411/index.html
COPY hosting/resume/index.html /usr/share/nginx/html/resume/index.html
# Arete Tutoring splash (proxied app takes /arete-tutoring/ when service is up;
# this file is a fallback if proxy is down — also useful for local static browse)
COPY hosting/arete-tutoring/index.html /usr/share/nginx/html/arete-tutoring/index.html
# Stable vanity URL for IMPACTO custom domain (impacto.community). Hash paths stay
# the unguessable catalog URLs; this path must not change when HTML bytes change.
COPY generated-sites/impacto.html /usr/share/nginx/html/vanity/impacto/index.html
COPY generated-sites/impacto /usr/share/nginx/html/vanity/impacto/impacto
# Inner Wix routes (course-registration, teacher-registration, …) as first-party pages.
COPY generated-sites/impacto-pages/ /usr/share/nginx/html/vanity/impacto/
COPY hosting/nginx-default.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
