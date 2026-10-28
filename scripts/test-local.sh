#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ ! -x .venv/bin/python ]]; then
  echo "Install dependencies in .venv before running the checks. See README.md." >&2
  exit 1
fi

export DATABASE_URL="${DATABASE_URL:-postgresql://localhost:5432/employee_training}"
export DJANGO_DEBUG=1
export DEV_AUTH_STUB=1
export DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1

case "$DATABASE_URL" in
  postgres://*|postgresql://*|pgsql://*) ;;
  *)
    echo "Set DATABASE_URL to a PostgreSQL connection URL so all concurrency tests run." >&2
    exit 1
    ;;
esac

echo "Checking Django configuration..."
.venv/bin/python manage.py check

echo "Checking for missing migrations..."
.venv/bin/python manage.py makemigrations --check --dry-run

echo "Running the full test suite on PostgreSQL..."
.venv/bin/python manage.py test --verbosity 2

echo "Building static assets..."
.venv/bin/python manage.py collectstatic --noinput

echo "All checks passed."
