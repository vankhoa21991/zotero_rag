# shelf — library server (Docker image)
# Build from the repo root:  docker build -f deploy/Dockerfile -t shelf .
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[server,pdf]"

# data lives in a volume (see docker-compose.yml)
ENV SHELF_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8321

CMD ["shelf", "serve", "--host", "0.0.0.0", "--port", "8321"]
