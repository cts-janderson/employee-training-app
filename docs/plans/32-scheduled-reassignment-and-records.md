# Slice 32: scheduled reassignment and 3-year training records

Status: planned, not started. It extends [Slice 17](17-renewal.md), [Slice 19](19-waivers.md) and [ADR-0005](../adr/0005-renewal-interval-on-training.md).

## Context

Administrators need to reassign recurring training without remembering to do it. The organization must also be able to show an auditor, for up to three years back, that an employee held a current certification for each required training. The records cover every assignment however it ended: completed, waived, canceled, or reassigned.

What already exists:
- **Renewal (Slice 17, ADR-0005).** A per-training `renewal_interval_months` setting and a per-training review page (`trainings/<id>/renew/`). Only an administrator starts a renewal, and only a completion can be renewed.
- **No deletes or rewrites of evidence.** An audit of the code found that every foreign key uses `PROTECT`, nothing deletes assignments or evidence, and history rows are append-only. A renewal or reassignment is always a new `Assignment` row.

Gaps the audit found:
1. Protection works only at the model level. Queryset `.update()` and `.delete()` bypass it. `Assignment`, `AssignmentSlideProgress`, `AssignmentOperation`/`Result` and `EmployeeProfileChange` have no guard at all.
2. Changes to the renewal interval are not recorded. The renewal date is recalculated from the current interval, so changing it silently rewrites every past "certified through" date.
3. There is no single page or export that shows one employee's full certification history with lapses.
4. Nothing is scheduled.

User decisions (2026-10-30):
- Build all three scheduling forms:
  - automatic renewal for each employee
  - fixed calendar schedules for a group
  - one "renewals due" queue
- A waiver starts the next cycle, as a completion does. A cancellation does not.
- Keep records at least 3 years. Nothing is deleted, and purging stays out of scope.

Codex reviewed this plan on 2026-10-30. Its corrections are included below: update guards as well as delete guards, a persisted snapshot for each scheduled run instead of a daily UUID, reconstructed renewal dates labelled as such, catch-up due dates, chunking for large groups, and a single scheduler entry point.

## Before you start

This plan was written on 2026-10-30 on top of `rate-limit-store`. That branch had uncommitted work adding migration `0019_rate_limit_counters`. Start from `main` after that work merges. Number the new migrations after whatever the latest one is then; this plan doesn't fix the numbers. Line references point to the code as of `9de7777` and may have drifted.

## Step 0: decisions and documentation

- New ADR `docs/adr/0012-retain-training-records-three-years.md`:
  - The application never deletes, or rewrites the outcome of, an assignment or its evidence.
  - Retention runs at least 3 years from the later of the assignment's ending and the employee's deactivation.
  - Purging needs an approved policy.
  - Deployment items for `docs/plans/30-production-deployment.md`:
    - The production database role has no `TRUNCATE` or DDL rights at runtime; migrations run under a separate role.
    - Backups are kept at least 3 years, and restoring them is tested.
- New ADR `0013-scheduled-reassignment.md`:
  - Supersedes ADR-0005's "administrator starts it" decision and the deferral of "automatic reassignment" in `docs/implementation-plan.md#deferred-scope`.
  - An administrator authorizes each schedule. The scheduler runs it, recorded as "created by the scheduler under authorization from X". It uses the existing `create_assignments` transaction path.
- Amend ADR-0005:
  - The renewal date is stored when the assignment ends, and a waiver also gets one.
  - Clearing a training's interval stops all further renewals of it. Dates already stored still appear in the training record.
- Update the docs. Use the `domain-modeling` skill for the glossary.
  - `docs/GLOSSARY.md`:
    - Renewal: ends by completion or waiver.
    - Renewal date: stored at ending.
    - New terms: Automatic renewal, Assignment schedule, Training record.
  - `docs/user-stories.md`:
    - Revise US-15's acceptance criteria.
    - Add US-23 (scheduled reassignment) and US-24 (training record for audit).
  - The real-data gate rows in `docs/mvp.md` and `docs/implementation-plan.md` now name the 3-year minimum.

## Slice A: make records tamper-resistant

### Model guards

These catch mistakes in application code. Follow the pattern of `AssignmentHistory.save/delete` in `training/models.py:454–460`.

- **`Assignment`:**
  - `delete()` raises.
  - `save()` raises if any of these would change:
    - `completed_at`, `canceled_at`, `waived_at` or `started_at` once set
    - `renewal_due_on` once set (Slice B)
    - `assigned_at`
    - `employee`, `training`, `version`, `renews`, `cycle_number`
    - `due_*` after the assignment has ended
  - `resume_*` and `archived_at` may change. Both have history or are not evidence.
- **Insert-only:** `AssignmentSlideProgress`, `AssignmentOperation`, `AssignmentOperationResult` and `EmployeeProfileChange`. Use the same pattern as `AssignmentDocumentProgress` (`training/models.py:790–799`).
- **Endings exclude each other.** Add a check constraint that also forbids `completed_at` together with `canceled_at`. Today only combinations with a waiver are blocked.

### PostgreSQL triggers

These are the real guarantee.

- Add them in a migration through a reversible custom operation, or a `RunPython` wrapper that runs SQL only when `schema_editor.connection.vendor == "postgresql"`. A plain `RunSQL` has no vendor check, and the SQLite screenshot skill must keep working.
- Test applying and reversing the migration.
- **Append-only tables** reject both `UPDATE` and `DELETE`:
  - `AssignmentHistory`
  - slide and document progress
  - `QuizAttempt`, `AttemptResponse` and `AttemptResponseSelection`
  - `AssignmentOperation` and its results
  - profile, activity and group change tables
  - `NotificationReconciliation`
- **`NotificationDeliveryAttempt`:** an update is rejected once the attempt has an outcome. `Notification` rejects deletes only, because its status changes legitimately in `training/notifications.py`.
- **`Employee`:** deletes are rejected.
- **`Assignment`:** a `BEFORE UPDATE` trigger enforces the same field rules as `save()`, and deletes are rejected.
- **Published content:** add one trigger on each concrete table:
  - `TrainingVersion`
  - `Slide`, `SupportingMaterial` and `OutlineItem` (owned through `version`)
  - `Quiz`
  - `Question` (owned through `quiz` → `version`)
  - `QuestionOption` (owned through `question` → `quiz` → `version`)

  Each trigger rejects an insert, update or delete when the owning version is published. It checks both the old and the new owner, so a row can't be moved into or out of a published version.
- **`seed_demo.py`** back-dates `assigned_at` with `.update()` (lines 197–199). Change it to set the value when it creates the row instead: `assigned_at` moves from `auto_now_add` to `default=timezone.now`.
  - Check the other back-dating updates the same way: published `TrainingVersion` `created_at` and `published_at` at 166–167, and `Notification` `created_at`.

## Slice B: store renewal dates and renew from waivers

- New `Assignment` fields, written once inside the same transaction that ends the assignment:
  - `renewal_due_on`
  - `renewal_interval_months_at_end`
  - `renewal_date_basis` (`recorded` or `reconstructed`)
- Every completion path must include the new fields in its `update_fields`. Setting them in `_complete_if_satisfied` alone would lose them silently. The paths are:
  - slide acknowledgment
  - document acknowledgment
  - quiz submission in `training/quizzes.py`
- Waivers also fill the fields, in `training/assignments.py` ~571–608. The date is the waiver date plus the interval. Cancellations never get a renewal date.
- The data migration fills existing completions and waivers using the current interval, with basis `reconstructed`. The training record labels those dates "reconstructed on <migration date> from a <N>-month interval".
- Add an insert-only `TrainingRenewalSettingChange(training, actor, field, old, new, occurred_at)`.
  - `set_renewal_interval` (`training/renewals.py:79`) writes one row per change. Slice D's automatic-renewal settings write to it as well.
  - A changed interval applies only to assignments that end after the change, and the editor help text says so.
- Changes in `training/renewals.py`:
  - `renewal_date()` returns the stored value.
  - `_due_for_renewal` accepts both `Ending.COMPLETED` and `Ending.WAIVED` as the source. Rename `RenewalCandidate.completed_assignment` to `prior_assignment`.
  - A training whose interval is cleared still returns no candidates.
  - The manual-assignment blocker and `one_live_renewal_per_completion` stay as they are. Neither depends on the kind of ending.
- Pages:
  - On the renew review page, show "Completed <date>" or "Waived <date>".
  - On the learner's My training page, show waived cycles in the cycle list.

## Slice C: renewals-due queue

- New administrator page `renewals/` in `training/assignment_views.py` (`training/urls.py`). It covers every training in the administrator's population that has an interval, using `renewal_candidates(training, as_of=today + 60 days)`.
- It groups employees by training:
  - Columns: renewal date, overdue or upcoming, and whether automatic renewal will handle them.
  - Blocked employees are listed separately: inactive, already holding an outstanding assignment, or "automatic renewal was canceled".
- Each training section links to the existing renew page with `as_of` filled in (`renew_training`, `assignment_views.py:164`). Review and submit go through `create_assignments` unchanged.
- Add a dashboard tile with the count of overdue renewals.

## Shared scheduler rules (Slices D and E)

- **The run is saved before it executes.** Each scheduled action first commits an immutable run row that holds everything the action needs:
  - a random `operation_id`
  - the exact inputs: version, due date and employee IDs
  - the authorizing administrator
  - the settings revision it ran under
  - the execution time

  It then calls `create_assignments` with exactly those stored inputs. A retry after a crash reuses the row, so `create_assignments`' fingerprint check (`training/assignments.py:212, 431`) returns the original results instead of failing.
- **The authorizer is checked on every run.** Re-fetch the authorizing administrator each run and validate them with `_administrator_can_change`. If they fail, nothing is created and the schedule is flagged on the Slice C queue and the schedule page.
- **One population.** The training, the group and the authorizer must all belong to the same population. The service functions enforce this, not only the editors.
- **ADR-0001 stays enforced by the existing path.** `create_assignments` keeps its employee locking, its re-checking of locked candidates, and the partial unique constraint.
  - Leaving out conflicting employees beforehand only keeps the results tidy. It is not concurrency protection.
- **Automatic renewal and calendar schedules exclude each other.** Each training uses at most one of them. Both editors take `select_for_update` on the `Training` row before checking.
- **Recipient limit.** `create_assignments` accepts at most 100 recipients, so recipients are split into chunks of 100 in employee-ID order, and each chunk gets its own stored operation.
- **New sources.** `AssignmentOperation.Source` gains `AUTOMATIC_RENEWAL` and `SCHEDULE`, and the check constraint widens to allow them. Administrator pages label these operations "Scheduled: authorized by X on <date>".

## Slice D: automatic renewal

- New fields on `Training`:
  - `auto_renew_authorized_by` (FK to Employee, PROTECT, null)
  - `auto_renew_authorized_at`
  - `auto_renew_lead_days` (0–90, default 30)
- Each change is logged in `TrainingRenewalSettingChange`.
- Turning it on happens on the draft editor's renewal panel. A preview says: "N employees are already past their renewal date. They will be assigned immediately, due <today + lead>."
- New `training/schedules.py` with `run_automatic_renewals(*, today, now)`.
  - **One attempt per prior assignment.** `AutomaticRenewalAttempt(prior_assignment, run_on, operation_id, version, due_date, outcome)` is unique on `(prior_assignment, run_on)`.
  - **Daily run.** For each candidate as of `today + lead_days` that is not already pre-filtered as blocked:
    1. Commit the attempt row.
    2. Call `create_assignments` for that one employee, using `Recipients.renewal(training_id, as_of, employee_ids=[id])`. Widen `RenewalRecipients` to pass the `employee_ids` argument that `renewal_candidates` already accepts.
  - **Due date:** the renewal date, or `today + lead_days` if that is later (a backlog).
  - **Blocked employees** (inactive, or with an outstanding assignment) get no attempt. A race that `create_assignments` reports as `ALREADY_ASSIGNED`, `CONFLICT` or `INACTIVE` is recorded on the attempt and tried again on a later day, if the employee is still a candidate.
  - **No re-creating canceled renewals.** A prior assignment that already has a `CREATED` attempt is never retried automatically. If an administrator cancels that renewal, the employee shows on the Slice C queue for manual action.

## Slice E: fixed calendar schedules

- `AssignmentSchedule` fields:
  - `training`, `group` (PROTECT)
  - `first_date`, `every_months` (1–120)
  - `due_days` (1–365)
  - `authorized_by`, `authorized_at`
  - `ended_at`, `ended_by`, `end_reason`
  - Schedules are never deleted, only ended. Edits are logged in an insert-only `AssignmentScheduleChange`.
- `AssignmentScheduleRun` fields:
  - `schedule`, `occurrence_date`, `executed_at`
  - `skipped_occurrences`
  - `member_ids` (JSON): the group's membership at execution, kept as audit evidence
  - `version`, `due_date`
  - its stored `operation_id`s
  - It is unique on `(schedule, occurrence_date)`.
- `run_calendar_schedules(*, today, now)` handles each active schedule whose occurrence on or before today has no run yet.
  - **Missed occurrences.** After downtime it runs only the latest missed occurrence, the same policy as missed reminders, and records the skipped dates on the run.
  - **Due date:** occurrence + `due_days`. If that date has already passed, which happens when catching up, use `today + due_days` instead. `validate_new_due_date` rejects past dates.
  - **Membership** is resolved when the run executes, not on the occurrence date. Both the run row and the docs say so.
  - **Recipients:** a new `ScheduleRecipients` built from the stored `member_ids`. It sets `renews` to each employee's latest completed or waived assignment of the training, archived or not, so that `cycle_number` continues.
    - `EmployeeGroupRecipients` supplies no previous assignment today, which is why the new class is needed.
    - An employee with no earlier assignment starts at cycle 1.
  - **Conflicts are final for that occurrence.** Employees who are inactive or already assigned are recorded in the operation results and shown on the schedule page. The scheduler doesn't retry them.
- Pages:
  - a schedule list and editor at `trainings/<id>/schedules/`, with a preview of the next three occurrences and the current group size
  - run history with each run's results
  - "End schedule" with a reason

## Scheduler entry point

- Only one entry point: the existing `dispatch_notifications` worker.
  - Every cycle calls `run_schedules(now)` from `training/schedules.py`, which runs Slice D and then Slice E. Schedules run at most once every `--schedule-interval` seconds (default 900), and only after `SCHEDULE_RUN_TIME` (07:00 in the organization time zone).
  - A single run without `--loop` also runs schedules, which covers cron and manual runs.
- It writes a `WorkerHeartbeat(name="schedules")`.
- Each training runs in its own transaction. A failure is logged and doesn't stop the others.

## Slice F: training record for audit

- `employees/<id>/training-record/` and `…/training-record.csv`, for administrators only. It works for inactive employees too.
- **Date range:** the default is the last 3 years. Coverage is calculated from certification periods that overlap the window, so a completion from before the window still counts.
- **Grouping:** by training. Each assignment is one row, in cycle order, whether archived, canceled, waived, renewed or reassigned.
- **Columns for each row:**
  - assignment ID, cycle, version number and title
  - how it was assigned: manual, group, renewal, automatic renewal or schedule, plus the actor or authorizer and the operation ID
  - assigned at and due date, with any deadline changes from `AssignmentHistory`
  - outcome and its timestamp, and on-time or late
  - the passing quiz score and the passing threshold
  - the actor and reason for a waiver or cancellation
  - "Certified through", with its basis: recorded, or reconstructed
  - the employee's name and email at the time of the assignment, from `EmployeeProfileChange`
- A waiver is labelled "Waived (exception; not a demonstration of training)", so it is never shown as competence.
- **Coverage summary for each training:**
  - certified periods
  - lapses, where the "Certified through" date passed before the next completion or waiver. The day after that date is the first day lapsed, and an ongoing lapse runs to today.
  - overlapping renewals are merged
  - current status
- Each row links to the administrator's assignment detail, which shows the exact published version content and the quiz attempts. Published versions can't be changed (Slice A).
- **CSV:**
  - Reuse `_spreadsheet_safe` and the generation time and time zone header from `training/assignment_reports.py:246–251`.
  - Under the existing US-20 rule, the CSV leaves out answer keys and responses.
- Link to the record from the employee directory and from `employee_report`.

## Tests

Name each test after its observable behavior. They go in new `training/test_records_retention.py` and `training/test_schedules.py`, plus additions to `test_renewals.py`. Use the existing controllable clock and the local email sink.
- **Retention:**
  - Model guards (any database):
    - deleting an assignment raises
    - changing a set ending raises
    - completed together with canceled violates the constraint
  - Triggers (PostgreSQL only):
    - queryset `.update()` and `.delete()` of an attempt, a history row or a completed assignment's `completed_at` or `renewal_due_on` raise
    - a published `Question` can't be edited or moved to another version
    - a notification's status can still change, a finished delivery attempt can't
  - These still work: changing the deadline of an outstanding assignment, archiving and restoring, and resume updates.
  - The migration applies and reverses.
- **Renewal:**
  - a waiver gets a renewal date and starts the next cycle
  - a canceled assignment doesn't
  - every completion path stores `renewal_due_on`: slide, document and quiz
  - changing the interval leaves existing dates unchanged and records the change
  - backfilled dates are marked as reconstructed
- **Automatic renewal:**
  - creates the renewal at `renewal date − lead days`, due on the renewal date
  - a crash-and-retry reuses the stored attempt
  - publishing a new version on the same day doesn't break a retry
  - a backlog is due on `today + lead`
  - inactive employees and conflicts are retried later
  - a canceled automatic renewal isn't recreated
  - it doesn't run when the authorizing administrator was deactivated or demoted
  - (PostgreSQL) a concurrent manual assignment still leaves only one outstanding assignment
- **Calendar schedule:**
  - each occurrence runs once
  - after downtime, only the latest missed occurrence runs, and its due date is `today + due_days`
  - the run stores the group's membership at execution
  - a group of more than 100 is split into chunks
  - `renews` is linked and `cycle_number` continues
  - conflicts are final for that occurrence
  - an ended schedule doesn't run
  - automatic renewal and a schedule can't both be on for one training
- **Training record:**
  - shows completed, waived, canceled and archived cycles
  - waivers are labelled as exceptions
  - lapse boundaries and ongoing lapses are right
  - a completion from before the window counts toward coverage
  - historical names are shown
  - the CSV matches the page and neutralizes formula prefixes
  - only administrators can open it

## Verification

- `./scripts/test-local.sh` runs the full check, the PostgreSQL tests (including the trigger tests) and the static build.
- `.venv/bin/python manage.py makemigrations --check` shows no pending changes after each slice. Also run `migrate training 0019` and then `migrate` again on a scratch database.
- Manual check:
  1. `seed_demo`
  2. Give a training a 12-month interval and turn on automatic renewal.
  3. Back-date a completion with the test clock, then run `dispatch_notifications` once.
  4. Confirm the renewal and its email in the local sink.
  5. Open the employee's training record and export its CSV.
- Take screenshots of the queue, schedule editor and training record at 1280 and 390 px with the `screenshot-training-app-page` skill, for the PR.
- Ship as separate PRs: Slice A+B, then C, D, E, F. A and B first, because they protect the data before any automation writes more of it.
