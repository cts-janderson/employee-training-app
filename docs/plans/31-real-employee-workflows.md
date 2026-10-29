# Slice 31: real-employee workflows

This is step A of [production deployment](30-production-deployment.md) and step 4 of [email and password authentication](email-password-authentication.md). It lets signed-in real administrators and employees use the authoring, assignment, group, renewal, report and notification workflows. Demo data stays a separate local sandbox.

## Problem

About 45 queries and checks across 20 modules restrict administrator work to `is_demo=True`. Examples include `demo_admin_required` in `training/dev_auth.py`, `is_administrator` in `training/employees.py`, `training/assignments.py`, `training/notification_views.py` and `training/renewals.py`. A signed-in real administrator gets a 403 on every page except **Accounts**. Notification recipients must be demo employees with `.test` addresses (`_verified_recipient` in `training/notifications.py`).

## Design: the actor's population

Each employee belongs to one population: demo (`is_demo=True`) or real. **An administrator works only with employees in their own population.** Every `is_demo=True` filter becomes `is_demo=actor.is_demo`, applied through one helper module instead of at each call site. The result:

- With the stub on, a demo administrator sees exactly what they see today.
- A real administrator sees only real employees. Demo rows never appear in a real administrator's lists, reports, exports or notification queues, and a demo administrator can't act on real employees.
- With the stub off, no demo actor can be resolved (the existing guard), so in production only the real population is reachable.

The helpers live in `training/population.py`:

| Helper | Returns | Used by |
| --- | --- | --- |
| `employees(actor)` | Every employee in the actor's population, active or not | Employee list and detail, reports, exports, completion summaries, report filters, assignment and notification lookups (through `employee__is_demo`) |
| `assignable(actor)` | `employees(actor)` that are active and, for real employees, have `approval_status == APPROVED`. Verification and password setup aren't required, because an invitation implies approval. This deliberately avoids `account_state()`, which reports an unverified pending applicant as `UNVERIFIED`. | New assignments, group membership, renewal candidates. Checked again inside the locked submission in `assignments._plan_one`, not only when choices are built. |
| `is_administrator(actor)` | An active administrator. A demo actor qualifies; a real actor must also have `account_state == active`. | Replaces `employees.is_administrator` and the role checks in `training_imports.py` |

Inactive employees stay visible in history and reports, and administrators can still reactivate them. Existing inactive-recipient outcomes in assignment planning keep their current behavior.

**What is scoped, and how:**

- **The library is scoped (decided October 29, 2026).** Demo and real administrators each have a separate library:
  - `Training`, `EmployeeGroup` and `Quiz` get an `is_demo` field, set from the creating actor. On `Quiz` the field is used only for library quizzes, where `version` is null.
  - Migration `0017` marks every existing row `is_demo=True`, because local data is synthetic.
  - The library, search and browse, draft, preview, publish, renewal-interval, import and library-quiz pages filter by the actor's population.
  - Copying a library quiz into a draft (ADR-0003) needs both to be in the same population, and so does assigning a training to an employee.
  - The case-insensitive group-name constraint becomes unique per population.
  - Import batches are already scoped through their owner. Their check changes from "owner is demo" to "owner is an administrator in the same population".
- **Groups follow the same rule.** Group lists, lookups, edits and member choices filter by the actor's population, including existing members.
- **Functions without an actor take an explicit population.** `renewal_candidates`, recipient resolution in `assignments.py`, and the completion-report queries and entities each receive a population argument from the requesting administrator. They don't read a global value.
- **Isolation is enforced in the service layer.** Functions such as `retry_notification`, `reconcile_notification`, `request_manual_nudge`, assignment end and change, and employee update check the target's population against the actor. View-level `get_object_or_404` alone isn't enough.
- **Some code stays cross-population on purpose.** The notification worker and import cleanup cover both populations. The worker chooses the transport per recipient: the local sink for demo, the configured transport for real, which comes in plan 30 step C. Demo resolution, the switcher, `seed_demo` and `bootstrap_demo_admin` stay demo-only. Account authentication, link redemption and approval stay real-only.

## Changes

1. **Access decorators** (`training/dev_auth.py`): `demo_admin_required` becomes `admin_required`. It accepts an active real administrator (signed in) or a demo administrator (stub), and refuses everyone else with the existing messages. `demo_actor_required` is renamed `actor_required` and stays the learner entry point. The `views.py:37` home redirect sends every administrator to the training list.
2. **Scoped queries:** replace each `is_demo=True` and `employee__is_demo=True` call site in the modules listed above with the matching helper. Learner ownership checks (`employee=request.employee`) don't change.
3. **Employee management** (`training/employees.py`, `employee_views.py`):
   - A demo administrator keeps `create_demo_employee`, with `.test` addresses.
   - A real administrator's **Add employee** action becomes the existing `invite_employee` from `training/accounts.py`, which sends a setup link.
   - When a real administrator changes an employee's email, the address becomes unverified: `verified_email` and `email_verified_at` are cleared, and `link_generation` is bumped so old links stop working. Then the right link goes to the new address. An employee with a usable password gets a verification link; one who hasn't set a password yet gets a fresh setup link, so an invitation can still be recovered. Changing the email of the only usable real administrator is refused.
4. **Notification recipients** (`notifications.py:_verified_recipient`): require `employee.email_is_verified`. That still binds verification to the *current* address. A demo employee also needs a `.test` address. A real employee needs neither `is_demo` nor `.test`. An unverified recipient keeps the existing recoverable `BLOCKED` / `unverified_address` outcome (`next_state`), which an administrator can retry after the employee verifies. Real delivery still waits for plan 30 step C, because `LocalEmailSink` and `dispatch_notifications` reject real addresses until then.
5. **Deactivation and session version:**
   - Add `Employee.session_version`. Deactivating an employee increments it.
   - `Employee` overrides `get_session_auth_hash` and the fallback hash used for secret rotation to include both the password hash and `session_version`. That makes a session from before deactivation stay dead after reactivation, even if no request arrived in between.
   - With the stub on, Django flushes a session whose hash doesn't match before the decorator runs, so it could then fall back to the default demo administrator. To prevent that, sign-in sets a separate `real_session` marker cookie. `actor_required` treats a marker without an authenticated user as a dead real session: it clears the marker and redirects to sign-in. Sign-out clears the marker.
   - Deactivation also increments `link_generation`, so outstanding setup and reset links stop working.
   - An administrator can't deactivate their own account, or the last real administrator whose `account_state` is active. The check locks the administrator rows (`select_for_update`), so two concurrent deactivations can't both pass.
6. **Navigation:** real administrators see the full administrator navigation plus **Accounts**. The demo switcher still appears only when the stub is on and no real session exists.

## Out of scope

Login email change by the employee, granting or removing the administrator role, reapplication after rejection, and retention rules for rejected applications.

## Done when

- The existing workflow tests pass unchanged under the demo stub.
- New tests run representative flows as a **real** administrator: create and publish a training, invite an employee, assign it, take the quiz as the signed-in employee, see the report, export, send a nudge, renew, and end the assignment.
- Isolation tests: a real administrator gets a 404 for a demo employee, assignment or notification, and the same holds the other way round. Lists and exports leave out the other population.
- Library isolation tests: a real administrator can't list, open, edit, publish, import into or assign a demo training or library quiz, and the reverse also holds. A library quiz from the other population can't be copied into a draft.
- Service-layer isolation tests: calling the service functions directly with a target from the other population is refused. Groups and history are isolated. A learner can't open another employee's assignment or attempt in the same population.
- Eligibility tests: pending and unverified applicants can't be assigned. An invited employee who hasn't set up an account can be. If eligibility changes while an assignment is being submitted, the locked recheck catches it.
- Notification tests: a real employee with a verified address passes the recipient check. An unverified invited employee is `BLOCKED/unverified_address`, and a retry after verification succeeds. After an email change, the notification is blocked until the new address is verified.
- Email-change tests: old links fail; the setup link sent to the new address works; the sole administrator's email change is refused.
- Deactivation tests: the session ends and stays ended after reactivation, with no request in between. Links stop working. With the stub on, a dead real session redirects to sign-in and never becomes the demo administrator. The last real administrator can't be deactivated, even by two concurrent requests. A session survives `SECRET_KEY` rotation through `SECRET_KEY_FALLBACKS`.

## Review

Codex reviewed this plan against the code on October 29, 2026, and its corrections are folded in above:

- Functions without an actor take an explicit population.
- Groups are scoped. Later, at the owner's request, the training library is scoped too.
- Isolation is enforced in the service layer.
- The `assignable` rule is a precise predicate that doesn't use `account_state`, and it is rechecked inside the lock.
- The fallback hash also includes the session version.
- A dead real session can't fall back to the demo administrator.
- Unverified recipients keep the existing recoverable outcome.
- An email change preserves setup recovery.
- The last-administrator check is serialized.
- `./scripts/test-local.sh` passes, `makemigrations --check` is clean, and the administrator pages are checked in a browser as a real administrator.

## Implementation status

Implemented locally on October 29, 2026:

- **New code:** `training/population.py`, migration `0017_population_scoping` and `update_employee` in `training/employees.py`, which replaces `update_demo_employee`.
- **Access checks:** `admin_required` and `actor_required` in `training/dev_auth.py` replace the demo-only decorators. `admin_required` checks every URL id against the actor's population through `route_in_population`.
- **Tests:** `training/test_real_workflows.py` covers the following:
  - The separate libraries.
  - Service-layer refusals.
  - Rules for who can be assigned training.
  - Learner ownership.
  - Recipient verification.
  - Email-change recovery.
  - Deactivation sessions and the demo fallback.
  - `SECRET_KEY` rotation.
  - The last-administrator rule, including a PostgreSQL race.

  The full PostgreSQL suite passed: 336 tests, along with `check`, `makemigrations --check` and `collectstatic`.

Two bugs that the new tests found were fixed before these results:

- **Deadlock:** `update_employee` now locks the administrator rows before the target row. Two administrators deactivating each other at the same time deadlocked before this change.
- **Demo fallback:** the dead-session marker stays until the next sign-in or an explicit demo profile choice. Before this change, it was cleared on the first redirect, so the second request became the demo administrator.

**Follow-up checks, October 29, 2026:**

- A migration test checks that `0017` marks existing trainings, library quizzes and groups as demo, and that group names are unique per population.
- The pages were checked against a throwaway SQLite demo database seeded by `seed_demo`, with a real administrator, a real employee and an invited employee added:
  - Every administrator page returned 200 for the real administrator.
  - Every demo training, employee, assignment, library quiz, group and notification returned 404.
  - No demo name or `.test` address appeared on any saved page or in the CSV export.
  - The real employee saw only their own assignment.
- Desktop and phone screenshots found one defect. The sidebar **Sign out** button, added in Slice 29, showed dark text on a blue button. It now matches the sidebar links.

- For a real administrator, the **Employees** form now reads **Invite employee** with a **Send invitation** button, and says that the employee sets their own password. The directory and the edit page show **Awaiting setup** for an invited employee and **Email unverified** after an email change, using `employee_status` in `training/employees.py`. The full suite passed: 339 tests.

Still open:

- Real email delivery still waits for [plan 30](30-production-deployment.md) step C.
