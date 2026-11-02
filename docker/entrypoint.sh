#!/bin/sh
set -e

wait_for_db() {
  python - <<'PY'
import os, time, psycopg
for i in range(60):
    try:
        psycopg.connect(host=os.environ["POSTGRES_HOST"], port=os.environ.get("POSTGRES_PORT", "5432"),
                        dbname=os.environ.get("POSTGRES_DB", "summons"), user=os.environ.get("POSTGRES_USER", "summons"),
                        password=os.environ.get("POSTGRES_PASSWORD", "")).close()
        break
    except Exception as exc:
        print("waiting for database...", exc)
        time.sleep(2)
PY
}

case "$1" in
  web)
    wait_for_db
    python manage.py migrate --noinput
    python manage.py init_system
    exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers "${WEB_WORKERS:-3}" --timeout 300 \
      --access-logfile -
    ;;
  worker)
    wait_for_db
    exec celery -A config worker -B -l info --concurrency "${WORKER_CONCURRENCY:-2}" \
      --schedule /app/data/celerybeat-schedule
    ;;
  *)
    exec "$@"
    ;;
esac
