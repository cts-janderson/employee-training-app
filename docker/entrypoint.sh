#!/bin/sh
# Starts one role of the application image; compose.yaml names the role for each service.
set -eu

role="${1:-web}"
[ "$#" -gt 0 ] && shift

case "$role" in
  migrate)
    exec python manage.py migrate --noinput
    ;;
  web)
    # Account emails still go out in the request (until plan 36 step 4): a token request, a send, and one refresh
    # and resend after a 401 can each take MAIL_TIMEOUT_SECONDS (15), so the timeout must outlast all four.
    exec gunicorn config.wsgi --bind 0.0.0.0:8000 --workers "${WEB_CONCURRENCY:-3}" --timeout 75 \
      --worker-tmp-dir /dev/shm --access-logfile - --logger-class config.gunicorn_logging.RedactingLogger
    ;;
  worker)
    exec python manage.py dispatch_notifications --loop
    ;;
  cleanup)
    # Import reviews, rate-limit counters, passkey challenges and registrations every minute; sessions hourly.
    exec python manage.py cleanup_training_imports --loop
    ;;
  healthcheck)
    # The first allowed host, because Django rejects any other Host header.
    exec python - <<'PY'
import os, sys, urllib.request
host = os.environ["DJANGO_ALLOWED_HOSTS"].split(",")[0].strip()
request = urllib.request.Request("http://127.0.0.1:8000/health/", headers={"Host": host})
try:
    with urllib.request.urlopen(request, timeout=5) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception as error:
    print(error, file=sys.stderr)
    sys.exit(1)
PY
    ;;
  manage)
    exec python manage.py "$@"
    ;;
  *)
    echo "Unknown role '$role'. Use migrate, web, worker, cleanup, healthcheck or manage." >&2
    exit 64
    ;;
esac
