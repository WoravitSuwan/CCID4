#!/bin/sh
# Summons system - start with Docker (PostgreSQL + Redis + LibreOffice inside containers). macOS / Linux.
#   ./start-docker.sh          build (first time) and start
#   ./start-docker.sh --no-build   start the image that is already built
cd "$(dirname "$0")" || exit 1
if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start Docker (Docker Desktop / 'sudo systemctl start docker') and try again." >&2
  exit 1
fi
sh tools/make_env.sh || exit 1
port=$(sed -n 's/^WEB_PORT=//p' .env | tr -d '\r'); port=${port:-8000}
if [ "$1" = "--no-build" ]; then
  docker compose up -d || exit 1
else
  echo "Building and starting containers (first time takes several minutes) ..."
  docker compose up -d --build || exit 1
fi
echo "Waiting for the web server ..."
i=0
until python3 -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:$port/healthz', timeout=3)" 2>/dev/null \
      || curl -fsS "http://localhost:$port/healthz" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 120 ]; then
    echo "The web server did not start. Logs:" >&2
    docker compose logs --tail 50 web >&2
    exit 1
  fi
  sleep 2
done
cat data/admin-password.txt 2>/dev/null
echo "System is running at http://localhost:$port   (stop: docker compose down)"
