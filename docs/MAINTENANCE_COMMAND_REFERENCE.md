# Maintenance Command Reference

Dokumen ini berisi command praktis untuk maintain `src/gitlab_deploy_mcp/server.py`.

## Prasyarat

```powershell
cd d:\laragon\www\MCP\gitlab-deploy-mcp
$env:PYTHONPATH = "src"
```

## Variable Global dan Environment

- `mcp`:
  - Cek object server:
  ```powershell
  python -c "from gitlab_deploy_mcp.server import mcp; print(type(mcp).__name__)"
  ```

- `DEFAULT_DEPLOYMENT_ROOT` (turunan env `GITLAB_DEPLOYMENT_ROOT`):
  - Cek nilai default:
  ```powershell
  python -c "from gitlab_deploy_mcp.server import DEFAULT_DEPLOYMENT_ROOT; print(DEFAULT_DEPLOYMENT_ROOT)"
  ```
  - Override value:
  ```powershell
  $env:GITLAB_DEPLOYMENT_ROOT = "D:/deployments"
  python -c "from gitlab_deploy_mcp.server import DEFAULT_DEPLOYMENT_ROOT; print(DEFAULT_DEPLOYMENT_ROOT)"
  ```

- Runtime env untuk `main()`:
  - `MCP_TRANSPORT`, `MCP_HOST`, `MCP_PORT`, `MCP_MOUNT_PATH`, `MCP_STREAMABLE_HTTP_PATH`, `MCP_SSE_PATH`
  - Contoh run:
  ```powershell
  $env:MCP_TRANSPORT = "streamable-http"
  $env:MCP_HOST = "0.0.0.0"
  $env:MCP_PORT = "8000"
  $env:MCP_STREAMABLE_HTTP_PATH = "/mcp"
  gitlab-deploy-mcp
  ```

- Runtime env untuk clone repo private:
  - `GITLAB_ACCESS_TOKEN`
  - Contoh cek:
  ```powershell
  $env:GITLAB_ACCESS_TOKEN = "glpat-xxx"
  python -c "from gitlab_deploy_mcp.server import _prepare_repo_url; print(_prepare_repo_url('https://gitlab.com/group/repo.git', None, 'oauth2')[0])"
  ```

## Command per Function

### Helper command utilities

- `_run_command`:
```powershell
python -c "from gitlab_deploy_mcp.server import _run_command; print(_run_command(['git','--version']))"
```

- `_slugify_app_name`:
```powershell
python -c "from gitlab_deploy_mcp.server import _slugify_app_name; print(_slugify_app_name('My Demo App'))"
```

- `_normalize_path_prefix`:
```powershell
python -c "from gitlab_deploy_mcp.server import _normalize_path_prefix; print(_normalize_path_prefix('gitlab-deploy/'))"
```

- `_normalize_mount_path`:
```powershell
python -c "from gitlab_deploy_mcp.server import _normalize_mount_path; print(_normalize_mount_path('mcp/'))"
```

- `_normalize_repo_subdir`:
```powershell
python -c "from gitlab_deploy_mcp.server import _normalize_repo_subdir; print(_normalize_repo_subdir('services/api'))"
```

### Helper file parsing

- `_load_json_file`:
```powershell
python -c "from pathlib import Path; from gitlab_deploy_mcp.server import _load_json_file; print(_load_json_file(Path('examples/codex.mcp.json')))"
```

- `_read_text_if_exists`:
```powershell
python -c "from pathlib import Path; from gitlab_deploy_mcp.server import _read_text_if_exists; print(_read_text_if_exists(Path('README.md'))[:120])"
```

### Helper detection dan render

- `_detect_stack`:
```powershell
python -c "from pathlib import Path; from gitlab_deploy_mcp.server import _detect_stack; print(_detect_stack(Path('.')))"
```

- `_render_dockerfile`:
```powershell
python -c "from gitlab_deploy_mcp.server import _render_dockerfile; d={'framework':'nodejs','stack':'nodejs','start_command':'npm run start'}; print(_render_dockerfile(d,''))"
```

- `_render_compose`:
```powershell
python -c "from gitlab_deploy_mcp.server import _render_compose; print(_render_compose('demo','/demo',None,'web','websecure',3000,{'APP_ENV':'prod'}))"
```

- `_render_dockerignore`:
```powershell
python -c "from gitlab_deploy_mcp.server import _render_dockerignore; print(_render_dockerignore())"
```

### Helper git/deploy/runtime

- `_clone_or_update_repo`:
```powershell
python -c "from pathlib import Path; from gitlab_deploy_mcp.server import _clone_or_update_repo; print(_clone_or_update_repo('https://gitlab.com/group/repo.git','main',Path('./tmp-repo')))"
```

- `_path_exists_or_raise`:
```powershell
python -c "from pathlib import Path; from gitlab_deploy_mcp.server import _path_exists_or_raise; _path_exists_or_raise(Path('.'),'workspace-ok'); print('ok')"
```

- `_run_docker_compose`:
```powershell
python -c "from pathlib import Path; from gitlab_deploy_mcp.server import _run_docker_compose; print(_run_docker_compose(Path('./deployments/sample'), False))"
```

- `_build_run_kwargs`:
```powershell
python -c "from gitlab_deploy_mcp.server import _build_run_kwargs; print(_build_run_kwargs('streamable-http','0.0.0.0',8000,'','/mcp','/sse'))"
```

- `_apply_runtime_settings`:
```powershell
python -c "from gitlab_deploy_mcp.server import _apply_runtime_settings; _apply_runtime_settings('0.0.0.0',8000,'','/mcp','/sse'); print('settings applied')"
```

### Tool MCP (publik)

- `detect_tech_stack` (direct python call):
```powershell
python -c "from gitlab_deploy_mcp.server import detect_tech_stack; print(detect_tech_stack('.', 'main'))"
```

- `detect_tech_stack` (repo private):
```powershell
python -c "from gitlab_deploy_mcp.server import detect_tech_stack; print(detect_tech_stack('https://gitlab.com/group/repo.git','main','glpat-xxx','oauth2'))"
```

- `deploy_gitlab_app` (direct python call):
```powershell
python -c "from gitlab_deploy_mcp.server import deploy_gitlab_app; print(deploy_gitlab_app(repo_url='https://gitlab.com/group/repo.git', app_name='demo', path_prefix='/demo', run_compose=False))"
```

- `deploy_gitlab_app` (repo private):
```powershell
python -c "from gitlab_deploy_mcp.server import deploy_gitlab_app; print(deploy_gitlab_app(repo_url='https://gitlab.com/group/private-repo.git', app_name='demo', path_prefix='/demo', git_auth_token='glpat-xxx', run_compose=False))"
```

- `main` (entrypoint server):
```powershell
gitlab-deploy-mcp --transport stdio
```

## Command Operasional Standar

- Validasi syntax:
```powershell
python -m compileall src
```

- Jalankan service dengan Traefik:
```powershell
docker compose -f docker-compose.traefik.yml up -d --build
```

- Cek logs service MCP:
```powershell
docker compose -f docker-compose.traefik.yml logs -f gitlab-deploy-mcp
```
