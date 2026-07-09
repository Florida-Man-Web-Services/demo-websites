# Build from the REPO ROOT: docker build -f mcp-server/Dockerfile .
FROM python:3.12-slim

WORKDIR /app

COPY mcp-server/requirements.txt mcp-server/requirements.txt
RUN pip install --no-cache-dir -r mcp-server/requirements.txt

# Shared data layer + baked-in business data (refreshed on image rebuild).
COPY voice-agent/businesses.py voice-agent/config.py voice-agent/
COPY correspondences/outreach-data.csv correspondences/call-order.csv correspondences/
COPY gainesville-no-website/gainesville_no_website.json gainesville-no-website/
COPY mcp-server/*.py mcp-server/

# Call log lives on a mounted volume so outcomes survive restarts.
ENV CALL_LOG=/data/call-log.csv
RUN mkdir /data

EXPOSE 8036
CMD ["python", "mcp-server/server.py"]
