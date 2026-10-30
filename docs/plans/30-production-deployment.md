# Plan 30: production deployment

Codex reviewed an earlier draft on October 29, 2026; its corrections are folded in below.

## Context

The app only runs as a local development instance. Some production settings already exist: secure cookies, SSL redirect, the proxy SSL header, WhiteNoise with hashed static files, `gunicorn`, a required `DJANGO_SECRET_KEY` when debug is off, and a stub that refuses non-loopback hosts. Slice 29 added real email and password accounts. Several things still prevent a live host from serving employees:

1. **Real employees can't use the core workflows.** Administrator pages, assignments, groups, renewals, reports and notification recipients are scoped to `is_demo` in about 20 modules. `_verified_recipient` (`training/notifications.py:266`) and `create_demo_employee` (`training/employees.py`) require `.test` addresses. This is step 4 of `docs/plans/email-password-authentication.md`.
2. **Email can't leave the process.** `EMAIL_BACKEND` is hard-coded to locmem. `LocalEmailSink` and `dispatch_notifications` refuse to run outside debug and stub mode. `send_account_email` (`training/accounts.py:133`) sends synchronously after commit, so a delivery failure there would return a 500 *after* the account is created and the earlier links are invalidated.
3. **Rate limiting isn't fit for production.** The limiter (`training/accounts.py:107`) uses the per-process cache, which Slice 29 marks as **blocking rollout**. It keys on `REMOTE_ADDR` (`training/account_views.py:41`), which becomes the proxy's address once the app runs behind one.
4. **There's no packaging, migration procedure, health check, monitoring, logging, backup or deployment documentation.** `cleanup_training_imports --loop` runs only from `scripts/start-local.sh`. Nothing clears expired sessions.
5. **Non-code release gates are open.** These are a named operational owner, an approved data retention and access policy, a verified restore, and documented operations (`docs/implementation-plan.md`).

Outcome: one Docker Compose stack that runs on any Linux host with outbound HTTPS, sends email through Microsoft 365, and satisfies the deployment-proof and production-readiness gates.

## Decisions

- **Packaging:** Docker Compose with these services:
  - `proxy`: Caddy
  - `web`: gunicorn
  - `worker`: notification dispatch
  - `cleanup`: import cleanup and `clearsessions`
  - `migrate`: one-shot migrations
  - `db`: PostgreSQL 17
  - `backup`: `pg_dump` with an off-host copy

  Record this in ADR-0010, which amends the hosting paragraph of ADR-0002.
- **Email through Microsoft Graph `sendMail`, not SMTP AUTH.** Microsoft is disabling Basic SMTP AUTH by default for existing tenants at the end of December 2026. Django's SMTP backend can't use OAuth, and a relay connector needs port 25 plus a static IP or certificate, which conflicts with "any host". Graph uses the client-credentials flow: an Entra app registration with the `Mail.Send` application permission, limited to the sender mailbox through Exchange RBAC for Applications. It needs only outbound HTTPS. Its responses also make failures easy to classify (see step C). Record this in ADR-0011.
- **TLS:** Caddy obtains a public certificate through an ACME DNS-01 challenge against the `critical-technology.com` Cloudflare zone. This needs a custom Caddy build with the Cloudflare DNS module (`xcaddy`). It works whether or not the host is reachable from the internet, and no client has to trust a private CA.
- **Rate limits:** stored in a dedicated PostgreSQL counter table with an atomic upsert, not in Django's `DatabaseCache`. That cache's `incr` isn't atomic, it resets expiry, and culling can drop live counters.
- **Stub isolation:** production runs with `DJANGO_DEBUG=0` and `DEV_AUTH_STUB=0`. The existing `ImproperlyConfigured` guard enforces this.

## Steps

### A. Real-employee workflows (Slice 31, its own plan file; must come first)

- Write one eligibility rule based on `account_state()` (`training/accounts.py:40`):
  - **New work** (assignment, groups, renewals) requires an active, approved employee in the administrator's population; see Slice 31.
  - **History and reports** keep inactive employees visible. Reactivation stays possible.
  - Demo rows are excluded when the stub is off.
- Let real administrators pass the administrator decorators in `training/dev_auth.py` and use the existing administrator pages.
- Make `_verified_recipient` use `email_verified_at`, bound to the *current* address. Reconcile the old `verified_email` field.
- Add a non-demo employee creation path, or retire `create_demo_employee` in favour of `invite_employee`.
- Bring forward the deferred **session version**: deactivation bumps it, which ends sessions and invalidates links. Add a deactivation interface and an offboarding procedure.
- Tests: the existing workflow suite runs with real employees; inactive employees appear in history; demo rows are invisible when the stub is off.

### B. Settings and request boundary (`config/settings.py`, `training/account_views.py`)

Done; see **Status** below. These parts move to later steps:

- **Step C:** a startup failure when Graph settings are missing, and bounded outbound timeouts for the mail client.
- **Step E:** gunicorn access-log redaction.

### C. Email delivery

- **Shared mail module** (`training/mail.py`): a `GraphMailSender` that gets a client-credentials token from Entra, caches it until it expires, and posts to `POST /users/{sender}/sendMail`. Classify the results for the existing `delivery_result` path (`notifications.py:351`):
  - `202` → accepted, and the result records the request ID.
  - `400`/`403`/`404` → `PermanentDeliveryError`.
  - `401` → refresh the token once, then permanent.
  - `429`/`5xx` → retryable, honouring `Retry-After`.
  - Connection refused or DNS failure before the request is sent → retryable.
  - A timeout or reset *after* the request is sent → `AmbiguousDeliveryError`. Graph has no idempotency key, so these cases need manual reconciliation.
- Wire the sender into Django as a small `EMAIL_BACKEND`, so account emails use the same transport. Keep locmem as the default while the stub is on.
- `dispatch_notifications`:
  - Use `LocalEmailSink` when the stub is on and `GraphMailSender` when debug is off.
  - Remove the guard that allows only debug mode.
  - Handle SIGTERM by finishing the current claim, then exit.
  - Write a heartbeat timestamp to a row or table.
- **Account emails:** wrap `send()` in `send_account_email`. A failure is logged and recorded on the employee as `link_send_failed_at`. It must not return a 500, and the response stays the same. Recovery uses the existing resend actions (verification resend, invitation resend, a new reset request). The **Accounts** page flags employees whose last send failed.
- Tests use a fake HTTP transport and cover each status class, timeout after send, token refresh, and `Retry-After`.

### D. Rate-limit store

- Add a `RateLimitCounter(scope, key_digest, window_start, count)` model with a unique constraint on (scope, key_digest, window_start). `key_digest` is an HMAC-SHA256 of the limited key. `rate_limited()` performs one `INSERT … ON CONFLICT DO UPDATE SET count = count + 1 RETURNING count`.
- Ended windows are deleted by `prune_rate_limits()`, which `cleanup_training_imports` calls until step E's `cleanup` loop runs it.
- Add a PostgreSQL concurrency test (N parallel increments → count N) and a window-expiry test with a controllable clock.

### E. Container packaging (new files)

- `Dockerfile`:
  - Multi-stage build on `python:3.12-slim`, installing with `pip install .`.
  - At build time, run `collectstatic` with placeholder settings that aren't secret: `DJANGO_SECRET_KEY=build`, a dummy `DATABASE_URL`, `DJANGO_DEBUG=0`, `DJANGO_ALLOWED_HOSTS=build`, `APP_BASE_URL=https://build.invalid`, and placeholder `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID` and `GRAPH_CLIENT_SECRET` values (`build`), which the startup checks from steps B and C require. `collectstatic` never sends mail. Settings must not open a database connection at import.
  - Non-root user.
- `docker/entrypoint.sh` dispatches on the role:
  - `migrate`: `migrate --noinput`, then exit.
  - `web`: `gunicorn config.wsgi -b 0.0.0.0:8000 --workers ${WEB_CONCURRENCY:-3} --timeout 30 --access-logfile -`
  - `worker`: `dispatch_notifications --loop`
  - `cleanup`: a loop that runs `cleanup_training_imports`, rate-limit pruning and an hourly `clearsessions`.
- `compose.yaml`:
  - Only `proxy` publishes ports 80 and 443.
  - Named volumes for `pgdata`, `caddy_data` and `backups`.
  - `web`, `worker` and `cleanup` depend on `migrate` with `condition: service_completed_successfully`.
  - Health checks: `web` calls `/health/` with an allowed `Host` header. `proxy` doesn't depend on `web`'s health, which avoids a cycle.
  - The `json-file` logging driver with `max-size` and `max-file`.
  - `restart: unless-stopped`. That only restarts processes that exit, so the worker exits on fatal errors, and monitoring (step F) catches anything that hangs.
  - Always run with `docker compose --env-file .env.production`, which handles interpolation and is gitignored. Ship `.env.production.example`.
- `docker/caddy/Dockerfile` (xcaddy build with `caddy-dns/cloudflare`) and a `Caddyfile` containing `{$APP_HOST}`, `tls { dns cloudflare {$CF_API_TOKEN} }` and `reverse_proxy web:8000`. The token is a zone-scoped DNS-edit token.
- `.dockerignore`: `.venv`, `node_modules`, `.git`, `staticfiles`, `.env*` and `docs/screenshots`.
- `GET /health/` in `training/urls.py`: no authentication, runs `SELECT 1`, and returns 200 or 503.
- **Upgrade procedure:** `git pull` → `docker compose build` → `docker compose stop worker cleanup web` → `docker compose run --rm migrate` → `docker compose up -d`. Document rollback as: restore the pre-upgrade dump, then redeploy the previous image tag.

### F. Backups and monitoring

- The `backup` service runs `pg_dump -Fc` every night, keeps N dumps locally, and copies each dump **off the host** (rclone or scp to a target the owner chooses). It records success time in a file.
- `scripts/restore-db.sh` restores into a fresh database and runs `migrate --check`. A rehearsed restore, with its date recorded, is release evidence.
- A `manage.py ops_status` command exits non-zero with a message when any of these is true:
  - The worker heartbeat is older than 2 minutes.
  - The oldest due notification is older than 15 minutes.
  - Any notifications are exhausted or ambiguous and haven't been reconciled.
  - The latest backup is older than 26 hours.
  - Recent account sends have failed.
  - An address block is open ([plan 33](33-sign-in-attack-protection.md)), with its network and expiry.
- Host cron or an external monitor runs `ops_status` and emails the operational owner.

### G. Documentation

- `docs/deployment.md`:
  - Host prerequisites: Docker, outbound HTTPS, a DNS name, and a Cloudflare DNS token.
  - Entra and Exchange setup: app registration, the scoped `Application Mail.Send` role (no Entra-level grant), sender mailbox, SPF and DKIM; see [RBAC for Applications](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac).
  - First deployment, then `create_administrator`, which prompts for a password.
  - Upgrade and rollback, logs, backup and restore, secret rotation (client secret expiry), and the `ops_status` alert runbook (ambiguous notification reconciliation, worker restart).
  - Offboarding.
  - Administrator recovery from [plan 33](33-sign-in-attack-protection.md): `unblock_address`, `remove_passkeys`, and adding a second passkey.
- ADR-0010 (Docker packaging) and ADR-0011 (Graph mail). Update `README.md` and `AGENTS.md` with production commands. Mark the gates in `docs/implementation-plan.md` as they are met.

### H. Release gates (owner actions)

- The owner names the operational owner and approves the data policy, then provisions the host and DNS name, the Entra app registration with its Exchange-scoped mail permission, and the off-host backup target.
- Run a smoke deployment with synthetic data on the target host before inviting any real employee.

### Review findings to fold in

The October 29, 2026 security review of the Slice 29 and 31 commits found no critical or high issues. These low findings belong with the steps above:

- **Step D (done):** add a per-IP counter and a per-email counter to sign-in, so one password can't be tried across many emails or addresses without a cap.
- **Step C:** send account emails off the request path, so the reset response time doesn't reveal whether an email is registered.
- **Step C:** give each link purpose its own counter, so a reset request doesn't cancel an outstanding verification or setup link.
- **Step A follow-up:** let administrators reject or remove an unverified applicant, or expire one automatically. Today an unverified registration blocks an invitation to the same address.

## Status

**Step B, implemented October 29, 2026.** These are in `config/settings.py`, `config/logging.py` and `_client` in `training/account_views.py`:

- With debug off, startup requires an https `APP_BASE_URL` and an explicit `DJANGO_ALLOWED_HOSTS`.
- `CSRF_TRUSTED_ORIGINS` comes from `APP_BASE_URL`.
- HSTS starts at one hour, set through `SECURE_HSTS_SECONDS`. The referrer policy is `same-origin`, frames are denied, and sessions last 12 hours by default.
- `/health/` is exempt from the https redirect.
- Every log goes to stdout through a filter that redacts account-link tokens, including Django's own request and runserver lines.
- Rate limits take the client address from the last `X-Forwarded-For` entry, but only when the request comes from a network listed in `TRUSTED_PROXY_NETWORKS`.
- `RENDER_EXTERNAL_HOSTNAME` is removed.
- Plain-text account emails no longer escape names, and demo emails get the same normalization as real ones.
- `check --deploy` reports only the two intended HSTS warnings: subdomains and preload.
- Tests are in `training/test_production_settings.py`.

**Step C, implemented October 29, 2026.** See [ADR-0011](../adr/0011-email-through-microsoft-graph.md):

- `training/mail.py` sends through Graph with the client-credentials flow. It tells "not sent" apart from "sent with no answer" using its own `https_post`, and maps each outcome to retryable, permanent or ambiguous.
- `GraphEmailBackend` carries account emails.
- The notification worker picks its transport from `EMAIL_BACKEND`, honors `Retry-After`, refuses `.test` addresses when sending through Graph, stops cleanly on SIGTERM between notifications, and records a `WorkerHeartbeat` each cycle.
- A failed account email is logged without the link, sets `Employee.link_send_failed_at` (shown as **Email not sent**), and no longer returns a 500.
- With debug off, startup requires the Graph settings unless another backend is set explicitly.
- Log redaction also covers `stack_info`.
- Tests are in `training/test_mail.py`. Nothing has been sent through a real tenant yet; that needs the Entra app registration from step H.

Still open from the review findings: separate link counters per purpose, and sending account emails off the request path to hide timing.

**Step D, implemented October 30, 2026.** `RateLimitCounter` (migration `0019`) replaces the per-process cache:

- `rate_limited()` in `training/accounts.py` counts each request with one `INSERT … ON CONFLICT DO UPDATE … RETURNING count` in a fixed window aligned to the scope's length. A request at the end of one window and the start of the next can therefore reach up to twice the limit.
- Each counter stores an HMAC-SHA256 of its key under `SECRET_KEY`. A plain hash of an email or address could be confirmed by guessing; the keyed hash can't be without the secret. Changing `SECRET_KEY` resets every counter.
- Sign-in now counts three keys on every attempt: address and email together (5 per 15 minutes), the address alone (100), and the email alone (20). The address limit is high because employees in one office can share an address. The email limit lets someone block one employee's sign-in for up to 15 minutes; that trade-off closes spreading one password across many addresses.
- `cleanup_training_imports` also deletes counters whose window ended in every scope, until step E gives the `cleanup` service its own loop.
- Tests in `training/test_accounts.py` cover the window reset with a controllable time, separate scopes and keys, pruning, both new sign-in limits, and 8 parallel increments on PostgreSQL. The full PostgreSQL suite passed: 388 tests.

Gunicorn's access-log redaction comes with step E. Other open items, also for step E:

- **Proxy header handling:** Caddy must replace any `X-Forwarded-For` header the client sends.
- **Network isolation:** `web` must be reachable only through Caddy. Both the forwarded-address and the forwarded-protocol header depend on this.

**Step E, implemented October 30, 2026,** as step 1 of [plan 36](36-current-task.md#step-1-notes):

- Gunicorn's logs redact account links.
- Caddy replaces `X-Forwarded-For`.
- `web` is reachable only through the proxy.

## Order

The steps run in order, each as its own PR: A → B → C → D → E → F → G. A synthetic-data smoke deployment can follow E even before A merges, as long as no real employees are invited.

## Verification

- `./scripts/test-local.sh` passes after each step: the full PostgreSQL suite, `check` and `collectstatic`.
- `DJANGO_DEBUG=0 manage.py check --deploy` passes, or produces only the documented HSTS warnings.
- Local stack with `docker compose --env-file .env.production up --build`. The Graph sender points at a local fake endpoint, or at a test tenant mailbox.
  - `/health/` returns 200 through Caddy, and plain http redirects to https.
  - `create_administrator` prompts and creates the administrator. Sign in, invite an employee, and confirm the setup email is sent. Complete setup, assign training, and confirm that the worker sends the notification.
  - Make the fake endpoint return 429, then 202: the notification retries and then succeeds. Make it time out after the send: the notification becomes ambiguous and appears for reconciliation.
  - Kill `worker` mid-claim and restart it. The stale claim becomes **ambiguous**, not retried, and shows up in `ops_status`.
  - Fire parallel sign-in failures: the counter is exact, and the limit holds across `web` restarts and gunicorn workers.
  - A forged `X-Forwarded-For` sent directly to Caddy doesn't change the rate-limit key.
  - Make an account send fail: the response is unchanged, the administrator sees the flag, and a resend succeeds.
  - Run a backup, restore it into a new database, and compare row counts.
  - Run the upgrade procedure with a new migration and check that the workers stay stopped while it runs.
- On the target host: repeat the smoke test with the real tenant to a test mailbox, and check headers for SPF, DKIM and DMARC.
