# Slice 29: sign-in skeleton

Part of [Email and password authentication](email-password-authentication.md). This slice builds the account flows from steps 1–3 of that plan against Django's local email backend. It doesn't connect an email provider, an external API, or a deployment. Step 4 (moving the administrator workflows off `is_demo`) and step 5 (email delivery and operation) stay open.

The design choice is recorded in [ADR-0009](../adr/0009-employee-is-the-authentication-user.md): `Employee` becomes Django's authentication user.

## Scope

| Area | In this slice |
| --- | --- |
| Account model | `Employee` gains `password`, `last_login`, `email_verified_at`, `link_generation` and an approval decision (`approval_status`, `approval_decided_at`, `approval_decided_by`). Existing rows become approved, with unusable passwords. Any employee saved without a password gets an unusable one. |
| Access rule | One function, `account_state(employee)`: `inactive`, `unverified`, `pending`, `rejected` or `active`. Only `active` reaches training pages. Other signed-in states see only their account status page. |
| Sign-in | Email and password sign-in, sign-out, password change. The session rotates on sign-in and drops any demo choice. A password reset ends every other session; a password change keeps the current one and ends the others. A deactivated employee's session stops authenticating on the next request. |
| Registration | Name, email and password. Creates a pending, unverified employee and sends a verification link. A registration for an existing email changes nothing and sends that address a notice instead. The response is the same either way. |
| Verification | A link proves mailbox control. A verified, pending applicant enters the approval queue. |
| Approval | Real administrators approve or reject verified applicants. The decision records the administrator and the time. |
| Invitation | Real administrators create an employee by name and email. The employee is approved at creation. A setup link lets the employee verify the address and choose a password. Setup links can be resent. |
| Recovery | Password reset by email for accounts with a usable password. The reset doesn't change approval, role or activity. |
| First administrator | `manage.py create_administrator` creates one real administrator when none exists. |
| Abuse controls | Per-address and per-client limits on sign-in, registration, reset and resend requests, using Django's cache. |
| Email | Sent through `EMAIL_BACKEND` (the in-memory sink locally). With the demo stub enabled, the link is also written to the server log so local testing can follow it. |

## Decisions taken for the skeleton

The parent plan lists decisions to settle. This slice takes its proposed defaults, so each can be changed later without changing the model:

- Registration accepts any email domain.
- Verification comes before approval: an applicant appears in the queue only after verifying.
- An invitation implies approval.
- A rejected applicant can sign in and see the rejection, and can't reapply. Reapplication is future work.
- Links expire after `PASSWORD_RESET_TIMEOUT` (one day here). Links are signed tokens, not stored rows. Each purpose uses its own salt, so a link can't be used for another purpose. Every link sent bumps `link_generation`, so it supersedes all earlier links for that employee. A verification link also stops working once the address is verified or changes; a setup or reset link once the password changes or the employee signs in. Redemption locks the employee row and rechecks the token, so two tabs can't both use one link.
- Granting or removing the administrator role, and changing a login email, are out of scope. Administrators keep using the existing role assignment for demo employees.

## Separation from demo data

- Real accounts have `is_demo=False`. Demo employees keep unusable passwords. The authentication backend refuses them both at sign-in and when it restores a session, and no link is issued to or redeemed by a demo employee.
- A real session takes precedence over the demo switcher, and the switcher refuses while one exists. A real session that stops authenticating (for example after deactivation) is signed out and sent to sign-in; it never falls back to a demo profile. Signing out flushes the session.
- Real employees with an `active` state reach the learner pages (**My training**). Real administrators reach the new **Accounts** page. The existing administrator pages still require a demo administrator, because their queries and checks are scoped to `is_demo`. A signed-in real administrator gets a 403 there with an explanation, and their navigation hides those links.
- Only real administrators decide applications and send invitations. A demo administrator can't approve a real account.
- With the stub off, unauthenticated requests to protected pages redirect to sign-in instead of returning "Sign-in is not connected yet."

## Not in this slice

- Moving assignment, group, report, renewal and notification queries from `is_demo` to an eligibility rule for real employees (parent plan step 4).
- Login email changes, administrator role changes, last-administrator protection, reapplication, and retention of rejected applications.
- A provider-backed email backend, sender configuration review, and a shared cache for rate limits in deployment (parent plan step 5). The in-process cache reset on restart and wasn't shared between workers, so rate limiting blocked rollout. [Plan 30](30-production-deployment.md) step D replaced it with a PostgreSQL counter table.
- A session version that a deactivation bumps. Today a deactivated employee's session stops working, but it would work again if the employee were reactivated before it expired. Real-employee deactivation has no interface yet, so this lands with it.

## Review

Codex reviewed this plan before implementation. Its corrections are folded in above: links are superseded through `link_generation`, redemption is atomic, the confirm views refuse demo, inactive and (for setup) already-set-up employees, demo rows get unusable passwords on every creation path, dead real sessions don't fall back to demo, emails are sent after the transaction commits, and the session claims are stated precisely. Its other points are listed under **Not in this slice**.

## Done when

- Tests cover: registration and verification in either order with approval; registration against an existing email changes nothing; invited employees keep their ID and preassigned evidence; wrong-purpose, consumed and modified links fail; inactive and demo accounts can't sign in; deactivation ends a session; pending and rejected sessions can't reach training pages; non-administrators and demo administrators can't decide applications; rate limits return 429.
- PostgreSQL test: concurrent registration and invitation for one address create one employee.
- The full suite, `manage.py check` and `makemigrations --check` pass on PostgreSQL.

## Implementation status

Implemented locally on October 29, 2026, in `training/accounts.py`, `training/account_views.py`, the access decorators in `training/dev_auth.py`, migration `0016_employee_accounts` and `manage.py create_administrator`. `training/test_accounts.py` covers the list above, including the PostgreSQL race and the 0015→0016 upgrade. The full PostgreSQL suite passed: 326 tests. A copy of the local development database migrated cleanly: 8 existing employees, all approved with unusable passwords. The pages haven't been reviewed in a browser yet.

The document migration test used to restore the schema to `0015` by name, which left later tests without newer columns. It now restores the newest migration.
