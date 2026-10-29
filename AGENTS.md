# Repository Guidelines

## Project Structure & Module Organization

This workspace is the project root. Planning documents live in `docs/`. The Django application lives in `config/` and `training/`, with templates in `templates/training/`; dependencies are declared in `pyproject.toml`. Local authoring, assignment, slide completion, and quiz attempts use synthetic employees until sign-in is connected.

- [mvp.md](docs/mvp.md): confirmed requirements and user stories `US-01`–`US-12`.
- [implementation-plan.md](docs/implementation-plan.md): eight delivery slices, acceptance criteria, setup gates, and release checks.
- [GLOSSARY.md](docs/GLOSSARY.md): canonical domain terminology.
- `docs/adr/`: architectural decisions, including assignment replacement and the application stack.
- [README.md](README.md): local setup and run commands.

## Build, Test, and Development Commands

With a local PostgreSQL database and environment configured as in [README.md](README.md):

- `.venv/bin/python -m pip install -e .` installs application dependencies.
- `.venv/bin/python manage.py migrate` applies database migrations.
- `.venv/bin/python manage.py bootstrap_demo_admin` creates the synthetic local administrator when debug and the stub are enabled.
- `.venv/bin/python manage.py create_administrator --name ... --email ...` creates the first real (password) administrator; see [sign-in skeleton](docs/plans/29-sign-in-skeleton.md).
- `.venv/bin/python manage.py check` checks Django configuration.
- `.venv/bin/python manage.py runserver` starts the local server.
- `.venv/bin/python manage.py dispatch_notifications --loop` runs the notification worker separately: the local sink with the demo stub, or Microsoft Graph when it's configured ([ADR-0011](docs/adr/0011-email-through-microsoft-graph.md)).
- `.venv/bin/python manage.py test` runs the Django tests. Settings load only when `DJANGO_DEBUG=1`, `DEV_AUTH_STUB=1`, `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1` and `DATABASE_URL` are exported; `./scripts/test-local.sh` sets them and runs the full check, test, and static build on PostgreSQL.
- `npm ci`, `npm run build:editor`, and `npm test` install editor dependencies, rebuild the checked-in document editor bundle, and run the JavaScript tests in `training/js_tests/`. See [document authoring](docs/local-development.md#document-authoring).
- `.venv/bin/python manage.py collectstatic --noinput` builds static assets.

Use the loopback-only demo actor switcher and local email sink for core-workflow development while identity and external delivery are deferred. Apply migrations through Slice 8 before running notifications, through `0015` before using text import or document authoring, through `0016` before using email and password sign-in, and through `0017` before real administrators use the administrator pages (each population keeps its own library; see [Slice 31](docs/plans/31-real-employee-workflows.md)), and through `0018` before sending email or running the notification worker. Record deployment, authentication, PostgreSQL migration and concurrency, and worker proof before internal rollout or introducing real employee data.

## Coding Style & Naming Conventions

Use Markdown headings, `-` bullets, language-labeled code fences, and relative links. Follow surrounding indentation. Name new documents with lowercase hyphenated filenames; preserve established names such as `GLOSSARY.md`. Number ADRs sequentially, for example `docs/adr/0002-descriptive-decision.md`.

Use glossary terms consistently. Keep glossary definitions free of implementation details; record consequential trade-offs in ADRs. Follow surrounding Python and Django style; no formatter or linter is configured yet.

## Testing Guidelines

For documentation changes, verify links, tables, Mermaid syntax, and agreement between requirements and implementation steps. Preserve story identifiers and update affected acceptance criteria when behavior changes.

Django's test runner runs the authoring, assignment, and quiz workflow tests; no coverage threshold exists yet. During implementation, follow the plan's domain tests for grading/deadlines, integration tests for permissions/transactions, and focused workflow tests. Use a controllable clock and email sink. Name tests after observable behavior.

## Commit & Pull Request Guidelines

The repository is on GitHub as `origin`. Use concise imperative summaries, such as `Clarify reminder expiry`.

PR descriptions should explain the behavior or documentation change, identify affected stories/slices, link relevant issues, and report verification performed. Include screenshots when changing visible interfaces.

## Architecture & Data Handling

Follow the confirmed plan's separation of training definitions, assignments, and execution evidence. Satisfy its real-data readiness gate before introducing employee data; use synthetic examples meanwhile. Keep credentials out of documentation.
