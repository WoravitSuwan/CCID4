#!/bin/sh
# Forgot the admin password / cannot log in: set a new one (the system must be running: ./start-docker.sh).
cd "$(dirname "$0")" && docker compose exec web python manage.py reset_admin "$@"
