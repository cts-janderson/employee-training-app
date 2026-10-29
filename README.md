# Employee Training

A Django application for authoring versioned training, assigning it to employees, tracking completion, and reviewing quiz and notification history. This repository currently supports **local demonstrations with synthetic employees**. It is not ready for real employee data or internal rollout.

Administrators can publish training with nested documents, rich text, and optional quizzes (legacy slide versions remain supported), assign a published version to employees or groups, review progress, and manage deadlines, renewals, waivers, and reminders. Learners can resume assigned training, acknowledge documents or legacy slides, take or retry quizzes, and review their own history. The four quiz formats are single choice, multiple selection, true or false, and ordering.

## Quick start

You need Python 3.12 or newer and PostgreSQL 14 or newer. Start a local PostgreSQL server and create an empty database accessible to your local user:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
createdb employee_training
export DATABASE_URL=postgresql://localhost:5432/employee_training
./scripts/start-local.sh
```

The script applies all migrations, creates a synthetic administrator, and serves the app at <http://127.0.0.1:8000/>. It uses `postgresql://localhost:5432/employee_training` by default. If your connection differs, export a matching `DATABASE_URL` before running it. The unauthenticated health endpoint is <http://127.0.0.1:8000/health/>.

To explore the populated workflows, stop the server, seed the same local database, and restart it:

```bash
DJANGO_DEBUG=1 DEV_AUTH_STUB=1 .venv/bin/python manage.py seed_demo
./scripts/start-local.sh
```

The seed adds six synthetic learners, two groups, published trainings, a draft, a library quiz, and assignments in different states. Use **Switch demo employee** in the app to inspect learner views. See the [demo guide](docs/demo-guide.md) for suggested scenarios.

For setup variations, configuration, the notification worker, and checks, see [local development](docs/local-development.md).

With PostgreSQL running, run configuration and migration checks, the full test suite (including concurrency tests), and the static asset build:

```bash
./scripts/test-local.sh
```

## Current scope and rollout status

- **Implemented locally:** versioned document and legacy slide authoring; document/slide acknowledgment; quiz grading and retries; individual and group assignment; completion reports by training/employee with matching CSV exports; reviewed Markdown/text draft import; renewal review; assignment cancellation, archiving, and waiver; durable notification intents, scheduled reminders, and audited manual nudges.
- **Development integrations:** a loopback-only demo actor switcher stands in for sign-in. The notification worker uses Django's in-memory email sink and synthetic `.test` addresses; it does not send external email.
- **Before real use:** implement [email and password authentication with enrollment approval](docs/plans/email-password-authentication.md) and connect an approved email provider, prove deployment and worker operation, verify PostgreSQL migrations and concurrency, and complete the operational owner and employee-data retention/access approvals in the [MVP release criteria](docs/implementation-plan.md#mvp-release-criteria). Keep real employee data out until those gates are met.

The [MVP requirements](docs/mvp.md) define confirmed stories `US-01`–`US-12`. [Extended stories](docs/user-stories.md) record locally implemented additions beyond that baseline. Local implementation does not by itself complete the release criteria.

## Documentation

| Document | Purpose |
| --- | --- |
| [Local development](docs/local-development.md) | Environment, database, worker, Python and JavaScript tests, editor bundle, and static assets |
| [Demo guide](docs/demo-guide.md) | Seed data and administrator-to-learner walkthrough |
| [User stories](docs/user-stories.md) | Confirmed MVP and proposed extension stories |
| [Authentication plan](docs/plans/email-password-authentication.md) | Deferred email/password sign-in, self-registration with approval, and administrator-created employees |
| [MVP requirements](docs/mvp.md) and [implementation plan](docs/implementation-plan.md) | Product behavior, delivery slices, and release gates |
| [Glossary](docs/GLOSSARY.md) and [architecture decisions](docs/adr/) | Domain terms and consequential design choices |
| [HCI review](docs/hci-review.md), [implementation plan](docs/hci-implementation-plan.md), and [validation](docs/hci-validation.md) | Interface findings, changes, and remaining human review |

The Django project is in `config/`; application code and tests are in `training/`; templates are in `templates/training/`. Dependencies are declared in `pyproject.toml`; document editor dependencies are in `package.json`.
