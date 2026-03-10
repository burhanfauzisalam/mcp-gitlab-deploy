# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    docker.io; \
    (apt-get install -y --no-install-recommends docker-compose-plugin || apt-get install -y --no-install-recommends docker-compose || true); \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip && pip install .

RUN mkdir -p /data/deployments

ENV GITLAB_DEPLOYMENT_ROOT=/data/deployments \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    MCP_STREAMABLE_HTTP_PATH=/mcp \
    MCP_MOUNT_PATH=

EXPOSE 8000

CMD ["gitlab-deploy-mcp"]
