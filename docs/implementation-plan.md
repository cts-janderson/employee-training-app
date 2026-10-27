# Employee Training MVP Implementation Plan

**Source:** [MVP requirements](mvp.md)  
**Status:** Confirmed implementation baseline  
**Date:** October 26, 2026

Deliver the MVP through eight vertical slices. Each slice includes the interface, server behavior, persistence, authorization, and verification needed for a usable workflow. The local authoring, assignment, quiz, monitoring, and notification flows now exist, including scheduled reminders and manual nudges. The [stack decision](adr/0002-application-stack.md) is recorded; organizational constraints and deployment proof remain open setup gates below.

For local development, core workflows may use a loopback-only synthetic administrator and an email sink while the Entra sign-in and outbound-email integrations are deferred. Stub behavior does not satisfy the authentication, notification, or deployment acceptance criteria below. Keep all real employee data and live use behind those release gates.

The sequence below turns the broad phases in the MVP into demonstrable increments. Early slices are for demonstrations and testing; live employee use begins only after all eight slices and the release criteria are satisfied. Deploy a working skeleton in Slice 1 to validate hosting, authentication, and background-job constraints before building the remaining workflows.

## Planning assumptions and decisions

The following scope decisions were confirmed on October 26, 2026. Shared domain terms are defined in [GLOSSARY.md](GLOSSARY.md).

| Confirmed decision | Policy | Needed by |
| --- | --- | --- |
| Organization and roles | One organization with organization-wide administrators and individual employee identities. Administrators can also receive training. | Slice 1 |
| Release boundary | Early slices are demonstrations and tests. Live employee use begins after Slice 8 and all release checks; deployment starts with a working skeleton in Slice 1. | Slice 1 / release |
| Quiz optionality and completion | Permit zero or one quiz per version. All slides require acknowledgment; a quiz, when included, also requires a passing attempt. Acknowledgment records an employee action, not verified attention. | Slices 1–2 |
| Duplicate assignments and replacement | Allow at most one active, incomplete assignment per employee and logical training across versions. Use separate Cancel and Assign actions: require a cancellation reason, then select the replacement version and deadline. Preserve the old evidence; start new progress from zero. See [ADR 0001](adr/0001-one-outstanding-assignment-per-training.md). | Slices 2 / 5 |
| Employee deactivation | Preserve assignments and history, reject access and new assignments, and suppress reminders. Exclude inactive employees from the default actionable-work view; cancellation remains explicit. | Slices 2 / 5–8 |
| Reminder configuration | Use fixed reminder rules. Administrators change assignment deadlines; there is no reminder-policy configuration screen in the MVP. | Slices 5–8 |
| Slide authoring | Plain-text headers, bodies, and optional footers, preserving paragraphs and line breaks. Formatting tools and media are outside the MVP. | Slice 1 |
| Enrollment and first administrator | Administrators create employee records before sign-in. Use provider-managed authentication, no public self-registration, and a one-time operator procedure to bootstrap the first administrator. Initial linking requires a provider-verified email matching an active, unlinked employee record; subsequent sign-in uses the provider's stable account identity. Email changes never transfer the binding. | Slices 1–2 |
| Slide navigation and progress | Employees navigate freely, explicitly acknowledge every slide, and resume from saved acknowledgments and position. All acknowledgments are required before quiz submission or acknowledgment-only completion. | Slice 2 |
| Assessment rules | Unlimited retries until completion; equally weighted questions; no partial credit; whole-number passing thresholds from 1 through 100. Compare the unrounded score inclusively with the threshold and round only for display. Stop new attempts after completion. | Slices 3–4 |
| Learner result visibility | Show submitted answers, overall score, pass/fail, and attempt history. Withhold per-question correctness, correct answers, and grading configuration, including after completion. Unlimited retries may still permit answer inference. | Slices 3–4 |
| Reactivation | Restore access to existing active assignments with unchanged progress and deadlines. Do not create assignments, extend deadlines, or send a new assignment email automatically. | Slice 2 |
| Historical identity | Retain stable employee identifiers and audit name/email changes. Ordinary reports show current names; profile changes do not transfer training history to another person. | Slices 2 / 5 |
| Deadlines and edits | A selected date covers the entire calendar day in the configured organization time zone. Newly selected dates must be today or later. Keep existing overdue work accessible. Edit deadlines only for active, incomplete assignments, recompute current status, and retain old/new values and the actor/time in history. Completed deadlines remain fixed. | Slices 2 / 5 |
| Scheduled reminder timing | At 09:00 organization time, three calendar days before the due date and on the due date. For newly created assignments, skip elapsed reminder times, retain future ones, and queue the assignment email immediately. | Slices 6–7 |
| Manual nudge limits | One accepted manual nudge request per assignment in a rolling 24 hours, enforced across administrators. Scheduled reminders are independent and may arrive nearby; show the next scheduled reminder beside the nudge action. | Slice 8 |
| Stale assignment email | Suppress unsent assignment emails when work has completed, been canceled, or belongs to an inactive employee. Record the suppression reason. | Slice 6 |
| Deployment shape | One application, one relational database, and a background worker from the same codebase. Select framework, hosting, authentication, and email services before their dependent integrations. | Slice 1 |
| Unfinished drafts | Save incomplete slides and questions, display actionable validation problems, and block publication until the entire training is valid. Reject structurally invalid data on save. | Slices 1 / 3–4 |
| Deadline-change email | An actual deadline change immediately queues an email with the new date, using the shared delivery queue and assignment eligibility checks. | Slices 5–6 |
| Missed reminders | After an outage, send at most the latest eligible missed scheduled reminder. The due-day reminder supersedes the three-day reminder; both expire at the deadline. Reactivation resumes only future reminders. Overdue follow-up uses manual nudges. | Slice 7 |
| Delayed email content | Before dispatch, use the current verified recipient address, current assignment deadline, and title from its immutable published version. Recheck eligibility and preserve the recipient and content actually sent in delivery history. | Slices 6–8 |
| Real-data readiness | An approved employee-data retention/access policy and named operational owner are required before real employee data is introduced. Use demonstration data until then, preserve evidence, and add no automatic deletion until the policy defines retention. | Before real data / release |

The interview's product-policy decisions are settled. The following implementation gates remain intentionally open; they are concrete prerequisites for building or operating the selected design.

| Gate | Required result | Needed by |
| --- | --- | --- |
| Stack selection | Record framework, database, hosting, identity provider, email provider, and organizational constraints before building the Slice 1 authoring workflow. Confirm that these choices support the accepted account-linking, email-verification, and background-job requirements. | Slice 1 setup |
| Deployment proof | Demonstrate authenticated access, database migrations, and background execution on the intended deployment. Document local run/check and deployment procedures. | Slice 1 |
| Real-data authorization | Record the named operational owner and approved retention/access policy before loading real employee data. Keep demonstrations on test data and preserve evidence; automatic deletion remains outside the implementation until policy and scope authorize it. | Before real employee data |
| Production readiness | Configure the organization time zone and sender, verify email delivery and database restore, and document operation/recovery procedures. Satisfy every release criterion before live employee use. | Release |

These policies can be fixed application rules initially. A general-purpose policy configuration interface is outside the MVP.

## Delivery sequence

| Slice | Demonstrable user outcome | Depends on | Requirements advanced |
| --- | --- | --- | --- |
| 1 | Administrator authors and publishes stable slide-based training. | Initial setup | US-01, US-02, US-04 |
| 2 | Administrator assigns training; an employee reads and completes it. | 1 | US-05, US-08, slide portion of US-09 |
| 3 | Employee passes or retries a single-choice/true-false assessment. | 2 | Part of US-03; assessment portion of US-09; US-10 |
| 4 | Administrator and employee use all four question formats. | 3 | Completes US-03 and question-format coverage for US-09/US-10 |
| 5 | Administrator finds outstanding work and reviews completion evidence. | 2–4 | US-11; deadline and cancellation controls needed by US-07 |
| 6 | Employee receives durable assignment and deadline-change emails. | 2, 5 | US-06; confirmed extension to US-07 |
| 7 | Employee receives timely deadline reminders. | 5–6 | US-07 |
| 8 | Administrator sends a controlled, auditable manual nudge. | 5–7 | US-12 |

### Slice 1 — Author and publish versioned training

**Outcome:** An administrator creates training, arranges its slides, previews it, and publishes a stable version.

**Build:**

- Resolve the stack-selection gate before the authoring workflow: record the selected framework, database, hosting, identity provider, email provider, and organizational constraints. Select services that satisfy the accepted policies; provider-specific configuration belongs in setup documentation.
- Establish the application, relational database migrations, authentication, administrator access, and a repeatable local run/check workflow as part of delivering the authoring screen.
- Keep the application and background worker in one codebase, sharing the relational database and domain rules.
- Integrate provider-managed authentication and a one-time first-administrator setup procedure. Sign-in requires an existing active employee record; public self-registration is outside scope. The employee-management interface follows in Slice 2.
- On initial linking, require a trusted provider's verified email to match an active, unlinked employee record. Atomically bind the provider issuer and stable account identifier so an account or employee cannot be claimed twice. Subsequent authentication uses that binding; an email change never triggers automatic relinking.
- Enforce current employee activity and role on every protected operation so an existing session cannot retain access after deactivation or demotion.
- Deploy a working skeleton to the intended hosting environment and verify authenticated access, a database migration, and the ability to run a background job. Use demonstration/test data until the full MVP is released.
- Provide training list, draft editor, preview, and publish actions. Support title, description, and slide create/edit/remove/reorder with plain-text header, body, and optional footer. Preserve paragraphs and line breaks when rendering.
- Save incomplete draft content with actionable validation messages. Enforce structural integrity on save, and require all publication rules only when publishing. Detect stale administrator edits rather than silently overwriting newer work.
- Persist `employees`, `trainings`, `training_versions`, and `slides`, with foreign keys and unique version numbers and slide positions. Save reorder operations transactionally.
- Validate publication, including a title and at least one valid slide. Reserve zero-or-one quiz per version for the assessment slices.
- Make every published field immutable, including the title and description shown to learners. Snapshot display metadata on the version as needed; the MVP's conceptual schema is not complete DDL.
- Create a new draft by copying a published version. Editing the draft must leave the previous published content intact.

**Acceptance and verification:**

- Create a three-slide draft, reorder it, reload, and verify the saved sequence and preview match.
- Verify plain-text rendering preserves paragraphs and line breaks, and treats markup as text.
- Demonstrate the deployed skeleton, authenticated access, migration workflow, and a background-job smoke check.
- Reject initial linking for unverified email, unmatched/inactive employees, or an already-linked employee/account. Verify an email change does not transfer the binding and a deactivated employee cannot use an existing session.
- Save and reload an unfinished draft, verify publication identifies its missing content, then complete it and publish successfully.
- Publish version 1, create and edit version 2, and verify version 1 remains unchanged. Drafts never become assignments automatically.
- Reject invalid publication, direct mutation of published content, and employee access to administrator operations on the server.

**Demo:** An administrator publishes “Data Security” version 1 and starts a revised draft without changing the published training.

### Slice 2 — Assign, read, and complete slide-based training

**Outcome:** An administrator assigns a published version to employees, and each employee can complete their own training and see its deadline status.

**Build:**

- Add a minimal employee directory: create/update name and email, enroll employees through the selected provider, link authenticated identity, and activate/deactivate employees. Preserve stable employee identity and audit name/email changes; ordinary views use current profile values. Preserve historical records and outstanding assignments when an employee is deactivated; reject access and new assignments for inactive employees. Deactivation does not complete or cancel assignments.
- Reactivation restores access to active assignments with their original progress and deadlines. It does not create new obligations, extend deadlines, or queue a fresh assignment email. Reminder eligibility follows the separately defined scheduling policy.
- Add single and bulk assignment creation with a due date, explicit version selection, and per-employee success/conflict feedback. Enforce duplicate policy under concurrent requests.
- Process bulk recipients with independent outcomes. A replay of the same creation operation returns its original per-employee results, even if an assignment has since completed or been canceled; it never creates a new obligation or email. An intentional new operation is distinct and is checked against the current duplicate policy. Report an incompatible existing assignment as a conflict rather than silently changing its version or deadline.
- Persist one `assignments` row per recipient, plus assignment-scoped slide progress such as `assignment_slide_progress`, unique by assignment and slide. Record assignment time and first-start time where needed.
- Provide “My training,” assignment detail, freely accessible ordered slide navigation, saved acknowledgments and resume position, and a completion result. Administrators use the same personal workflow when assigned training. Navigating to a slide does not acknowledge it automatically.
- Preserve the selected due date and its organization time zone, and calculate `due_at` as the exclusive start of the following local calendar day converted to UTC. Completion before that instant is on time; at or after it is late. This includes the full final second of the selected day. Display the due date and organization time zone clearly.
- Validate newly selected due dates against today in the organization time zone. Reject dates earlier than today; this does not invalidate assignments that have naturally become overdue.
- Enforce ownership and assigned-version access on every read and write. Accept progress only for slides belonging to the assigned version.
- Evaluate slide-only completion on the server and set `completed_at` once. Derive `not_started`, `in_progress`, and `completed` separately from `on_time`, `overdue`, and `completed_late`.
- Enforce the same version pinning when a newer version is published. Reassignment creates a new obligation under the duplicate policy; it never rewrites historical evidence. An unfinished assignment must be explicitly canceled before its replacement is created. The cancellation workflow arrives in Slice 5.

**Acceptance and verification:**

- Assign to two employees and verify distinct obligations reference one version. Repeating or concurrently submitting the request cannot create forbidden duplicates.
- Replay a bulk operation after one recipient has completed their assignment and another has a conflict; verify the original results are preserved and no recipient gains another assignment or notification. A deliberate new operation may assign again after completion or cancellation.
- Read some slides, sign out and back in, resume, and finish. Repeating a progress request does not reset progress or change an existing completion timestamp.
- Verify no completion before all slides are acknowledged, and verify an employee cannot access another employee's assignment or spoof progress for unrelated slides.
- Advance a controlled clock across the deadline: unfinished work becomes overdue, remains accessible, and subsequently completes late. Completion in the final fraction of the due date is on time; completion exactly at the following day's boundary is late. Cover calendar-day boundaries across daylight-saving transitions.
- Accept today as a new due date and reject yesterday, using the organization's date even when the browser's local date differs.
- Publish a revised training version and verify an existing assignment still displays its original content.
- Deactivate an employee with unfinished training and verify access and new assignments are rejected while the existing assignment and its progress remain intact.
- Reactivate that employee and verify their original progress and deadline return unchanged, including overdue status where applicable, without a new assignment or assignment email.
- Change an employee's name/email and verify the same employee retains their evidence, ordinary views use the current profile, and the prior values remain in profile history.

**Demo:** Two employees receive the same training; one finishes on time while the other resumes and completes after the deadline.

### Slice 3 — Assess understanding and preserve retries

**Outcome:** An administrator adds a quiz, and an employee can fail, retry, pass, and review the recorded results.

**Build:**

- Extend the draft editor with one optional quiz, ordered single-choice and true/false questions, answer configuration, and a validated integer passing threshold from 1 through 100.
- Persist `quizzes`, `questions`, `question_options`, `quiz_attempts`, and `attempt_responses`. Keep grading rules within the immutable published version.
- Permit incomplete question prompts, choices, and grading configuration in saved drafts while enforcing structural integrity. At publication require valid question types, complete prompts/choices, valid grading rules, and at least one gradable question in every included quiz.
- Provide employee quiz rendering, submission, results, history, and retry actions. Require all slides to be acknowledged before quiz submission. Show submitted answers, overall score, pass/fail, and attempt history without per-question correctness or correct answers, including after completion.
- Validate response membership and completeness and calculate grades on the server. Reject client-supplied scores and responses referring to another quiz or version.
- Commit an immutable attempt, its responses, and any resulting completion in one transaction. Make retried delivery of the same submission idempotent while allowing a deliberate retry to create a new attempt.
- Apply the completion rule consistently: acknowledging slides alone cannot complete training that has a quiz. Failed attempts remain in progress; a passing attempt completes it once.
- Allow unlimited retries until completion. Weight questions equally, award no partial credit, and compare the unrounded percentage inclusively with the threshold. Round only for display. Reject new attempts after completion while returning the original result for an idempotent replay of a previously accepted submission.

**Acceptance and verification:**

- Author, publish, assign, fail, retry, and pass a mixed single-choice/true-false quiz; preserve both attempts and their original scores.
- Verify passing exactly at the configured threshold and failing just below it even if display rounding makes the scores appear equal.
- Verify a quiz cannot publish with no questions, invalid correct-answer rules, or a threshold outside the integers 1–100. Employee payloads never include answer keys or per-question correctness.
- Save and reload an unfinished quiz question; verify its validation problems block publication without losing the draft.
- Repeat a submission after a simulated timeout and verify only one attempt is recorded. Verify concurrent passing submissions cannot overwrite the first completion timestamp.
- Verify a new attempt after completion is rejected while replaying the accepted submission returns the original result without another attempt.

**Demo:** An employee fails an assessment, reviews the result, retries successfully, and sees both attempts alongside their completion.

### Slice 4 — Support multiple-selection and ordering questions

**Outcome:** Administrators can author and employees can complete every question format required by the MVP.

**Build:**

- Extend the authoring interface, publication validation, learner controls, persisted responses, and server grader together for multiple-selection and ordering questions.
- Store a correct option set for multiple-selection and an explicit required sequence for ordering. Do not overload a single `is_correct` flag to represent both.
- Keep learner presentation order separate from the required grading sequence; learner payloads must omit canonical answer ranks as well as correctness flags.
- Grade multiple-selection by exact set equality and ordering by exact sequence equality, with no partial credit. Require ordering responses to contain every expected option exactly once.
- Provide keyboard-operable selection and reordering controls, plus readable submitted-response history.
- Apply the existing retry, versioning, completion, and answer-key protections to both formats.

**Acceptance and verification:**

- Complete a published quiz containing all four formats and verify the combined score and threshold outcome.
- Cover reordered selections, extra/missing selections, wrong order, duplicate or foreign option IDs, and incomplete sequences. Reject malformed submissions without recording a valid graded attempt.
- Inspect ordering-question payloads and rendering to verify they do not expose the required sequence through canonical ranks or by reusing grading order as presentation order.
- Publish changed grading rules in a new version and verify prior assignments and attempt results retain their original meaning.

**Demo:** An administrator publishes a four-format quiz; an employee completes it and can review each submitted response.

### Slice 5 — Monitor progress and manage outstanding assignments

**Outcome:** Administrators identify who needs follow-up, inspect evidence, and maintain deadlines without losing history.

**Build:**

- Provide an assignment dashboard filtered by employee, training, progress, deadline status, and employee activity. Define pending as active and incomplete; display cancellation separately. The default actionable-work view excludes inactive employees, with an explicit filter to include their preserved assignments.
- Show assigned version, due date, completion timestamp, latest score, passing-attempt score where applicable, and drill-down to slide progress and attempt history. Show no-quiz scores as not applicable.
- Query assignments, progress, and attempts as authoritative data. Do not persist a separate `Employee.completedAssignments` collection.
- Add deadline editing and cancellation for active, incomplete assignments. Record actor, time, and old/new values in assignment history; require a reason for cancellation. Extending an overdue deadline recalculates current status without erasing the earlier deadline.
- Require a newly selected deadline to be today or later in the organization time zone. Once shared notification delivery is added in Slice 6, every actual deadline change queues an immediate deadline-change email in the same transaction. Saving an unchanged date creates no change event or email.
- Replacement uses separate Cancel and Assign actions. After cancellation, the administrator explicitly selects the replacement version and deadline. Preserve the original evidence and initialize the replacement without copied progress or attempts.
- Preserve completed assignments' deadlines and evidence. Exclude canceled assignments from pending obligations and prevent further learner submissions while retaining history.

**Acceptance and verification:**

- Use employees with not-started, in-progress, completed, overdue, completed-late, and canceled assignments to verify filters and details against stored evidence.
- Extend a pending deadline and verify both administrator and employee views agree on the new derived status; preserve the previous due date in history.
- Reject changing a deadline to yesterday. In Slice 6, shorten a deadline to today after 09:00 and verify an immediate deadline-change email is queued even though no scheduled reminder time remains.
- Cancel an assignment and verify progress and attempts remain reviewable while further completion actions are rejected.
- Race cancellation with completion and verify each transition is checked transactionally: if completion wins, cancellation fails without changing its evidence; if cancellation wins, further completion is rejected.
- Cancel unfinished version 1 and assign version 2; verify the original evidence remains intact and only the new assignment is active and incomplete.
- Verify inactive employees are excluded from default follow-up results but their assignments and evidence remain available through the activity filter.
- Reject employee access to organization-wide reports and assignment administration endpoints.

**Demo:** An administrator finds an overdue employee, reviews failed attempts, updates a deadline, and verifies the employee sees the change.

### Slice 6 — Send assignment and deadline-change emails reliably

**Outcome:** Employees receive assignment and deadline-change emails with the training, current deadline, and assignment link after transient delivery failures, provided the assignment remains active, incomplete, and actionable when dispatched.

**Build:**

- Integrate the email provider selected during setup; supply a development/test delivery sink and configurable sender and application URL.
- In the assignment-creation transaction, persist an assignment-created notification intent in a transactional outbox. In an actual deadline-change transaction, persist a deadline-change intent tied to the resulting due-date revision. Send email only after commit through a background worker.
- Add notification records and delivery-attempt records with recipient, assignment, notification kind, status, timestamps, provider reference, and sanitized error information.
- Retry transient failures with bounded backoff; surface exhausted failures and allow an administrator to retry eligible failed deliveries through the same queue.
- Before initial dispatch and every retry, suppress an unsent assignment or deadline-change email for completed or canceled work or an inactive recipient, and record the reason. Suppress an obsolete deadline-change intent when a newer revision has replaced it. Suppression concerns unsent work; email already accepted by the provider cannot be recalled.
- Before sending, resolve the current verified recipient address and current deadline and use the title from the assigned immutable version. If no current verified address is available, surface a blocked delivery without falling back to an obsolete or unverified address. Preserve the actual recipient, content, and due-date revision used for each delivery attempt. Reconcile ambiguous provider outcomes before deciding whether a new dispatch is needed.
- Use stable event/delivery identifiers, atomic job claiming, and provider idempotency where available. Handle ambiguous provider timeouts explicitly; do not assume application retries guarantee exactly-once email delivery.
- Link emails through authentication to the correct assignment. Do not expose quiz answers or other employees' information.

**Acceptance and verification:**

- Create a bulk assignment and verify one notification intent per newly created assignment, with correct titles, deadlines, and links.
- Simulate provider failure and worker restart: assignments remain usable, notification work survives, and retries retain their delivery history.
- Reprocess an event and verify it does not create a second logical notification. A repeated assignment request does not generate a new assignment email.
- Delay delivery, then complete/cancel the assignment or deactivate its recipient; verify the unsent email is suppressed and its reason recorded.
- Change a deadline after 09:00 and verify an immediate deadline-change intent survives a delivery outage. Repeat the same request or save the same date and verify no duplicate logical notification is created.
- Change the recipient's verified address and deadline before dispatch; verify the email uses current values and the original assigned version's title, and logs exactly what was sent. Verify an unverified replacement address blocks delivery and stale deadline-change revisions are suppressed.
- Verify an emailed link still enforces authentication and assignment ownership.

**Demo:** Assign training and change another assignment's deadline during a simulated email outage, restore delivery, and inspect the successful retries with current deadlines in the administrator delivery log.

### Slice 7 — Send and reschedule deadline reminders

**Outcome:** Employees receive the two scheduled reminders while completed or canceled work stops generating reminders.

**Build:**

- Schedule reminders at 09:00 organization time three calendar days before the deadline and on the due date. Store concrete UTC execution times.
- Reuse notification delivery, retry handling, and logs from Slice 6. Distinguish reminder kinds and associate jobs with a due-date revision to identify obsolete work.
- On deadline changes, invalidate unsent reminders for the old revision and create only eligible future reminders for the new deadline. Preserve already-sent history.
- Recheck completion, cancellation, recipient activity, due-date revision, reminder expiration, and reminder supersession immediately before every dispatch and retry, including administrator-triggered retries. Prevent queued obsolete jobs from sending after changes have taken effect.
- For new assignments, skip elapsed reminder times and retain future ones; the initial assignment email is queued immediately. After scheduler downtime, send at most the latest eligible missed reminder: the three-day reminder remains eligible until the due-day reminder is due, which supersedes it; both expire at the assignment's deadline. Record skipped, superseded, and expired outcomes.
- Reactivation enables only future scheduled reminder times and creates no catch-up reminder. Reminder work missed while the employee was inactive remains suppressed. Once overdue, follow-up uses manual nudges; expired scheduled reminders do not resume.
- Show upcoming reminders and delivery history on the administrator assignment detail. Scheduling uses fixed MVP rules.

**Acceptance and verification:**

- With a controlled clock, verify both send times, calendar-day calculations across daylight-saving transitions, and assignments created inside the three-day window or on the due date.
- Move a deadline earlier and later and verify stale scheduled jobs cannot send for the prior date.
- Complete or cancel an assignment before dispatch and verify pending reminders are suppressed. Repeat the check when retrying an earlier failed send.
- Restart the scheduler, run concurrent workers, and replay jobs to verify durable scheduling and deduplication. Document that a message already accepted by the provider cannot be recalled.
- Recover from outages between reminder times, later on the due date, and after the deadline. Verify only the latest eligible reminder sends and expired ones do not. Reactivate before and after reminder times and verify only future times resume.
- Fail a three-day reminder and retry after the due-day reminder becomes eligible; verify the earlier reminder is suppressed. Fail a due-day reminder and retry at or after the deadline; verify it expires. Apply both checks to automatic and administrator-triggered retries.

**Demo:** Advance time through both reminder points, change a deadline, and complete another assignment to demonstrate rescheduling and suppression.

### Slice 8 — Send audited manual nudges

**Outcome:** Administrators can follow up directly from the dashboard and see who sent each reminder and what happened.

**Build:**

- Add a “Send reminder” action for eligible assignments in the administrator dashboard and detail view. Show the next scheduled reminder beside it.
- Reuse the notification queue and delivery log, recording the requesting administrator and request time as well as each delivery attempt.
- Enforce eligibility and the confirmed rolling 24-hour limit on the server, including concurrent requests from different administrators. Show the next eligible manual-send time when limited.
- Recheck assignment eligibility before dispatch. Leave scheduled reminder timing unchanged when a manual nudge is sent.
- Show queued, sent, failed, suppressed, and rate-limited outcomes clearly; a queued request must not be presented as delivered.

**Acceptance and verification:**

- Send a nudge for incomplete training and verify the email, actor, timestamp, and delivery outcome.
- Reject or suppress nudges for completed/canceled work or inactive recipients. Reject employee attempts to invoke the action.
- Submit repeated and concurrent nudges and verify only one eligible notification is queued during the limit window.
- Verify a failed delivery follows the established retry process without consuming another manual-send allowance or generating duplicate logical nudges.
- Send a manual nudge shortly before a scheduled reminder and verify both remain eligible, with the upcoming scheduled time shown to the administrator. The manual limit does not suppress scheduled reminders.

**Demo:** An administrator filters overdue work, sends one reminder, sees a repeat request rate-limited, and inspects the delivery history.

## Implementation boundaries

- Keep content definitions, assignments, and execution evidence separate throughout every slice. Add only the tables and migrations needed by the current workflow, including progress, assignment history, and notification records absent from the conceptual schema.
- Keep authorization, input validation, grading, completion, and assignment transitions on the server. Treat UI checks as assistance for users.
- Treat request replay and a deliberate new operation differently. Persist replay outcomes with the transaction they describe; replays cannot create new assignments, attempts, or logical notifications after lifecycle state changes.
- Use database constraints and transactions for publication, ordering, duplicate assignments, attempts, completion, and notification creation. Scope constraints to the actual chosen policies.
- Preserve historical content and evidence through version changes, employee edits, deadline changes, and cancellation. Avoid destructive deletion of referenced records.
- Add focused domain tests for grading and deadline rules, integration tests for permissions and transaction behavior, and a small set of end-to-end workflow tests as the relevant slices arrive. Use a controllable clock and email sink for deterministic notification checks.

## MVP release criteria

- [ ] All twelve user stories have their acceptance criteria demonstrated, including all four question types and both acknowledgment-only and quiz-based completion.
- [ ] A complete administrator-to-employee scenario covers authoring, publishing, bulk assignment, email receipt, saved slide progress, failed and passing attempts, reporting, scheduled reminders, and a manual nudge.
- [ ] Published revisions, repeated requests, concurrent submissions, deadline edits, late completion, cancellation, and worker/provider failures preserve valid assignments and historical evidence.
- [ ] Server authorization protects administrator actions, employee assignment ownership, reports, and answer keys.
- [ ] The selected deployment has tested migrations, administrator provisioning, configured time zone and email delivery, durable background jobs, and a verified database backup/restore path.
- [ ] Notification failures are visible and recoverable; retries respect current eligibility, revision, supersession, and expiration rules.
- [ ] The operational owner is named and the employee-data retention/access policy is approved before any real employee data is introduced. Demonstration data is used until that gate is satisfied; automatic deletion is not implemented without an approved policy and scope.
- [ ] Runtime configuration and the steps to run, check, deploy, and operate the application are documented.

## Deferred scope

Defer formatting tools and media, elaborate templates, complex permission hierarchies, extensive analytics, and sophisticated reporting exports. Also leave recurring certifications, learning-engagement features, waivers, administrative score overrides, configurable policy builders, and automatic reassignment to later work unless the product decisions above change.

Post-MVP work on CSV export, group assignment, renewal, search, supporting links, and waivers is planned separately in [Extended user stories](plans/extended-stories.md) (`US-13`–`US-18`). Those plans keep these exclusions: no uploaded media, no approver hierarchy, no automatic reassignment, and a single renewal interval rather than a policy builder. They do not change the MVP release criteria.
