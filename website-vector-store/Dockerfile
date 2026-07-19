FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY wvs ./wvs
COPY mcp_tool.py .
ENV WVS_STORE=/data/wvs.sqlite
VOLUME ["/data"]
ENTRYPOINT ["python", "-m", "wvs.cli"]
CMD ["update"]
