# GitLab Deploy MCP

MCP server untuk deployment aplikasi dari GitLab ke server menggunakan Docker + Traefik (routing `PathPrefix`).

## Fitur

- Tool `detect_tech_stack` untuk deteksi stack aplikasi.
- Tool `deploy_gitlab_app` untuk clone/pull repo, deteksi stack, generate artefak Docker + Traefik, lalu deploy.
- Deploy app target selalu ke folder persistent `<GITLAB_DEPLOYMENT_ROOT>/<app_name>`.
  Default: `/home/ubuntu/apps/deploy/<app_name>`.
- Network Traefik app target selalu `web`.
- Domain (`host`) dan `path_prefix` wajib diisi sebelum deploy.
- Auto-detection stack:
  - Laravel
  - CodeIgniter 3 / 4
  - Node.js
  - Python (Django / Flask / FastAPI / generic)

## Endpoint MCP (untuk Codex)

Saat dijalankan dengan Traefik config bawaan repo ini, endpoint MCP publiknya:

- `https://<domain-anda>/gitlab-deploy/mcp`

Traefik akan `StripPrefix(/gitlab-deploy)` dan meneruskan request ke service MCP di path internal `/mcp`.

## Jalankan dengan Docker + Traefik

### 1) Siapkan env

```bash
cp .env.example .env
```

Edit `.env` minimal:

- `TRAEFIK_NETWORK` (network external Traefik, default `web`)
- `TRAEFIK_ENTRYPOINT` (default `websecure`)
- `MAIN_DOMAIN` (mis. `apps.example.com`)

### 2) Build dan jalankan

```bash
docker compose -f docker-compose.traefik.yml up -d --build
```

### 3) Verifikasi endpoint

Akses endpoint MCP:

- `https://<domain-anda>/gitlab-deploy/mcp`

## Konfigurasi Codex di VS Code

Gunakan konfigurasi MCP remote `streamable-http`.
Contoh ada di file [examples/codex.mcp.json](./examples/codex.mcp.json).

Contoh isi:

```json
{
  "mcpServers": {
    "gitlab-deploy": {
      "transport": "streamable-http",
      "url": "https://example.com/gitlab-deploy/mcp"
    }
  }
}
```

Sesuaikan URL sesuai domain Anda.

## Menjalankan secara lokal (tanpa Docker)

Instalasi:

```bash
pip install -e .
```

Mode stdio (default):

```bash
gitlab-deploy-mcp
```

Mode HTTP streamable:

```bash
gitlab-deploy-mcp --transport streamable-http --host 0.0.0.0 --port 8000 --streamable-http-path /mcp
```

## CI/CD GitHub

Repo ini menggunakan GitHub Actions:

- [`.github/workflows/ci.yml`](./.github/workflows/ci.yml) untuk validasi source + build image.
- [`.github/workflows/deploy.yml`](./.github/workflows/deploy.yml) untuk deploy service MCP ke server.

Catatan arsitektur:

- CI/CD project ini hanya untuk deploy **service MCP**.
- Service MCP inilah yang nanti men-deploy **aplikasi lain** dari repo GitLab saat tool `deploy_gitlab_app` dipanggil dari Codex.

Job deploy menggunakan script [scripts/ci/deploy_via_ssh.sh](./scripts/ci/deploy_via_ssh.sh).

Variable penting untuk deploy:

- `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`
- `SSH_PORT` (opsional, default `22`)
- `DEPLOY_PATH` (opsional, default `/home/<ssh_user>/apps/gitlab-deploy-mcp`)
- `TRAEFIK_NETWORK` (default `web`), `TRAEFIK_ENTRYPOINT` (default `websecure`), `MAIN_DOMAIN`
- `GITLAB_DEPLOYMENT_ROOT` (default `/home/ubuntu/apps/deploy`), `DEPLOYMENT_DATA_DIR`
  Pastikan `DEPLOYMENT_DATA_DIR` dan `GITLAB_DEPLOYMENT_ROOT` menunjuk path yang sama agar persistent di host.
- `GITLAB_ACCESS_TOKEN` (opsional, untuk default clone repo private oleh tool runtime)
- `MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS` (opsional, allowlist Host/Origin untuk endpoint MCP)
- `MCP_ENABLE_DNS_REBINDING_PROTECTION` (opsional, `true/false`)

Panduan detail variable dan flow ada di [docs/GITHUB_CICD.md](./docs/GITHUB_CICD.md).

## Tool 1: `detect_tech_stack`

Input:

- `target` (path lokal atau URL GitLab)
- `branch` (default `main`)
- `git_auth_token` (opsional, token repo private HTTPS)
- `git_auth_username` (default `oauth2`)

Output:

- mode (`local`/`remote`)
- hasil deteksi (`stack`, `framework`, `internal_port`, `start_command`, `indicators`)

## Tool 2: `deploy_gitlab_app`

Input utama:

- `repo_url`
- `app_name`
- `host` (domain, wajib)
- `path_prefix` (contoh `/app1`)

Input opsional:

- `branch` (default `main`)
- `deployment_root` (tetap ada untuk kompatibilitas, tapi diabaikan; gunakan env `GITLAB_DEPLOYMENT_ROOT`)
- `repo_subdir`
- `traefik_network` (tetap ada untuk kompatibilitas, tapi diabaikan; selalu `web`)
- `traefik_entrypoint` (default `websecure`)
- `env_vars`
- `git_auth_token` (opsional, token repo private HTTPS)
- `git_auth_username` (default `oauth2`)
- `run_compose`
- `force_rebuild`

## File penting

- [src/gitlab_deploy_mcp/server.py](./src/gitlab_deploy_mcp/server.py): implementasi tool MCP.
- [Dockerfile](./Dockerfile): image MCP server.
- [docker-compose.traefik.yml](./docker-compose.traefik.yml): deployment MCP + Traefik labels.
- [.env.example](./.env.example): contoh environment.
- [examples/codex.mcp.json](./examples/codex.mcp.json): contoh config Codex VS Code.
- [docs/MAINTENANCE_COMMAND_REFERENCE.md](./docs/MAINTENANCE_COMMAND_REFERENCE.md): referensi command untuk tiap function/variable.
- [docs/GITHUB_CICD.md](./docs/GITHUB_CICD.md): referensi flow dan variable GitHub CI/CD.

## Catatan operasional

- Container MCP butuh akses Docker host (`/var/run/docker.sock`) agar tool deployment bisa menjalankan `docker compose`.
- Pastikan Traefik dan service MCP berada pada network Docker yang sama.
- Untuk production, tambahkan auth/protection di layer Traefik (IP whitelist, basic auth, atau auth proxy) sebelum endpoint dibuka publik.
