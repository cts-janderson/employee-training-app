#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ ! -x .venv/bin/python ]]; then
  echo "Install dependencies in .venv before starting the local app." >&2
  exit 1
fi

export DATABASE_URL="${DATABASE_URL:-postgresql://localhost:5432/employee_training}"
export DJANGO_DEBUG=1
export DEV_AUTH_STUB=1
export DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1

.venv/bin/python manage.py migrate
.venv/bin/python manage.py bootstrap_demo_admin
exec .venv/bin/python manage.py runserver 127.0.0.1:8000
