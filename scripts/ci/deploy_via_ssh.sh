#!/usr/bin/env sh
set -eu

require_var() {
  var_name="$1"
  eval "var_value=\${$var_name:-}"
  if [ -z "$var_value" ]; then
    echo "Missing CI variable: $var_name"
    exit 1
  fi
}

require_var SSH_HOST
require_var SSH_USER
require_var SSH_PRIVATE_KEY

SSH_PORT="${SSH_PORT:-22}"
REMOTE_DIR="${DEPLOY_PATH:-/home/$SSH_USER/apps/gitlab-deploy-mcp}"

TRAEFIK_NETWORK="${TRAEFIK_NETWORK:-web}"
TRAEFIK_ENTRYPOINT="${TRAEFIK_ENTRYPOINT:-websecure}"
TRAEFIK_ROUTER_RULE="${TRAEFIK_ROUTER_RULE:-PathPrefix(\`/gitlab-deploy\`)}"

DEPLOYMENT_DATA_DIR="${DEPLOYMENT_DATA_DIR:-./data/deployments}"
GITLAB_DEPLOYMENT_ROOT="${GITLAB_DEPLOYMENT_ROOT:-/data/deployments}"

MCP_TRANSPORT="${MCP_TRANSPORT:-streamable-http}"
MCP_HOST="${MCP_HOST:-0.0.0.0}"
MCP_PORT="${MCP_PORT:-8000}"
MCP_STREAMABLE_HTTP_PATH="${MCP_STREAMABLE_HTTP_PATH:-/mcp}"
MCP_MOUNT_PATH="${MCP_MOUNT_PATH:-}"
MCP_SSE_PATH="${MCP_SSE_PATH:-/sse}"
GITLAB_ACCESS_TOKEN="${GITLAB_ACCESS_TOKEN:-}"

mkdir -p "$HOME/.ssh"
printf "%s\n" "$SSH_PRIVATE_KEY" > "$HOME/.ssh/id_ed25519"
chmod 600 "$HOME/.ssh/id_ed25519"
ssh-keyscan -p "$SSH_PORT" -H "$SSH_HOST" >> "$HOME/.ssh/known_hosts" 2>/dev/null || true

ssh -i "$HOME/.ssh/id_ed25519" -p "$SSH_PORT" "$SSH_USER@$SSH_HOST" "mkdir -p '$REMOTE_DIR'"

DEPLOYMENT_DATA_DIR_REL="$(printf '%s' "$DEPLOYMENT_DATA_DIR" | sed 's#^\./##' | sed 's#/$##')"
DEPLOYMENT_DATA_PARENT="$(dirname "$DEPLOYMENT_DATA_DIR_REL")"

if [ -n "$DEPLOYMENT_DATA_DIR_REL" ] && [ "${DEPLOYMENT_DATA_DIR_REL#/}" = "$DEPLOYMENT_DATA_DIR_REL" ]; then
  if [ "$DEPLOYMENT_DATA_PARENT" != "." ] && [ "$DEPLOYMENT_DATA_PARENT" != "/" ]; then
    rsync -az --delete \
      --filter="P $DEPLOYMENT_DATA_PARENT/" \
      --filter="P $DEPLOYMENT_DATA_DIR_REL/" \
      --exclude='.git' \
      --exclude='.github' \
      --exclude='.gitlab' \
      --exclude='.env' \
      -e "ssh -i $HOME/.ssh/id_ed25519 -p $SSH_PORT" \
      ./ "$SSH_USER@$SSH_HOST:$REMOTE_DIR/"
  else
    rsync -az --delete \
      --filter="P $DEPLOYMENT_DATA_DIR_REL/" \
      --exclude='.git' \
      --exclude='.github' \
      --exclude='.gitlab' \
      --exclude='.env' \
      -e "ssh -i $HOME/.ssh/id_ed25519 -p $SSH_PORT" \
      ./ "$SSH_USER@$SSH_HOST:$REMOTE_DIR/"
  fi
else
  rsync -az --delete \
    --exclude='.git' \
    --exclude='.github' \
    --exclude='.gitlab' \
    --exclude='.env' \
    -e "ssh -i $HOME/.ssh/id_ed25519 -p $SSH_PORT" \
    ./ "$SSH_USER@$SSH_HOST:$REMOTE_DIR/"
fi

cat > .env.deploy <<EOF
TRAEFIK_NETWORK=$TRAEFIK_NETWORK
TRAEFIK_ENTRYPOINT=$TRAEFIK_ENTRYPOINT
TRAEFIK_ROUTER_RULE=$TRAEFIK_ROUTER_RULE
DEPLOYMENT_DATA_DIR=$DEPLOYMENT_DATA_DIR
GITLAB_DEPLOYMENT_ROOT=$GITLAB_DEPLOYMENT_ROOT
MCP_TRANSPORT=$MCP_TRANSPORT
MCP_HOST=$MCP_HOST
MCP_PORT=$MCP_PORT
MCP_STREAMABLE_HTTP_PATH=$MCP_STREAMABLE_HTTP_PATH
MCP_MOUNT_PATH=$MCP_MOUNT_PATH
MCP_SSE_PATH=$MCP_SSE_PATH
GITLAB_ACCESS_TOKEN=$GITLAB_ACCESS_TOKEN
EOF

scp -i "$HOME/.ssh/id_ed25519" -P "$SSH_PORT" .env.deploy "$SSH_USER@$SSH_HOST:$REMOTE_DIR/.env"

ssh -i "$HOME/.ssh/id_ed25519" -p "$SSH_PORT" "$SSH_USER@$SSH_HOST" "REMOTE_DIR='$REMOTE_DIR' TRAEFIK_NETWORK='$TRAEFIK_NETWORK' sh -s" <<'EOF'
set -eu

cd "$REMOTE_DIR"

if docker info >/dev/null 2>&1; then
  docker_cmd() { docker "$@"; }
elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
  docker_cmd() { sudo -n docker "$@"; }
else
  echo "Cannot access Docker daemon from SSH session."
  echo "Fix: add SSH user to docker group or allow passwordless sudo for docker."
  exit 1
fi

if ! docker_cmd network inspect "$TRAEFIK_NETWORK" >/dev/null 2>&1; then
  docker_cmd network create "$TRAEFIK_NETWORK"
fi

retry() {
  max_attempts="$1"
  delay_seconds="$2"
  shift 2

  attempt=1
  while true; do
    if "$@"; then
      return 0
    fi

    if [ "$attempt" -ge "$max_attempts" ]; then
      echo "Command failed after ${attempt} attempts: $*"
      return 1
    fi

    echo "Attempt ${attempt} failed. Retrying in ${delay_seconds}s: $*"
    attempt=$((attempt + 1))
    sleep "$delay_seconds"
  done
}

# Warm-up pull untuk mengurangi kegagalan TLS timeout sesaat dari Docker Hub.
retry 5 20 docker_cmd pull python:3.11-slim

retry 5 20 docker_cmd compose -f docker-compose.traefik.yml build --pull
retry 3 10 docker_cmd compose -f docker-compose.traefik.yml up -d --remove-orphans
docker_cmd compose -f docker-compose.traefik.yml ps
EOF

rm -f .env.deploy
