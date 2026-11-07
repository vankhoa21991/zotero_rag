# aikeytake-zog — library server (Docker image)
# Build from the repo root:  docker build -f deploy/Dockerfile -t aikeytake-zog .
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[server,pdf]"

# data lives in a volume (see docker-compose.yml)
ENV AIKEYTAKE_ZOG_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8321

CMD ["aikeytake-zog", "serve", "--host", "0.0.0.0", "--port", "8321"]
