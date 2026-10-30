# Plan 36: current task — deployment, records and list cleanup

This is the current task. It sets the order of the next series of work and points at the plans that hold the detail. Read it first, then the plan named by the step you're on.

Written October 30, 2026 on top of `e52bc4c` (branch `build-static-before-tests`, not yet merged). Line references in the linked plans may have drifted.

## How to use this file

1. Read this file, `AGENTS.md`, and `docs/GLOSSARY.md`.
2. Find the first step under **Status** that isn't done. Read the plan section it names.
3. Check `git status` and `git log`. Another session may have started the step. Work on a new branch from an up-to-date `main`, one branch and PR per step.
4. Implement the step, run its verification, and commit. Stage files by name, never `git add -A`.
5. Update **Status** below in the same PR: what was done, the test count, and anything moved to a later step.
6. Stop at the end of the step. Push, give a prefilled compare link for the PR, and wait for the owner before starting the next step.

If the step needs a decision this file and its plan don't answer, ask the owner. Don't guess.

## Order

| Step | What | Detail | PR |
|---|---|---|---|
| 1 | Container packaging | [Plan 30 step E](30-production-deployment.md#e-container-packaging-new-files) | own |
| 2 | Backups and monitoring | [Plan 30 step F](30-production-deployment.md#f-backups-and-monitoring) | own |
| 3 | Deployment documentation | [Plan 30 step G](30-production-deployment.md#g-documentation) | own |
| 4 | Account emails off the request path, separate link counters | [Below](#step-4-plan-30-step-c-leftovers) | own |
| 5 | Retention decisions and documentation | [Plan 32 step 0](32-scheduled-reassignment-and-records.md#step-0-decisions-and-documentation) | with 6 |
| 6 | Tamper-resistant records, stored renewal dates | [Plan 32 slices A and B](32-scheduled-reassignment-and-records.md#slice-a-make-records-tamper-resistant) | own |
| 7 | Renewals-due queue | [Plan 32 slice C](32-scheduled-reassignment-and-records.md#slice-c-renewals-due-queue) | own |
| 8 | Automatic renewal and the scheduler entry point | [Plan 32 slice D](32-scheduled-reassignment-and-records.md#slice-d-automatic-renewal) | own |
| 9 | Fixed calendar schedules | [Plan 32 slice E](32-scheduled-reassignment-and-records.md#slice-e-fixed-calendar-schedules) | own |
| 10 | Training record for audit | [Plan 32 slice F](32-scheduled-reassignment-and-records.md#slice-f-training-record-for-audit) | own |
| 11 | Groups list preview | [Below](#step-11-plan-35-leftover-groups-list) | own |

Plan 30 step H (owner actions and release gates) is not part of this task. The owner does it. Steps 1–3 must leave everything the owner needs for it in `docs/deployment.md`.

## Before you start

- Merge `build-static-before-tests` first, so every step branches from a `main` whose test runner builds static files.
- `docs/plans/32-scheduled-reassignment-and-records.md` is untracked. Commit it with this file.
- The latest migration is `0021_registration_expiry`. Number new migrations after whatever is latest when you start; neither plan fixes the numbers.

## Changes to the linked plans

These adjust plans 30 and 32 to the order above. Where they conflict with the linked plan, this file wins.

### Steps 1–3 (plan 30 E–G)

- **Take plan 32's deployment items now.** ADR-0012 (step 5) requires them, and they cost little while the stack is being built.
- **Step 1, database roles:**
  - The image's `POSTGRES_USER` is a superuser used only for provisioning. Two login roles do the work:
    - `training_owner` owns the schema and every object. Only the `migrate` service uses it.
    - `training_app` is used by `web`, `worker`, `cleanup` and `backup`. It owns nothing and isn't a member of `training_owner`.
  - Grant `training_app` `CONNECT` on the database, `USAGE` on the schema, `SELECT, INSERT, UPDATE, DELETE` on all tables and `USAGE, SELECT` on all sequences. Also run `ALTER DEFAULT PRIVILEGES FOR ROLE training_owner` with the same table and sequence grants, so tables created by later migrations are covered. It gets no `TRUNCATE`, no `CREATE` and no ownership.
  - `clearsessions` and the cleanup jobs only need `DELETE`. Plan 32's triggers (step 6) block deletes of evidence for both roles.
  - Put the SQL in `docker/postgres/provision-roles.sql`, written so it can run again safely. The init script runs it on an empty volume. Init scripts don't run on an existing volume, so `docs/deployment.md` (step 3) gives the command to run it by hand there and after a restore into a new cluster.
  - `pg_dump` must read every table, so `backup` uses `training_app`, or a separate read-only role with the same `SELECT` grants.
  - Tests keep the local privileged role, because the test runner creates databases and truncates tables. The step 1 verification checks the production permissions in the compose stack: as `training_app`, `TRUNCATE` and `CREATE TABLE` fail, and an ordinary `INSERT` succeeds on a table created by a later migration.
- **Step 1, worker shutdown:** set `stop_grace_period: 60s` on `worker`. Compose's 10-second default is shorter than one Graph send (`MAIL_TIMEOUT_SECONDS`, 15) plus a token refresh.
- **Step 2, backup retention:** keep every nightly dump off-host for at least 3 years from when it was created. A dump counts toward retention only after its off-host upload succeeds. The restore rehearsal also checks that roles and grants come back, by running `provision-roles.sql` and the step 1 permission checks against the restored database.
- **The `worker` service will also run the scheduler** (step 8). Don't add a scheduler service.
- **`docs/deployment.md` is updated again** by steps 4, 8 and 9. Step 3 writes it for the stack as it is then.

### Step 4 (plan 30 step C leftovers)

Designed below; plan 30 doesn't specify it.

### Steps 5–10 (plan 32)

- Ignore plan 32's **Before you start**. `rate-limit-store` has merged.
- In plan 32's **Verification**, `migrate training 0019` means "migrate back to the migration before this slice's first one".
- **Scheduler entry point** (plan 32) arrives with step 8. Step 9 adds calendar schedules to `run_schedules`.
- Steps 8 and 9 also update the deployment pieces:
  - `ops_status` (step 2) reports a `schedules` heartbeat older than `2 × --schedule-interval`.
  - `docs/deployment.md` describes `SCHEDULE_RUN_TIME`, `--schedule-interval`, and what an administrator sees when a schedule's authorizer is no longer valid.
- Plan 32 says to ship A+B together, then C, D, E and F. Step 5's documents go in the A+B PR.
- **Employee deletes (step 6).** Plan 32 rejects every `Employee` delete, which would break plan 35's registration removal and expiry. The trigger and the model guard allow one case only: deleting a removable registration, as `removable_registrations()` (plan 35) defines it. That is a real, active employee whose email isn't confirmed, whose approval is pending, and who has no decision recorded. Every other delete is rejected. ADR-0012 states the exception: an unconfirmed registration was never an employee record. The tests cover both cases: a registration can still be removed and can still expire, and an approved employee can't be deleted by `.delete()` or raw SQL.
- **Resumable scheduled runs (steps 8 and 9).** Plan 32 promises that each run is saved before it executes. Make that hold after a crash:
  - Commit the run row (`AutomaticRenewalAttempt` or `AssignmentScheduleRun`) and its stored chunk inputs in their own transaction. Then call `create_assignments` for each chunk in a separate transaction. Don't wrap a whole training in one transaction, because that would undo the commit made before execution.
  - Record each chunk's outcome on the run. A run with a chunk that has no outcome is **unfinished**. The next cycle resumes it with the stored inputs, before looking for new occurrences. Don't skip it just because a run row exists. `create_assignments`' fingerprint check makes the resumed chunk return its original results.
  - Between chunks, check the worker's SIGTERM flag and stop. Cap each cycle's scheduler work, for example 20 chunks, so notifications aren't starved.
  - `WorkerHeartbeat(name="schedules")` is written every cycle, before and after `SCHEDULE_RUN_TIME`, and means "the scheduler is alive". Each completed day is recorded separately. `ops_status` reports a stale heartbeat, a day not completed by `SCHEDULE_RUN_TIME` plus one hour, and any run left unfinished for over an hour.
  - Tests: a crash after the run row commits, and another after the first chunk commits, both resume to the same results as an uninterrupted run. A SIGTERM between chunks leaves a run that the next cycle resumes.

## Step 4: plan 30 step C leftovers

These are two findings from the October 29, 2026 security review. Codex reviewed this design on October 30, 2026, and its corrections are folded in.

### A. Send account emails from the worker

**Problem.** `send_account_email` (`training/accounts.py:202`) sends in the request thread, after commit. A password-reset request for a registered email waits for Graph, but one for an unknown email doesn't. The response time therefore reveals whether an email is registered.

**Queue.**

- New model `AccountEmail`:
  - Fields: `employee` (null, `CASCADE`), `purpose`, `email_at_request`, `session_version_at_request`, `status`, `requested_at`, `claim_token`, `claimed_at`, `link_generation_issued`, `finished_at`, `error_code`.
  - Statuses: `queued`, `sending`, `sent`, `failed`, `ambiguous`, `superseded`, `obsolete` and `discarded`.
  - `CASCADE` means removing an unconfirmed registration (plan 35) also removes its queued emails.
- `send_account_email(employee, purpose)` inserts one `queued` row in the caller's transaction and returns. It no longer builds a link or calls `send_mail`.
- Move each enqueue into the transaction that creates or changes the employee. Today `register` and `invite_employee` call it after their transactions (`training/accounts.py:300`, `:409`).

**Timing.**

- A reset request inserts one row in both cases:
  - For a known email, a `queued` row.
  - For an unknown email, a `discarded` row with no employee and no address.
- The cleanup loop deletes `discarded` rows after a day.
- Registration already runs the password hash on both paths, and both now insert one queue row. The difference left is one `Employee` insert. Accept that and say so in `docs/deployment.md`.
- Signed-in resends need no timing work, because the administrator already knows the account exists.
- **Verification:** a manual timing check, not a unit test. Time 200 reset requests for a known email and 200 for an unknown one against the compose stack, compare the medians, and record the result in the PR.

**Worker.** `dispatch_notifications` handles account emails each cycle, oldest first, before notifications. Mirror the notification claim flow (`training/notifications.py`):

1. **Claim** a batch with `select_for_update(skip_locked=True)`:
   - set `sending`, `claim_token` and `claimed_at`
   - commit
   - never hold the claim transaction while taking the employee's advisory lock
2. **For each claimed row**, take `_account_email_lock(employee)`, then in a short transaction:
   - Skip it if the employee is gone, because a removal raced the claim. CASCADE usually deletes the row too.
   - Mark it `obsolete` and don't send if the request no longer applies:
     - the employee's email differs from `email_at_request`
     - `session_version` changed (deactivation)
     - the employee is inactive
     - for `verify`: the email is already confirmed
     - for `setup`: a password is already set
   - Mark it `superseded` if a newer `queued` or `sent` row with the same purpose exists for this employee. `existing` emails carry no link and are never superseded.
   - Otherwise:
     - increment that purpose's counter (B below)
     - for `verify`, set `verification_sent_at`
     - store `link_generation_issued`
     - commit
3. **Send** outside any transaction, through the email backend.
4. **Record the result**, guarded by `claim_token`, so a worker whose claim expired can't overwrite a newer outcome:
   - accepted: `sent`
   - definite failure: `failed`. Restore the previous counter and `verification_sent_at` only if the counter still equals `link_generation_issued`, as today's code does. Set `link_send_failed_at`.
   - ambiguous outcome: `ambiguous`, without restoring anything
   - Clear `link_send_failed_at` only when the counter still equals `link_generation_issued`.
5. **Stale claims.** A `sending` row older than `NOTIFICATION_CLAIM_LEASE_SECONDS` becomes `ambiguous`. It isn't failed and isn't resent: the email may have left. The administrator resend actions recover it.

The send function returns the delivery result instead of swallowing errors. The worker records it.

**Expiry (plan 35).**

- `verification_sent_at` is set when the worker issues the link, not when the email is queued. Expiry therefore keeps counting from the most recent confirmation email actually issued.
- A resend still queued when expiry runs is removed with the registration. This only happens if the worker has been down while a registration crosses its 7-day mark, and `ops_status` already reports the stalled queue. Accept it and test it.

**Notices (plan 34).**

- A resend action puts the new row's ID in the session. The notice on the next page view reads that row:
  - `queued` or `sending`: "Confirmation link queued for …" or "Setup link queued for …"
  - `sent`: "… sent to …"
  - `failed` or `ambiguous`: the error notice
- The row's purpose decides the wording, so a reset queued for the same employee can't be mistaken for a confirmation. The **Email not sent** badge still uses `link_send_failed_at`.
- Public messages change from "we've sent" to "we're sending": registration, password reset and verification resend in `training/account_views.py`.

**Local development.**

- A separate worker process can't fill `runserver`'s in-memory `mail.outbox`, so `/dev/outbox/` (`training/account_views.py:389`) would go empty.
- With debug and the stub on, use Django's file-based email backend with `EMAIL_FILE_PATH` set to `.local-mail/` (gitignored).
- `/dev/outbox/` reads the newest 50 messages from that directory. `LocalEmailSink` accepts the file backend as well as locmem.
- `scripts/start-local.sh` starts `dispatch_notifications --loop` beside the cleanup loop. Update `docs/local-development.md`.

**Monitoring.** `ops_status` (step 2) reports:

- the oldest `queued` account email, when it's older than 5 minutes
- any `ambiguous` account email from the last 7 days

**Tests.**

- Tests run the worker once to drain the queue, not in a background thread. Add a helper for it, and add the drain to existing account tests that read `mail.outbox` straight after a request.
- Add one test that runs the real `/dev/outbox/` view against the file backend, so the cross-process path is covered.
- Cover:
  - claim and send
  - definite failure restores the counter
  - an ambiguous outcome and an expired claim restore nothing and aren't resent
  - two workers don't both send superseded rows
  - an email change, a deactivation or a completed setup makes queued rows `obsolete`
  - a removal during a claim
  - expiry removing a queued resend
  - the known and unknown reset paths each insert one row
  - each notice state

### B. Separate link counters for each purpose

**Problem.** `Employee.link_generation` is shared by verify, setup and reset links, so a reset request cancels an outstanding verification or setup link.

**Design.**

- Replace `link_generation` with `verify_link_generation`, `setup_link_generation` and `reset_link_generation`. The migration copies the current value into all three.
- Each token generator (`training/accounts.py:93–106`) hashes its own counter instead of the shared one. Everything else stays the same: the salts, the order of the hash inputs, Django's password, `last_login` and email inputs for setup and reset links, and verification's deliberate exclusion of password and login. Because the copied value is identical, a token issued before the migration stays valid.
- Invalidation:

  | Event | verify | setup | reset |
  |---|---|---|---|
  | Verify email issued | ✓ | | |
  | Setup email issued | | ✓ | |
  | Reset email issued | | | ✓ |
  | Email change | ✓ | ✓ | ✓ |
  | Deactivation | ✓ | ✓ | ✓ |
  | Email confirmed | ✓ | | |
  | Setup completed (also confirms the mailbox) | ✓ | ✓ | ✓ |
  | Password reset completed | | ✓ | ✓ |
  | Signed-in password change | | ✓ | ✓ |
  | Approval or rejection | | | |

- **Tests:**
  - a reset request leaves an outstanding verification link working, and the reverse
  - each row of the table
  - a token made by the pre-migration code for each purpose still validates after the migration

### Verification

- `./scripts/test-local.sh` passes.
- In Chrome at `http://localhost:8000` with `start-local.sh`:
  1. Register, and find the verify link in `/dev/outbox/`.
  2. Request a password reset for the same email.
  3. Confirm the verify link still works.
- Record the timing check above in the PR.

## Step 11: plan 35 leftover, Groups list

**Problem.** **Groups** (`training/group_views.py:13`) lists every group on one page.

**Design.**

- Use `browse()` from `training/browsing.py` with `search_fields=["name"]`, the same page size and page links as the other full lists, and the existing order (name, then ID). Name-only search adds no join, so `Count("memberships")` stays correct with `browse()`'s `distinct()`.
- The create-group form stays at the top. Its `action` (`templates/training/groups.html:6`) carries the current `q` and `page`. After an error the page renders with the same search and page, and keeps the name that was typed.
- The group detail page's membership checkboxes stay on one page, as plan 35 decided.
- **Tests:** more groups than fit on one page spread across pages. An invalid name submitted on page 2 of a search shows the error on that same page, with the typed name kept. A search finds a group by part of its name. The member count is right on page 2. Demo and real administrators each see only their own population's groups.
- **Screenshots:** the Groups list with page links, at 1280 and 390 px, with the `screenshot-training-app-page` skill.

## Verification for every step

- `./scripts/test-local.sh` passes: check, the full PostgreSQL suite, and the static build.
- `.venv/bin/python manage.py makemigrations --check` reports nothing pending.
- Plan 36 and the steps 5–10 corrections were reviewed by Codex on October 30, 2026. Re-run `codex-review-plan` only if a step's design changes materially.
- A visible change gets screenshots at 1280 and 390 px in the PR.
- After a template change, restart `runserver --noreload` before checking in Chrome.

## Status

Nothing started. Update this section as each step finishes.

| Step | State | PR | Notes |
|---|---|---|---|
| 1 | not started | | |
| 2 | not started | | |
| 3 | not started | | |
| 4 | not started | | |
| 5–6 | not started | | |
| 7 | not started | | |
| 8 | not started | | |
| 9 | not started | | |
| 10 | not started | | |
| 11 | not started | | |
