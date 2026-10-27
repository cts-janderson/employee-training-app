# Local development

This setup is for synthetic-data development on one machine. The [README](../README.md) has the shortest path to a running app; this page covers configuration and separate processes.

## Prerequisites and database

Install Python 3.12 or newer and PostgreSQL 14 or newer. With PostgreSQL running, create a local database that your account can access:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
createdb employee_training
export DATABASE_URL=postgresql://localhost:5432/employee_training
```

If you use a different database name, user, host, or port, export a matching `DATABASE_URL`, for example `postgresql://user:password@localhost:5432/employee_training`. Keep credentials in your shell or a local untracked environment file, not in repository files. [.env.example](../.env.example) lists configurable values; Django does not load that file automatically.

## Start the app

```bash
./scripts/start-local.sh
```

The script sets `DJANGO_DEBUG=1`, `DEV_AUTH_STUB=1`, and loopback-only allowed hosts. It supplies the default `DATABASE_URL` if none is exported, applies all migrations, creates the `.test` demo administrator, and starts Django at <http://127.0.0.1:8000/>. Visit `/health/` for a simple health response. The demo actor switcher and protected pages require a loopback request; they are local stand-ins for identity integration.

You can run the steps separately when diagnosing startup:

```bash
export DATABASE_URL=postgresql://localhost:5432/employee_training
export DJANGO_DEBUG=1 DEV_AUTH_STUB=1
.venv/bin/python manage.py migrate
.venv/bin/python manage.py bootstrap_demo_admin
.venv/bin/python manage.py check
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

Use the startup script for normal local use: it also sets `DJANGO_ALLOWED_HOSTS` to loopback values. To use the manual commands, retain the default allowed hosts or set them to `localhost,127.0.0.1`. The demo stub is rejected if debug is off or allowed hosts include a non-loopback host.

## Seed and explore

Apply migrations first, then run:

```bash
DJANGO_DEBUG=1 DEV_AUTH_STUB=1 .venv/bin/python manage.py seed_demo
```

The command requires a `.test` `DEV_ADMIN_EMAIL` (the default is `demo.admin@example.test`). It creates only synthetic data, does not send email, and is safe to rerun: if its training already exists, it leaves the seed and later edits untouched. Use a fresh database to reset the scenarios. The [demo guide](demo-guide.md) describes the included assignments and a walkthrough.

## Notification worker

In another terminal, set the same `DATABASE_URL` as the app (adjust the example if needed) and run:

```bash
export DATABASE_URL=postgresql://localhost:5432/employee_training
DJANGO_DEBUG=1 DEV_AUTH_STUB=1 .venv/bin/python manage.py dispatch_notifications --loop
```

Omit `--loop` to process one batch and exit. The worker requires debug mode, the demo stub, and Django's in-memory email backend. Assignment notices, deadline changes, scheduled reminders, and manual nudges are stored as notification records in PostgreSQL before dispatch. The **Notifications** pages show attempts, suppression, blocked addresses, and retries. The in-memory mailbox ends with the worker process; it is not an external delivery service.

Scheduled reminders use 09:00 in `ORG_TIME_ZONE` (default `America/New_York`), three calendar days before and on the due date. The assignment deadline is the start of the following day in that time zone. The worker rechecks eligibility and expiration before dispatch. A manual nudge queues a request; the notification detail shows whether delivery later succeeded.

## Checks and static assets

With PostgreSQL configured as above:

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py test
.venv/bin/python manage.py collectstatic --noinput
```

For a quick local test run without PostgreSQL, the Django test suite can use an ephemeral SQLite database:

```bash
DATABASE_URL=sqlite:///:memory: DJANGO_DEBUG=1 DEV_AUTH_STUB=1 .venv/bin/python manage.py test training
```

PostgreSQL-specific locking tests may be skipped on SQLite. `collectstatic` writes assets to the ignored `staticfiles/` directory. Before rollout, run the full suite and concurrency checks on the target PostgreSQL environment and complete the [release criteria](implementation-plan.md#mvp-release-criteria).
