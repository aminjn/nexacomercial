#!/usr/bin/env bash
# One-shot installer for a fresh Ubuntu 22.04/24.04 server (e.g. ArvanCloud).
# Usage:  sudo bash deploy/install.sh
# Docker's official download site blocks Iranian IPs, so Docker comes from the Ubuntu repos and
# images are pulled through a registry mirror (default: ArvanCloud's). Override with REGISTRY_MIRROR=...
set -euo pipefail

REGISTRY_MIRROR="${REGISTRY_MIRROR:-https://docker.arvancloud.ir}"
# Local Ollama model to pull ("" = skip Ollama and use an external API instead)
OLLAMA_MODEL="${OLLAMA_MODEL-qwen2.5:7b}"
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
  if [ -n "$OLLAMA_MODEL" ]; then
    sed -i "s|^APP_LLM_PROVIDER=.*|APP_LLM_PROVIDER=openai|; s|^APP_LLM_BASE_URL=.*|APP_LLM_BASE_URL=http://ollama:11434/v1|; s|^APP_LLM_MODEL=.*|APP_LLM_MODEL=$OLLAMA_MODEL|" .env
  fi
fi

mkdir -p data
echo "==> building and starting"
PROFILE=""
[ -n "$OLLAMA_MODEL" ] && PROFILE="--profile ollama"
$DC $PROFILE up -d --build

if [ -n "$OLLAMA_MODEL" ]; then
  echo "==> downloading model $OLLAMA_MODEL (several GB, takes a while)"
  $DC exec -T ollama ollama pull "$OLLAMA_MODEL"
  echo "==> quick model test"
  $DC exec -T ollama ollama run "$OLLAMA_MODEL" "در یک جمله سلام کن" || true
fi

IP="$(curl -s --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')"
echo
echo "Done. Dashboard: http://$IP:${HOST_PORT:-8020}"
echo "Restart later with:  cd $APP_DIR && $DC $PROFILE up -d"
