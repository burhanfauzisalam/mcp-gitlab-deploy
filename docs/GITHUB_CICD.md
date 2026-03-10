# GitHub CI/CD Guide

Dokumen ini menjelaskan alur CI/CD untuk repo ini menggunakan GitHub Actions.

## Scope CI/CD

- CI/CD repo ini hanya untuk deploy **service MCP** (`gitlab-deploy-mcp`).
- Setelah service MCP hidup, service tersebut akan deploy aplikasi lain dari repo GitLab saat tool `deploy_gitlab_app` dipanggil (misalnya dari Codex).

## Workflow yang Digunakan

- CI: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)
- Deploy: [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml)

## Alur CI

Workflow `CI` berjalan di:
- `pull_request`
- `push` ke `main`
- `push` tag `v*`

Job CI:
1. `validate-python`
- Install package (`pip install -e .`)
- Compile source (`python -m compileall src`)

2. `validate-compose`
- Render `docker-compose.traefik.yml` untuk validasi konfigurasi

3. `build-docker`
- Build image Docker dari `Dockerfile` (validasi build image)

## Alur Deploy

Workflow `Deploy` berjalan:
- otomatis saat `CI` sukses di branch `main` (`workflow_run`)
- manual via `workflow_dispatch`

Deploy flow:
1. Validasi secret SSH wajib.
2. Install `openssh-client` + `rsync`.
3. Jalankan script [`scripts/ci/deploy_via_ssh.sh`](../scripts/ci/deploy_via_ssh.sh):
- rsync source ke server
- generate `.env` runtime
- deploy `docker compose -f docker-compose.traefik.yml up -d --build --remove-orphans`
- path data persistent (`DEPLOYMENT_DATA_DIR`) diproteksi dari `rsync --delete`

## GitHub Secrets dan Variables

### Secrets wajib

- `SSH_HOST`
- `SSH_USER`
- `SSH_PRIVATE_KEY`

### Secrets opsional

- `SSH_PORT` (default `22`)
- `GITLAB_ACCESS_TOKEN` (agar tool MCP bisa clone repo GitLab private via HTTPS)

### Variables opsional

- `DEPLOY_PATH` (default `/home/<SSH_USER>/apps/gitlab-deploy-mcp`)
- `TRAEFIK_NETWORK` (default `web`)
- `TRAEFIK_ENTRYPOINT` (default `websecure`)
- `TRAEFIK_ROUTER_RULE` (default `PathPrefix(`/gitlab-deploy`)`)
- `DEPLOYMENT_DATA_DIR` (default `./data/deployments`)
- `GITLAB_DEPLOYMENT_ROOT` (default `/data/deployments`)
- `MCP_TRANSPORT` (default `streamable-http`)
- `MCP_HOST` (default `0.0.0.0`)
- `MCP_PORT` (default `8000`)
- `MCP_STREAMABLE_HTTP_PATH` (default `/mcp`)
- `MCP_MOUNT_PATH` (default kosong)
- `MCP_SSE_PATH` (default `/sse`)

## Catatan Operasional

- Server target harus memiliki Docker + Docker Compose.
- User SSH harus bisa akses Docker daemon (langsung atau via passwordless sudo).
- Endpoint publik MCP tetap direkomendasikan via Traefik path prefix:
  - `https://<domain>/gitlab-deploy/mcp`
