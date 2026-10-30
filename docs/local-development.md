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

The script sets `DJANGO_DEBUG=1`, `DEV_AUTH_STUB=1`, and loopback-only allowed hosts. It supplies the default `DATABASE_URL` if none is exported, applies all migrations, creates the `.test` demo administrator, starts the import-review cleanup loop, and starts Django at <http://localhost:8000/>. The cleanup loop stops when the startup script exits. Visit `/health/` for a simple health response. The demo actor switcher and protected pages require a loopback request; they are local stand-ins for identity integration.

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

## Try email and password sign-in

Sign-in, registration, approval, invitations and password reset work locally without an email provider ([Slice 29](plans/29-sign-in-skeleton.md)). Account emails go to Django's in-memory sink. With the demo stub enabled, each link is also printed in the `runserver` output as `Local verify link …`, `Local setup link …` or `Local reset link …`.

1. Create the first real administrator. It prompts for a password of at least 12 characters and refuses once a real administrator exists:

   ```bash
   .venv/bin/python manage.py create_administrator --name "Local Admin" --email admin@example.com
   ```

2. Sign in at `/account/sign-in/`. Real administrators manage applications and invitations at **Accounts** (`/accounts/`) and use the other administrator pages with real employees. Real and demo administrators don't see each other's employees, trainings, library quizzes or groups; see [Slice 31](plans/31-real-employee-workflows.md). Apply migration `0017` first. To work with demo data, sign out and use **Switch profile**.
3. Register a second address at `/account/register/`, open the printed verify link, then approve the application from **Accounts**.

4. Apply migration `0020` first. A network with more than 50 failed sign-ins in 24 hours gets an address block for 24 hours. Lift it on **Blocked addresses** (`/accounts/blocks/`, linked from **Accounts**) or from the shell. Networks are stored as `127.0.0.1/32`, or as the `/64` of an IPv6 client:

   ```bash
   .venv/bin/python manage.py unblock_address --list
   .venv/bin/python manage.py unblock_address 127.0.0.1
   ```

5. To try passkeys, set `APP_BASE_URL=http://localhost:8000` and open the app at `localhost`, not `127.0.0.1`: WebAuthn refuses an IP address as the site. Add a passkey under **Your account** as a real administrator. In Chrome, DevTools → **WebAuthn** → **Enable virtual authenticator environment** gives a passkey without a physical device. `manage.py remove_passkeys --email …` removes an administrator's passkeys.

Real accounts can't use the demo switcher, and demo profiles can't sign in with a password.


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

Omit `--loop` to process one batch and exit. Locally the worker requires debug mode, the demo stub, and Django's in-memory email backend; in production it sends through Microsoft Graph ([ADR-0011](adr/0011-email-through-microsoft-graph.md)). Assignment notices, deadline changes, scheduled reminders, and manual nudges are stored as notification records in PostgreSQL before dispatch. The **Notifications** pages show attempts, suppression, blocked addresses, and retries. The in-memory mailbox ends with the worker process; it is not an external delivery service.

Scheduled reminders use 09:00 in `ORG_TIME_ZONE` (default `America/New_York`), three calendar days before and on the due date. The assignment deadline is the start of the following day in that time zone. The worker rechecks eligibility and expiration before dispatch. A manual nudge queues a request; the notification detail shows whether delivery later succeeded.

## Checks and static assets

With PostgreSQL running, run all local checks using:

```bash
./scripts/test-local.sh
```

The script uses `postgresql://localhost:5432/employee_training` unless you export a different `DATABASE_URL`. It requires PostgreSQL so the concurrency tests run, sets the local demo environment, and stops at the first failed command. It checks Django configuration and missing migrations, runs the full test suite with verbose output, and builds static assets. A successful run ends with `All checks passed.`

Django creates and removes a separate test database (`test_employee_training` with the default connection), so your PostgreSQL account needs permission to create databases. The script does not apply migrations to your development database.

To run the checks separately with the database and demo environment configured as above:

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test --verbosity 2
.venv/bin/python manage.py collectstatic --noinput
```

For a quick local test run without PostgreSQL, the Django test suite can use an ephemeral SQLite database:

```bash
DATABASE_URL=sqlite:///:memory: DJANGO_DEBUG=1 DEV_AUTH_STUB=1 .venv/bin/python manage.py test training
```

PostgreSQL-specific locking tests may be skipped on SQLite. `collectstatic` writes assets to the ignored `staticfiles/` directory. Before rollout, run the full suite and concurrency checks on the target PostgreSQL environment and complete the [release criteria](implementation-plan.md#mvp-release-criteria).

## Training draft import cleanup

The training library's **Create content → Import training drafts** accepts up to five UTF-8 Markdown/text files of 1 MB each. Review title edits, slide previews, warnings, and removals before creating drafts. The one-hour review is owned by the current synthetic administrator; draft creation is atomic and replay-safe. See [ADR-0007](adr/0007-short-lived-training-import-review.md).

The normal startup script runs cleanup every minute. If running Django manually, run the cleanup loop in another terminal using the same database:

```bash
DJANGO_DEBUG=1 DEV_AUTH_STUB=1 .venv/bin/python manage.py cleanup_training_imports --loop
```

Omit `--loop` for a single cleanup pass. Expired uncommitted reviews and committed result records older than seven days are removed; created trainings remain. Raw uploads are never permanently saved. Configure a scheduled cleanup job before introducing any non-local deployment; the existing real-data gate still applies.

## Completion reports

Use **Reports** for summaries by training or employee, then open a training for assignment cycles or version-specific slide acknowledgments. **Assignments** retains its workload and quiz-outcome charts and operational actions. Default completion counts pending and completed nonarchived cycles for active synthetic employees. Renewals count separately; history populations are selected explicitly. Search, activity and version filters apply to the full population and matching CSV export before pagination.

For the creation control's JavaScript event regression checks, use Node's built-in test runner (no npm dependencies):

```bash
node --test training/js_tests/create-menu.test.cjs
```

These checks exercise disclosure state and dismissal behavior without opening a browser. They complement the CSRF-enforced Django creation workflow tests and do not replace rendering or assistive-technology review.


## Document authoring

Apply migration 0015 before starting the app. **Create content → Create training** opens the document workspace. Add sections/documents, save explicitly, then review saved content before publication. Existing slide drafts offer **Convert saved draft to documents**; library revision actions create document drafts while retaining the published slide version and its evidence.

The editor bundle is checked in, so normal Django setup does not need Node or a CDN. To change the editor source, use Node 22 or newer:

```bash
npm ci
npm run build:editor
npm test
```

Commit the source, lockfile, generated bundle, and bundled third-party notices together. The server validates the structured body independently. The authoring interface offers a structured-content fallback if JavaScript fails; it does not silently substitute an empty document.

New file selections import one document per training, preserving headings inside it. Existing uncommitted slide import batches keep their recorded format. Reports retain By slide for legacy versions and add By document for document versions. Assignment exports add content format and document evidence columns while retaining existing slide columns.
