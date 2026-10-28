# Email and password authentication plan

**Status:** Deferred implementation  
**Date:** October 28, 2026

Replace the local demo identity switcher with email and password sign-in. Employees may register themselves, subject to administrator approval; administrators may also create employee records. Keep training history attached to the existing permanent employee ID so an email change does not create a new person or transfer their records.

This plan supersedes the Entra sign-in, provider account-binding, and prohibition on self-registration in the [MVP requirements](../mvp.md), [implementation baseline](../implementation-plan.md), and [stack decision](../adr/0002-application-stack.md). Implementation is future work. Existing deployment and real-data readiness gates still apply.

## Confirmed decisions

- Employees use Microsoft 365, but this application will authenticate its own accounts using email and employee-chosen passwords.
- Store password hashes using Django's password management facilities. Never store readable passwords or implement a custom hashing scheme.
- Employees can register themselves. Administrator approval is required before a self-registered employee gains application access.
- Administrators can create employee records without requiring the employee to register first.
- Email addresses may change. Preserve employee IDs, assignments, attempts, completion evidence, and profile history through those changes.

Microsoft 365 account deactivation will not automatically deactivate a separate application account. Include application deactivation in the offboarding procedure.

## Proposed enrollment behavior

These defaults complete the workflows and should be reviewed when implementation starts.

| Path | Setup | Access |
| --- | --- | --- |
| Employee registers | Employee supplies name, email, and password, then verifies control of the email address. | Pending applicants can see their own setup or approval status. Training access requires verification and administrator approval. |
| Administrator creates employee | Administrator supplies name and email. Creation records approval and sends an account-setup link; the employee verifies the address and chooses a password. | The administrator may assign training before account setup finishes. The employee gains access after setup, provided the account is active. |

- Keep email verification, approval, and active/inactive status separate. Verification proves mailbox control; approval authorizes enrollment; deactivation suspends access while preserving history.
- Approving an application grants the ordinary employee role. Administrator rights require a separate privileged action.
- Give administrators a queue of pending applications with approve and reject actions. Record the deciding administrator and decision time.
- An applicant cannot approve themselves, choose an administrator role, or obtain access through a direct URL while pending or rejected.
- If a record already exists for an email, direct its owner through account setup or recovery without creating a second employee. Do not replace an existing password or modify the existing profile merely because someone submitted a registration form.
- For an administrator-created record without credentials, verify mailbox control before establishing the password. Preserve the original employee ID and any preassigned training. Existing inactive accounts remain inactive through setup and recovery.
- Use neutral public responses where appropriate so registration and recovery do not reveal another person's approval status or account details.

## Implementation sequence

### 1. Establish account and access rules

- Choose how Django's authentication user relates to the existing [Employee model](../../training/models.py). Keep one authoritative source for login email, application role, and activity; preserve existing employee foreign keys. Review the migration implications before choosing a custom user model or a separate linked account.
- Represent enrollment approval and email verification explicitly, with appropriate timestamps and actors. Pending applicants must not become eligible assignment recipients merely because an employee row exists.
- Retain database-enforced case-insensitive email uniqueness. Use a consistent normalization and lookup policy across registration, administrative creation, sign-in, and email changes.
- Make duplicate registration, account setup, approval, and administrative creation safe under concurrent requests. A repeated action must not duplicate employees, credentials, or decisions.
- Define one server-side access rule for verified, approved, active accounts, with separate permission checks for administrators and assignment ownership. Restrict pending sessions to enrollment status and related setup actions.
- Provide a one-time operator command to create the first real administrator without public registration or a permanent bootstrap bypass.

### 2. Implement sign-in and account maintenance

- Add Django authentication integration, sign-in, logout, password change, password recovery, and browser sessions. Use Django password setters, checkers, validators, and standard recovery facilities where applicable.
- Add login and registration abuse controls, including rate limiting; Django's default authentication backend does not provide login rate limiting.
- Require HTTPS in deployment and retain secure cookie and CSRF protections. Rotate the session on successful login and invalidate sessions appropriately after password recovery.
- Check current account activity, approval, and role on protected requests so deactivation or demotion affects existing sessions.
- Keep email changes attached to the same employee ID, audit them, require verification of the replacement address, and prevent address collisions. Specify when the login address changes and how queued training emails handle verification before implementing the flow.

### 3. Implement both enrollment paths

- Build public registration, email verification, restricted enrollment status, and the administrator approval queue.
- Extend employee management to create real employee records and issue account-setup invitations. Administrators do not choose or view employee passwords.
- Use purpose-specific, expiring links for verification, setup, and recovery. Consumed or superseded links cannot be reused; a link alone cannot bypass approval or reactivate an account.
- Record approval, rejection, and administrator role changes alongside the existing profile and activity history.
- Define an intentional path for rejected applicants to reapply; repeated registration must not silently reset a rejection or deactivation.

### 4. Remove demo assumptions from production workflows

- Replace the request identity decorators in [dev_auth.py](../../training/dev_auth.py) with the real access boundary. Keep any demo switcher explicitly limited to synthetic local development.
- Review [employee management](../../training/employees.py), [employee views](../../training/employee_views.py), and all uses of `is_demo`, `.test` validation, and `request.employee`. The current administrator check explicitly requires a demo employee.
- Update assignment creation, groups, renewals, reports, and notification recipient selection so approved real employees work throughout the application. Do not simply remove demo filters without replacing their eligibility and environment checks.
- Preserve learner ownership checks and server-side administrator permissions on all reads, writes, exports, and answer-key access.
- Replace the demo-switch navigation with account controls and add registration, pending approval, and account-setup interfaces.
- Keep synthetic fixtures and demo records separate from live accounts. Do not convert synthetic training history into real employee evidence.

### 5. Connect email and document operation

- Supply configurable sender and application URL settings and reliable delivery for setup, verification, and recovery emails. Use a test sink during development; the current in-memory sink cannot deliver account setup to employees.
- Coordinate authentication email with the existing [notification module](../../training/notifications.py). Its current recipient checks require demo employees and `.test` addresses; retain verified-recipient checks when supporting real addresses.
- Document first-administrator setup, approval, invitation resend, email correction, password recovery, role changes, and offboarding.
- Update the MVP requirements and implementation baseline to replace the superseded auth details and revise affected acceptance criteria. Preserve existing story IDs, especially learner ownership in US-08 and authenticated notification links in US-06/US-07.
- Prove migrations, deployment configuration, worker operation, and email delivery before rollout. Authentication implementation alone does not satisfy the [MVP release criteria](../implementation-plan.md#mvp-release-criteria).

## Decisions to settle before implementation

- Whether registration is restricted to organization email domains and how contractors or other approved exceptions enroll.
- Whether verification is required before an application enters the approval queue; the proposed default is yes.
- Whether administrator creation implies approval and allows assignment before account setup; the proposed default is yes.
- How rejected applications may reapply, and how long pending or rejected application data is retained under the approved data policy.
- Who can grant or revoke administrator rights and how to prevent removal of the last active administrator.
- Link lifetimes, resend limits, session expiry, and the administrator-assisted recovery procedure if an employee loses mailbox access.
- Who may change a login email and whether the existing address remains usable until the replacement is verified.

## Acceptance and verification

Use synthetic employees, a controllable clock, and an email sink for automated verification. Include PostgreSQL transaction checks where uniqueness or concurrent actions matter.

- A self-registered employee cannot access training before both verification and approval, regardless of which happens first. Approval and verification do not reactivate an inactive employee.
- Ordinary employees, pending applicants, and anonymous requests cannot approve applications or grant roles. Successful approval records its administrator and time.
- An administrator creates an employee, assigns training, and sends setup instructions. The employee chooses a password and sees those assignments under the original employee ID.
- Registration against an existing email cannot overwrite credentials, profile details, approval, or activity. Concurrent registration and administrative creation produce one employee record.
- Passwords are stored as Django-managed hashes. Valid passwords authenticate; invalid passwords fail; recovery and change flows behave as intended, including session invalidation and rate limits.
- Expired, consumed, superseded, wrong-purpose, and modified setup links fail. Recovery neither grants approval nor changes role or activity.
- Deactivation blocks an existing session; administrator demotion removes administrator access; one employee cannot open another employee's assignments or results.
- An email change preserves assignments and history, enforces uniqueness and verification, and prevents delivery to an unverified replacement address.
- Real-account workflows work without `is_demo=True`. Demo access remains unavailable on a deployed instance, and synthetic accounts cannot obtain production privileges.
- Registration, pending approval, rejection, sign-in errors, and setup pages are reviewed in the browser. Run focused auth and permission tests, the existing workflow suite, Django checks, and migration checks before declaring the implementation complete.

## References

- [Django authentication](https://docs.djangoproject.com/en/5.2/topics/auth/default/)
- [Django password management](https://docs.djangoproject.com/en/5.2/topics/auth/passwords/)
- [Django authentication customization and backend limitations](https://docs.djangoproject.com/en/5.2/topics/auth/customizing/)
