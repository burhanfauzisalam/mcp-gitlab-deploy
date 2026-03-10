from __future__ import annotations

"""MCP server untuk deployment aplikasi GitLab melalui Docker + Traefik.

File ini berisi:
- Tool publik MCP: `detect_tech_stack`, `deploy_gitlab_app`.
- Helper internal untuk clone/pull repo, deteksi stack, render artefak Docker,
  dan startup server MCP (stdio atau streamable-http).
"""

import argparse
import inspect
import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from mcp.server.fastmcp import FastMCP
try:
    from mcp.server.transport_security import TransportSecuritySettings
except ImportError:
    TransportSecuritySettings = None  # type: ignore[assignment]


def _parse_env_csv(name: str) -> list[str]:
    """Parse env comma-separated list menjadi list bersih."""
    raw_value = os.getenv(name, "")
    return [part.strip() for part in raw_value.split(",") if part.strip()]


def _parse_env_bool(name: str) -> bool | None:
    """Parse env bool; return None jika tidak diisi."""
    raw_value = os.getenv(name)
    if raw_value is None:
        return None

    normalized = raw_value.strip().lower()
    if normalized in ("1", "true", "yes", "y", "on"):
        return True
    if normalized in ("0", "false", "no", "n", "off"):
        return False
    return None


def _unique_preserve(values: list[str]) -> list[str]:
    """Hilangkan duplikat tanpa mengubah urutan awal."""
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return unique_values


def _extract_host_from_domain(value: str) -> str:
    """Ambil host/domain dari MAIN_DOMAIN (boleh berformat URL)."""
    cleaned = value.strip()
    if not cleaned:
        return ""

    parsed = urlsplit(cleaned if "://" in cleaned else f"https://{cleaned}")
    host = parsed.netloc or parsed.path
    host = host.strip().strip("/")
    if host.startswith("[") and "]" in host:
        return host
    if ":" in host:
        return host.split(":", 1)[0]
    return host


def _normalize_host_entry(value: str) -> str:
    """Normalisasi value host allowlist agar konsisten untuk middleware."""
    cleaned = value.strip().strip("/")
    if not cleaned:
        return ""

    if "://" in cleaned:
        parsed = urlsplit(cleaned)
        host = parsed.netloc.strip()
    else:
        host = cleaned

    if not host:
        return ""

    if host.startswith("["):
        if "]:" in host:
            return host
        if host.endswith("]"):
            return f"{host}:*"
        return host

    return host if ":" in host else f"{host}:*"


def _build_transport_security() -> Any:
    """Bangun konfigurasi transport security dari environment.

    Tujuan:
    - Mengizinkan Host header domain publik (mis. lewat Traefik) agar tidak 421.
    - Tetap mempertahankan proteksi DNS rebinding bila domain/allowlist tersedia.
    """
    if TransportSecuritySettings is None:
        return None

    main_domain = _extract_host_from_domain(os.getenv("MAIN_DOMAIN", ""))

    configured_hosts: list[str] = []
    if main_domain:
        configured_hosts.extend([main_domain, f"{main_domain}:*"])

    configured_hosts.extend(
        host
        for host in (_normalize_host_entry(item) for item in _parse_env_csv("MCP_ALLOWED_HOSTS"))
        if host
    )

    explicit_enable = _parse_env_bool("MCP_ENABLE_DNS_REBINDING_PROTECTION")
    enable_protection = explicit_enable if explicit_enable is not None else bool(configured_hosts)

    if not enable_protection:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    safe_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    allowed_hosts = _unique_preserve(safe_hosts + configured_hosts)

    configured_origins = _parse_env_csv("MCP_ALLOWED_ORIGINS")
    if main_domain:
        configured_origins.extend(
            [
                f"https://{main_domain}",
                f"https://{main_domain}:*",
                f"http://{main_domain}",
                f"http://{main_domain}:*",
            ]
        )
    safe_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    allowed_origins = _unique_preserve(safe_origins + configured_origins)

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


_transport_security = _build_transport_security()

# Objek server MCP utama yang meregistrasikan seluruh tool.
try:
    if _transport_security is None:
        mcp = FastMCP("gitlab-deploy-mcp")
    else:
        mcp = FastMCP("gitlab-deploy-mcp", transport_security=_transport_security)
except TypeError:
    # Fallback untuk versi SDK yang belum mendukung argumen `transport_security`.
    mcp = FastMCP("gitlab-deploy-mcp")
# Root deploy persistent default (bisa dioverride via env `GITLAB_DEPLOYMENT_ROOT`).
PERSISTENT_DEPLOYMENT_ROOT = "/home/ubuntu/apps/deploy"
# Network Traefik untuk app target selalu fixed ke `web`.
TRAEFIK_NETWORK_FIXED = "web"
# Dipertahankan untuk kompatibilitas signature tool, tetap bersumber dari env.
DEFAULT_DEPLOYMENT_ROOT = os.getenv("GITLAB_DEPLOYMENT_ROOT", PERSISTENT_DEPLOYMENT_ROOT)


def _sanitize_text(text: str, redactions: list[str] | None = None) -> str:
    """Mask nilai sensitif dari teks log."""
    cleaned = text
    for secret in redactions or []:
        if secret:
            cleaned = cleaned.replace(secret, "***")
    return cleaned


def _run_command(
    command: list[str],
    cwd: Path | None = None,
    check: bool = True,
    redactions: list[str] | None = None,
) -> dict[str, Any]:
    """Jalankan command shell dan kembalikan hasil standar.

    Untuk maintain:
    - Semua eksekusi command eksternal harus lewat fungsi ini agar format log konsisten.
    - Jika `check=True`, kegagalan command akan dilempar sebagai RuntimeError.
    """
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )
    result = {
        "command": _sanitize_text(" ".join(command), redactions),
        "returncode": completed.returncode,
        "stdout": _sanitize_text(completed.stdout.strip(), redactions),
        "stderr": _sanitize_text(completed.stderr.strip(), redactions),
    }
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result['returncode']}): {result['command']}\n"
            f"STDOUT:\n{result['stdout']}\n"
            f"STDERR:\n{result['stderr']}"
        )
    return result


def _slugify_app_name(name: str) -> str:
    """Normalisasi nama aplikasi ke slug aman untuk nama service/container."""
    sanitized = re.sub(r"[^a-zA-Z0-9-]+", "-", name.strip().lower())
    sanitized = re.sub(r"-+", "-", sanitized).strip("-")
    if not sanitized:
        raise ValueError("app_name menghasilkan slug kosong. Gunakan nama aplikasi yang valid.")
    return sanitized


def _normalize_path_prefix(path_prefix: str) -> str:
    """Normalisasi PathPrefix Traefik agar selalu berbentuk `/prefix`."""
    value = path_prefix.strip()
    if not value:
        raise ValueError("path_prefix wajib diisi.")
    if not value.startswith("/"):
        value = f"/{value}"
    if len(value) > 1 and value.endswith("/"):
        value = value[:-1]
    return value


def _normalize_deploy_host(host: str) -> str:
    """Validasi dan normalisasi host/domain untuk rule Traefik."""
    value = host.strip()
    if not value:
        raise ValueError(
            "host/domain wajib diisi sebelum deploy. "
            "Tanyakan domain dan path_prefix ke user sebelum menjalankan deploy."
        )

    parsed = urlsplit(value if "://" in value else f"https://{value}")
    normalized = (parsed.netloc or parsed.path).strip().strip("/")
    if not normalized:
        raise ValueError("host/domain tidak valid.")
    if "/" in normalized:
        raise ValueError("host/domain tidak boleh mengandung path.")
    return normalized


def _resolve_persistent_deployment_root() -> Path:
    """Gunakan root deploy persistent dari env dengan fallback default."""
    configured_root = os.getenv("GITLAB_DEPLOYMENT_ROOT", PERSISTENT_DEPLOYMENT_ROOT).strip()
    root_path = Path(configured_root or PERSISTENT_DEPLOYMENT_ROOT).expanduser().resolve()
    root_path.mkdir(parents=True, exist_ok=True)
    return root_path


def _normalize_mount_path(path: str) -> str:
    """Normalisasi path endpoint MCP untuk runtime HTTP transport."""
    value = path.strip()
    if not value:
        return ""
    if not value.startswith("/"):
        value = f"/{value}"
    if len(value) > 1 and value.endswith("/"):
        value = value[:-1]
    return value


def _normalize_repo_subdir(repo_subdir: str) -> str:
    """Validasi dan normalisasi subdirectory repo.

    Proteksi keamanan:
    - Menolak segmen `.` dan `..` agar tidak bisa path traversal.
    """
    cleaned = repo_subdir.strip().replace("\\", "/").strip("/")
    if not cleaned:
        return ""
    parts = [part for part in cleaned.split("/") if part]
    if any(part in (".", "..") for part in parts):
        raise ValueError("repo_subdir tidak boleh mengandung '.' atau '..'.")
    return "/".join(parts)


def _with_git_http_auth(repo_url: str, username: str, token: str) -> tuple[str, list[str]]:
    """Tambahkan kredensial HTTP(S) ke URL git (untuk repo private GitLab)."""
    parsed = urlsplit(repo_url)
    if parsed.scheme not in ("http", "https"):
        return repo_url, [token]

    netloc = parsed.netloc
    if "@" in netloc:
        return repo_url, [token]

    safe_username = quote(username, safe="")
    safe_token = quote(token, safe="")
    authed_netloc = f"{safe_username}:{safe_token}@{netloc}"
    authed_url = urlunsplit((parsed.scheme, authed_netloc, parsed.path, parsed.query, parsed.fragment))
    return authed_url, [token, safe_token]


def _prepare_repo_url(repo_url: str, git_auth_token: str | None, git_auth_username: str) -> tuple[str, list[str]]:
    """Siapkan URL clone dan daftar redaction untuk log."""
    token = (git_auth_token or os.getenv("GITLAB_ACCESS_TOKEN", "")).strip()
    if not token:
        return repo_url, []
    return _with_git_http_auth(repo_url=repo_url, username=git_auth_username, token=token)


def _load_json_file(file_path: Path) -> dict[str, Any]:
    """Baca file JSON secara aman; fallback `{}` saat JSON invalid."""
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_text_if_exists(file_path: Path) -> str:
    """Baca file text jika ada; fallback string kosong jika tidak bisa dibaca."""
    if not file_path.exists():
        return ""
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


def _detect_stack(source_dir: Path) -> dict[str, Any]:
    """Deteksi stack/framework aplikasi dari marker file standar.

    Prioritas deteksi:
    1. Laravel
    2. CodeIgniter (v4/v3)
    3. Node.js
    4. Python
    """
    indicators: list[str] = []
    framework = "unknown"
    stack = "unknown"
    start_command: str | None = None
    internal_port = 8080

    composer_json = _read_text_if_exists(source_dir / "composer.json").lower()
    requirements_txt = _read_text_if_exists(source_dir / "requirements.txt").lower()
    pyproject_toml = _read_text_if_exists(source_dir / "pyproject.toml").lower()

    has_artisan = (source_dir / "artisan").exists()
    has_composer = (source_dir / "composer.json").exists()
    has_ci4_spark = (source_dir / "spark").exists()
    has_ci4_app = (source_dir / "app" / "Config" / "App.php").exists()
    has_ci3_config = (source_dir / "application" / "config" / "config.php").exists()
    has_package_json = (source_dir / "package.json").exists()
    has_python_markers = any(
        (source_dir / filename).exists()
        for filename in ("requirements.txt", "pyproject.toml", "Pipfile", "manage.py", "app.py", "main.py")
    )

    if has_artisan and has_composer:
        stack = "php"
        framework = "laravel"
        internal_port = 80
        indicators.extend(["artisan", "composer.json"])
    elif "laravel/framework" in composer_json:
        stack = "php"
        framework = "laravel"
        internal_port = 80
        indicators.append("composer.json:laravel/framework")
    elif has_ci4_spark and has_ci4_app:
        stack = "php"
        framework = "codeigniter4"
        internal_port = 80
        indicators.extend(["spark", "app/Config/App.php"])
    elif "codeigniter4/framework" in composer_json:
        stack = "php"
        framework = "codeigniter4"
        internal_port = 80
        indicators.append("composer.json:codeigniter4/framework")
    elif has_ci3_config:
        stack = "php"
        framework = "codeigniter3"
        internal_port = 80
        indicators.append("application/config/config.php")
    elif has_package_json:
        stack = "nodejs"
        framework = "nodejs"
        internal_port = 3000
        indicators.append("package.json")
        package_json_data = _load_json_file(source_dir / "package.json")
        scripts = package_json_data.get("scripts", {}) if isinstance(package_json_data, dict) else {}
        if isinstance(scripts, dict) and "start" in scripts:
            start_command = "npm run start"
            indicators.append("package.json:scripts.start")
        elif isinstance(scripts, dict) and "dev" in scripts:
            start_command = "npm run dev -- --host 0.0.0.0 --port 3000"
            indicators.append("package.json:scripts.dev")
        elif (source_dir / "server.js").exists():
            start_command = "node server.js"
            indicators.append("server.js")
        elif (source_dir / "app.js").exists():
            start_command = "node app.js"
            indicators.append("app.js")
        else:
            start_command = "node index.js"
            indicators.append("default:index.js")
    elif has_python_markers:
        stack = "python"
        framework = "python"
        internal_port = 8000
        indicators.append("python markers")

        if (source_dir / "manage.py").exists():
            start_command = "python manage.py runserver 0.0.0.0:8000"
            indicators.append("manage.py")
        elif "fastapi" in requirements_txt or "fastapi" in pyproject_toml:
            start_command = "uvicorn main:app --host 0.0.0.0 --port 8000"
            indicators.append("fastapi")
        elif "flask" in requirements_txt or "flask" in pyproject_toml:
            start_command = "gunicorn app:app --bind 0.0.0.0:8000"
            indicators.append("flask")
        elif (source_dir / "app.py").exists():
            start_command = "python app.py"
            indicators.append("app.py")
        elif (source_dir / "main.py").exists():
            start_command = "python main.py"
            indicators.append("main.py")
        else:
            start_command = "python -m http.server 8000"
            indicators.append("default:http.server")
    else:
        indicators.append("unknown stack markers")

    repo_kind = "gitlab" if "gitlab" in str(source_dir).lower() else "generic"

    return {
        "stack": stack,
        "framework": framework,
        "internal_port": internal_port,
        "start_command": start_command,
        "indicators": indicators,
        "repo_kind": repo_kind,
    }


def _render_dockerfile(detection: dict[str, Any], repo_subdir: str) -> str:
    """Generate konten Dockerfile berdasarkan hasil deteksi stack."""
    framework = detection["framework"]
    stack = detection["stack"]
    cleaned_subdir = _normalize_repo_subdir(repo_subdir)
    source_dir = "repo"
    if cleaned_subdir:
        source_dir = f"repo/{cleaned_subdir}"

    if framework == "laravel":
        return textwrap.dedent(
            f"""
            FROM php:8.2-apache

            RUN apt-get update && apt-get install -y --no-install-recommends \
                git unzip libzip-dev \
                && docker-php-ext-install pdo pdo_mysql zip \
                && a2enmod rewrite \
                && rm -rf /var/lib/apt/lists/*

            COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

            WORKDIR /var/www/html
            COPY {source_dir}/ ./

            RUN if [ -f composer.json ]; then composer install --no-dev --prefer-dist --no-interaction --optimize-autoloader; fi
            RUN chown -R www-data:www-data /var/www/html
            RUN sed -ri -e 's!/var/www/html!/var/www/html/public!g' /etc/apache2/sites-available/*.conf /etc/apache2/apache2.conf

            EXPOSE 80
            CMD ["apache2-foreground"]
            """
        ).strip()

    if framework in ("codeigniter4", "codeigniter3"):
        document_root = "/var/www/html/public" if framework == "codeigniter4" else "/var/www/html"
        return textwrap.dedent(
            f"""
            FROM php:8.2-apache

            RUN apt-get update && apt-get install -y --no-install-recommends \
                git unzip libzip-dev \
                && docker-php-ext-install pdo pdo_mysql zip \
                && a2enmod rewrite \
                && rm -rf /var/lib/apt/lists/*

            COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

            WORKDIR /var/www/html
            COPY {source_dir}/ ./

            RUN if [ -f composer.json ]; then composer install --no-dev --prefer-dist --no-interaction --optimize-autoloader; fi
            RUN chown -R www-data:www-data /var/www/html
            RUN sed -ri -e 's!/var/www/html!{document_root}!g' /etc/apache2/sites-available/*.conf /etc/apache2/apache2.conf

            EXPOSE 80
            CMD ["apache2-foreground"]
            """
        ).strip()

    if stack == "nodejs":
        start_command = detection.get("start_command") or "npm run start"
        return textwrap.dedent(
            f"""
            FROM node:20-alpine

            WORKDIR /app

            COPY {source_dir}/package*.json ./
            RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

            COPY {source_dir}/ ./

            ENV NODE_ENV=production
            EXPOSE 3000
            CMD ["sh", "-c", "{start_command}"]
            """
        ).strip()

    if stack == "python":
        start_command = detection.get("start_command") or "python main.py"
        return textwrap.dedent(
            f"""
            FROM python:3.11-slim

            WORKDIR /app

            ENV PYTHONDONTWRITEBYTECODE=1
            ENV PYTHONUNBUFFERED=1

            COPY {source_dir}/ ./
            RUN pip install --no-cache-dir --upgrade pip && \
                if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi && \
                if [ -f pyproject.toml ]; then pip install --no-cache-dir .; fi

            EXPOSE 8000
            CMD ["sh", "-c", "{start_command}"]
            """
        ).strip()

    return textwrap.dedent(
        f"""
        FROM nginx:1.27-alpine
        COPY {source_dir}/ /usr/share/nginx/html
        EXPOSE 80
        """
    ).strip()


def _render_compose(
    app_name: str,
    path_prefix: str,
    host: str,
    traefik_network: str,
    traefik_entrypoint: str,
    internal_port: int,
    env_vars: dict[str, str] | None,
) -> str:
    """Generate docker-compose.yml dengan label Traefik PathPrefix."""
    router = app_name
    service = app_name

    rule = f"Host(`{host}`) && PathPrefix(`{path_prefix}`)"

    labels = [
        "- \"traefik.enable=true\"",
        f"- \"traefik.docker.network={traefik_network}\"",
        f"- \"traefik.http.routers.{router}.entrypoints={traefik_entrypoint}\"",
        f"- \"traefik.http.routers.{router}.rule={rule}\"",
        f"- \"traefik.http.services.{service}.loadbalancer.server.port={internal_port}\"",
    ]

    if path_prefix != "/":
        middleware_name = f"{app_name}-stripprefix"
        labels.append(f"- \"traefik.http.routers.{router}.middlewares={middleware_name}\"")
        labels.append(f"- \"traefik.http.middlewares.{middleware_name}.stripprefix.prefixes={path_prefix}\"")

    env_lines = []
    if env_vars:
        env_lines.append("    environment:")
        for key, value in sorted(env_vars.items()):
            escaped = str(value).replace('"', '\\"')
            env_lines.append(f"      {key}: \"{escaped}\"")

    compose = [
        "services:",
        f"  {service}:",
        f"    container_name: {app_name}-app",
        "    build:",
        "      context: .",
        "      dockerfile: ./Dockerfile",
        "    restart: unless-stopped",
        "    labels:",
    ]
    compose.extend([f"      {line}" for line in labels])
    compose.extend(
        [
            "    networks:",
            "      - proxy",
        ]
    )
    compose.extend(env_lines)
    compose.extend(
        [
            "",
            "networks:",
            "  proxy:",
            "    external: true",
            f"    name: {traefik_network}",
        ]
    )
    return "\n".join(compose).strip() + "\n"


def _render_dockerignore() -> str:
    """Generate konten .dockerignore default untuk build hasil deployment."""
    return textwrap.dedent(
        """
        .git
        .gitlab
        .github
        node_modules
        vendor
        .venv
        venv
        __pycache__
        *.log
        .env
        """
    ).strip() + "\n"


def _clone_or_update_repo(
    repo_url: str,
    branch: str,
    repo_dir: Path,
    redactions: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Clone repo baru atau update repo existing ke branch target."""
    logs: list[dict[str, Any]] = []

    if (repo_dir / ".git").exists():
        logs.append(_run_command(["git", "fetch", "origin", branch], cwd=repo_dir, redactions=redactions))
        logs.append(_run_command(["git", "checkout", branch], cwd=repo_dir, redactions=redactions))
        logs.append(
            _run_command(["git", "pull", "--ff-only", "origin", branch], cwd=repo_dir, redactions=redactions)
        )
        return logs

    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    logs.append(
        _run_command(
            ["git", "clone", "--depth", "1", "--branch", branch, repo_url, str(repo_dir)],
            redactions=redactions,
        )
    )
    return logs


def _path_exists_or_raise(path: Path, label: str) -> None:
    """Validasi path wajib ada sebelum proses lanjut."""
    if not path.exists():
        raise FileNotFoundError(f"{label} tidak ditemukan: {path}")


def _run_docker_compose(app_path: Path, force_rebuild: bool) -> dict[str, Any]:
    """Jalankan deployment dengan fallback executor compose.

    Urutan fallback:
    - `docker compose`
    - `docker-compose`
    """
    command_candidates: list[list[str]] = [
        ["docker", "compose", "-f", "docker-compose.yml", "up", "-d"],
        ["docker-compose", "-f", "docker-compose.yml", "up", "-d"],
    ]
    if force_rebuild:
        for command in command_candidates:
            command.append("--build")

    failures: list[dict[str, Any]] = []
    for command in command_candidates:
        binary = command[0]
        if shutil.which(binary) is None:
            continue

        result = _run_command(command, cwd=app_path, check=False)
        if result["returncode"] == 0:
            result["executor"] = binary
            return result
        failures.append(result)

    if failures:
        failure_text = "\n\n".join(
            f"{entry['command']} -> {entry['returncode']}\nSTDERR:\n{entry['stderr']}" for entry in failures
        )
        raise RuntimeError(f"Gagal menjalankan docker compose.\n{failure_text}")

    raise RuntimeError("Perintah docker compose tidak ditemukan. Install 'docker compose' atau 'docker-compose'.")


def _build_run_kwargs(
    transport: str,
    host: str,
    port: int,
    mount_path: str,
    streamable_http_path: str,
    sse_path: str,
) -> dict[str, Any]:
    """Bangun kwargs dinamis untuk kompatibilitas versi `mcp` SDK."""
    signature = inspect.signature(mcp.run)
    parameters = signature.parameters
    candidates: dict[str, Any] = {
        "transport": transport,
        "host": host,
        "port": port,
        "mount_path": mount_path or None,
        "streamable_http_path": streamable_http_path or None,
        "sse_path": sse_path or None,
    }
    return {key: value for key, value in candidates.items() if key in parameters and value is not None}


def _apply_runtime_settings(host: str, port: int, mount_path: str, streamable_http_path: str, sse_path: str) -> None:
    """Apply konfigurasi runtime ke `mcp.settings` jika atribut tersedia."""
    settings = getattr(mcp, "settings", None)
    if settings is None:
        return

    if hasattr(settings, "host"):
        settings.host = host
    if hasattr(settings, "port"):
        settings.port = port
    if mount_path and hasattr(settings, "mount_path"):
        settings.mount_path = mount_path
    if streamable_http_path and hasattr(settings, "streamable_http_path"):
        settings.streamable_http_path = streamable_http_path
    if sse_path and hasattr(settings, "sse_path"):
        settings.sse_path = sse_path


@mcp.tool()
def detect_tech_stack(
    target: str,
    branch: str = "main",
    git_auth_token: str | None = None,
    git_auth_username: str = "oauth2",
) -> dict[str, Any]:
    """Tool MCP untuk mendeteksi stack aplikasi.

    Parameter:
    - target: path lokal atau URL Git repo.
    - branch: branch saat target berupa URL Git.
    - git_auth_token: token opsional untuk clone repo private via HTTPS.
    - git_auth_username: username auth HTTP Git (default: oauth2 untuk GitLab).
    """

    maybe_path = Path(target)
    if maybe_path.exists():
        detection = _detect_stack(maybe_path)
        return {
            "target": str(maybe_path.resolve()),
            "mode": "local",
            "detection": detection,
        }

    with tempfile.TemporaryDirectory(prefix="mcp-stack-detect-") as tmp_dir:
        clone_dir = Path(tmp_dir) / "repo"
        effective_target, redactions = _prepare_repo_url(
            repo_url=target,
            git_auth_token=git_auth_token,
            git_auth_username=git_auth_username,
        )
        clone_log = _run_command(
            ["git", "clone", "--depth", "1", "--branch", branch, effective_target, str(clone_dir)],
            redactions=redactions,
        )
        detection = _detect_stack(clone_dir)
        return {
            "target": target,
            "mode": "remote",
            "clone": clone_log,
            "detection": detection,
        }


@mcp.tool()
def deploy_gitlab_app(
    repo_url: str,
    app_name: str,
    host: str,
    path_prefix: str,
    branch: str = "main",
    deployment_root: str = DEFAULT_DEPLOYMENT_ROOT,
    repo_subdir: str = "",
    traefik_network: str = TRAEFIK_NETWORK_FIXED,
    traefik_entrypoint: str = "websecure",
    env_vars: dict[str, str] | None = None,
    git_auth_token: str | None = None,
    git_auth_username: str = "oauth2",
    run_compose: bool = True,
    force_rebuild: bool = True,
) -> dict[str, Any]:
    """Tool MCP untuk deploy aplikasi GitLab ke Docker + Traefik PathPrefix.

    Alur:
    1. Clone/pull repo.
    2. Deteksi stack.
    3. Generate Dockerfile + compose.
    4. (Opsional) `docker compose up -d --build`.

    Catatan auth:
    - Untuk repo private via HTTPS, isi `git_auth_token` atau set env
      `GITLAB_ACCESS_TOKEN` pada service MCP.

    Catatan Traefik:
    - `host` (domain) wajib diisi.
    - `path_prefix` wajib diisi.
    - Network app target selalu dipaksa ke `web`.
    - Root deploy selalu dipaksa ke `<GITLAB_DEPLOYMENT_ROOT>/<app_name>`.
      Default fallback: `/home/ubuntu/apps/deploy/<app_name>`.
    - Default entrypoint: `websecure`.
    """

    normalized_name = _slugify_app_name(app_name)
    normalized_host = _normalize_deploy_host(host)
    normalized_prefix = _normalize_path_prefix(path_prefix)
    requested_root = Path(deployment_root).expanduser().resolve()
    root_path = _resolve_persistent_deployment_root()
    effective_traefik_network = TRAEFIK_NETWORK_FIXED
    app_path = root_path / normalized_name
    repo_path = app_path / "repo"

    app_path.mkdir(parents=True, exist_ok=True)

    effective_repo_url, redactions = _prepare_repo_url(
        repo_url=repo_url,
        git_auth_token=git_auth_token,
        git_auth_username=git_auth_username,
    )
    clone_logs = _clone_or_update_repo(
        repo_url=effective_repo_url,
        branch=branch,
        repo_dir=repo_path,
        redactions=redactions,
    )

    source_path = repo_path
    cleaned_subdir = _normalize_repo_subdir(repo_subdir)
    if cleaned_subdir:
        source_path = repo_path / cleaned_subdir
    _path_exists_or_raise(source_path, "Source repo_subdir")

    detection = _detect_stack(source_path)
    dockerfile_content = _render_dockerfile(detection, cleaned_subdir)
    compose_content = _render_compose(
        app_name=normalized_name,
        path_prefix=normalized_prefix,
        host=normalized_host,
        traefik_network=effective_traefik_network,
        traefik_entrypoint=traefik_entrypoint,
        internal_port=detection["internal_port"],
        env_vars=env_vars,
    )
    dockerignore_content = _render_dockerignore()

    files_written = []

    dockerfile_path = app_path / "Dockerfile"
    dockerfile_path.write_text(dockerfile_content + "\n", encoding="utf-8")
    files_written.append(str(dockerfile_path))

    compose_path = app_path / "docker-compose.yml"
    compose_path.write_text(compose_content, encoding="utf-8")
    files_written.append(str(compose_path))

    dockerignore_path = app_path / ".dockerignore"
    dockerignore_path.write_text(dockerignore_content, encoding="utf-8")
    files_written.append(str(dockerignore_path))

    env_path = app_path / ".env"
    if env_vars:
        env_body = "\n".join(f"{key}={value}" for key, value in sorted(env_vars.items())) + "\n"
        env_path.write_text(env_body, encoding="utf-8")
        files_written.append(str(env_path))

    deploy_log = None
    if run_compose:
        deploy_log = _run_docker_compose(app_path=app_path, force_rebuild=force_rebuild)

    return {
        "app_name": normalized_name,
        "host": normalized_host,
        "path_prefix": normalized_prefix,
        "branch": branch,
        "deployment_root": str(root_path),
        "traefik_network": effective_traefik_network,
        "app_path": str(app_path),
        "source_path": str(source_path),
        "detection": detection,
        "files_written": files_written,
        "run_compose": run_compose,
        "deploy_log": deploy_log,
        "clone_logs": clone_logs,
        "notes": [
            "deploy_gitlab_app mewajibkan host/domain dan path_prefix diisi sebelum deploy.",
            (
                f"Parameter deployment_root='{requested_root}' diabaikan; "
                f"root deploy dipaksa ke env GITLAB_DEPLOYMENT_ROOT -> '{root_path}' agar persistent di host."
                if requested_root != root_path
                else f"Root deploy persistent: '{root_path}'."
            ),
            (
                f"Parameter traefik_network='{traefik_network}' diabaikan; "
                f"network dipaksa ke '{effective_traefik_network}'."
                if traefik_network != effective_traefik_network
                else f"Network Traefik fixed: '{effective_traefik_network}'."
            ),
            "Pastikan Traefik berjalan dan terhubung ke network yang sama.",
            "Untuk aplikasi dengan kebutuhan command khusus, override Dockerfile hasil generate.",
        ],
    }


def main() -> None:
    """Entrypoint CLI server MCP.

    Mendukung dua mode utama:
    - stdio (default, untuk client lokal)
    - streamable-http (untuk akses via reverse proxy seperti Traefik)
    """
    parser = argparse.ArgumentParser(description="GitLab Deploy MCP server")
    parser.add_argument("--transport", default=os.getenv("MCP_TRANSPORT", "stdio"))
    parser.add_argument("--host", default=os.getenv("MCP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_PORT", "8000")))
    parser.add_argument("--mount-path", default=os.getenv("MCP_MOUNT_PATH", ""))
    parser.add_argument("--streamable-http-path", default=os.getenv("MCP_STREAMABLE_HTTP_PATH", "/mcp"))
    parser.add_argument("--sse-path", default=os.getenv("MCP_SSE_PATH", "/sse"))
    args = parser.parse_args()

    mount_path = _normalize_mount_path(args.mount_path)
    streamable_http_path = _normalize_mount_path(args.streamable_http_path) or "/mcp"
    sse_path = _normalize_mount_path(args.sse_path) or "/sse"

    _apply_runtime_settings(
        host=args.host,
        port=args.port,
        mount_path=mount_path,
        streamable_http_path=streamable_http_path,
        sse_path=sse_path,
    )

    run_kwargs = _build_run_kwargs(
        transport=args.transport,
        host=args.host,
        port=args.port,
        mount_path=mount_path,
        streamable_http_path=streamable_http_path,
        sse_path=sse_path,
    )
    mcp.run(**run_kwargs)


if __name__ == "__main__":
    main()
