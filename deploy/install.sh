#!/usr/bin/env bash
# One-shot installer for a fresh Ubuntu 22.04/24.04 server (e.g. ArvanCloud).
# Usage:  sudo bash deploy/install.sh
# Docker's official download site blocks Iranian IPs, so Docker comes from the Ubuntu repos and
# images are pulled through a registry mirror (default: ArvanCloud's). Override with REGISTRY_MIRROR=...
set -euo pipefail

REGISTRY_MIRROR="${REGISTRY_MIRROR:-https://docker.arvancloud.ir}"
# Your Ollama server (outside Arvan) and a model already pulled there. Can also be set later in .env.
OLLAMA_URL="${OLLAMA_URL:-}"        # e.g. http://1.2.3.4:11434
OLLAMA_MODEL="${OLLAMA_MODEL:-}"    # e.g. qwen2.5:14b
OLLAMA_KEY="${OLLAMA_KEY:-}"        # only if a proxy in front of Ollama checks a Bearer token
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }

echo "==> installing docker from the Ubuntu repositories"
apt-get update -y
apt-get install -y docker.io curl openssl
apt-get install -y docker-compose-v2 2>/dev/null || apt-get install -y docker-compose

if [ -n "$REGISTRY_MIRROR" ]; then
  echo "==> using registry mirror $REGISTRY_MIRROR"
  mkdir -p /etc/docker
  cat > /etc/docker/daemon.json <<JSON
{ "registry-mirrors": ["$REGISTRY_MIRROR"] }
JSON
fi
systemctl enable docker
systemctl restart docker

if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi

if [ ! -f .env ]; then
  echo "==> creating .env with a random password and secret key"
  cp .env.example .env
  PASS="$(openssl rand -hex 12)"
  sed -i "s|^APP_ADMIN_PASSWORD=.*|APP_ADMIN_PASSWORD=$PASS|" .env
  sed -i "s|^APP_SECRET_KEY=.*|APP_SECRET_KEY=$(openssl rand -hex 32)|" .env
  echo "    dashboard user: admin   password: $PASS   (stored in $APP_DIR/.env)"
fi
[ -n "$OLLAMA_URL" ] && sed -i "s|^APP_LLM_BASE_URL=.*|APP_LLM_BASE_URL=$OLLAMA_URL|" .env
[ -n "$OLLAMA_MODEL" ] && sed -i "s|^APP_LLM_MODEL=.*|APP_LLM_MODEL=$OLLAMA_MODEL|" .env
[ -n "$OLLAMA_KEY" ] && sed -i "s|^APP_LLM_API_KEY=.*|APP_LLM_API_KEY=$OLLAMA_KEY|" .env

mkdir -p data
echo "==> building and starting"
$DC up -d --build

echo "==> testing the connection to Ollama"
sleep 3
$DC exec -T nexa python -m app.cli llm-test || echo "!! AI connection failed: check APP_LLM_* in .env (see README)"

IP="$(curl -s --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')"
echo
echo "Done. Dashboard: http://$IP:${HOST_PORT:-8020}"
echo "After editing .env:  cd $APP_DIR && $DC up -d"
