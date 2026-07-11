# Build from the REPO ROOT: docker build -f site-tracker/Dockerfile .
# Stage 1 generates sites.json (hash -> business) from generated-sites; the
# runtime image ships only the app + sites.json (not the 246 HTMLs).
FROM python:3.12-slim AS gen
WORKDIR /build
COPY generated-sites ./generated-sites
COPY site-tracker/gen_sites.py ./gen_sites.py
RUN python gen_sites.py ./generated-sites

FROM python:3.12-slim
WORKDIR /app
COPY site-tracker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY site-tracker/app.py .
COPY site-tracker/static ./static
COPY --from=gen /build/sites.json ./sites.json

# Notes DB lives on a mounted volume so it survives restarts.
ENV TRACKER_DB=/data/tracker.db
RUN mkdir -p /data
EXPOSE 8040
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8040"]
