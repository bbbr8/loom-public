#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v docker >/dev/null 2>&1; then
  echo 'Install and start Docker Desktop, then run this launcher again.' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo 'Start Docker Desktop, then run this launcher again.' >&2
  exit 1
fi
if [ ! -e .env ]; then
  key=$(openssl rand -hex 32)
  (umask 077; set -o noclobber; printf 'LOOM_API_KEY=%s\n' "$key" > .env)
fi
docker compose up --build -d
ready=false
for attempt in {1..30}; do
  if docker compose exec -T loom python -c "import json, urllib.request; h=json.load(urllib.request.urlopen('http://127.0.0.1:10000/health',timeout=2)); assert h.get('ok') and h.get('service')=='loom'" >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
if [ "$ready" != true ]; then
  echo 'Loom has not become healthy. Inspect docker compose logs.' >&2
  exit 1
fi
docker compose exec -T loom python -B check_install.py
sed -n 's/^LOOM_API_KEY=//p' .env | head -n 1 | tr -d '\r\n' | pbcopy
echo 'Loom is ready. Paste the access key from your clipboard into the Access key field. Copy something else afterward to clear the clipboard.'
open 'http://127.0.0.1:10000'
