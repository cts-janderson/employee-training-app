# User stories

This document collects the application's user stories in one place. `US-01`–`US-12` are the confirmed MVP baseline, reproduced from [mvp.md](mvp.md#6-refined-user-stories-and-acceptance-criteria) without changing their acceptance criteria. `US-13`–`US-19` are locally implemented proposed additions; they are not part of the confirmed MVP or its [release criteria](implementation-plan.md#mvp-release-criteria).

## Confirmed MVP stories

### Epic A: Content Management

#### US-01 — Create training

As an administrator, I want to create training so that I can organize material employees need to learn.

**Acceptance criteria:**

- Admin can create and edit a draft training with a title and description.
- A training can contain multiple slides.
- Draft content is not automatically assigned or published.
- Incomplete drafts can be saved with actionable validation problems; structurally invalid data is rejected on save, and incomplete content blocks publication.

#### US-02 — Manage slides

As an administrator, I want to create, edit, remove, and reorder slides so that training is presented sequentially.

**Acceptance criteria:**

- Each slide has a header, body, and optional footer.
- Slide content is plain text with paragraphs and line breaks preserved; formatting tools and media are outside the MVP.
- Slide ordering is deterministic.
- Slide edits and reorder operations are saved consistently.

#### US-03 — Create quizzes

As an administrator, I want to add quiz questions and configure a passing threshold so that understanding can be assessed.

**Acceptance criteria:**

- Supports single-choice, multiple-selection, true/false, and ordering formats.
- Each question includes valid grading rules for its type.
- Passing threshold is a validated whole percentage from 1 through 100.
- A quiz cannot be published with invalid or ungradable questions.
- Incomplete questions can be saved and resumed without satisfying publication requirements.
- Administrators can author a library quiz separately and attach a copy of a complete, titled quiz to a training draft. Later changes to either copy do not change the other.
- Replacing an attached draft quiz requires confirmation. Training publication still validates its saved quiz copy.

#### US-04 — Publish versions

As an administrator, I want to publish a training version so that assigned employees receive stable content.

**Acceptance criteria:**

- Published versions are immutable.
- Editing published content creates a new draft/version.
- Existing assignments retain their original version. Replacing unfinished training requires explicit cancellation and a new assignment; the prior assignment and its evidence remain intact.

### Epic B: Assignment Management

#### US-05 — Assign training

As an administrator, I want to assign a published training version to one or more employees with due dates.

**Acceptance criteria:**

- Each employee receives an individual assignment record.
- Assignment stores employee, training version, assignment time, and due date.
- A newly selected due date must be today or later in the organization time zone.
- At most one active, incomplete assignment exists per employee and logical training, across versions. Replacing unfinished training requires explicit cancellation before the new assignment is created.
- Deactivation preserves an employee's outstanding assignments and history while rejecting access and new assignments; it does not complete or cancel training.
- Reactivation restores access to active assignments with unchanged progress and deadlines. It creates no new assignment, deadline extension, or assignment email.
- Replacement uses separate Cancel and Assign actions: require a cancellation reason, then select the replacement version and deadline. Its progress starts from zero; the old evidence remains intact.
- An administrator can archive an assignment with a reason. Archiving unfinished work cancels it first; restoration changes visibility only and never restarts that obligation. Progress, attempts, and notification records are preserved.

#### US-06 — Assignment notifications

As an employee, I want to receive an email when training is assigned so that I know what to complete.

**Acceptance criteria:**

- Assignment creation schedules a notification.
- Email identifies the training and deadline.
- Notification failures do not roll back a valid assignment.
- Before dispatch and each retry, suppress unsent assignment emails for completed/canceled work or inactive employees and record the reason.

#### US-07 — Deadline reminders

As an employee, I want reminders before my training is due so that I can complete it on time.

**Acceptance criteria:**

- Reminder scheduled at 09:00 in the organization time zone three calendar days before the due date.
- Another reminder scheduled at 09:00 in the organization time zone on the due date.
- Reminder rules are fixed for the MVP; administrators adjust assignment deadlines rather than configuring reminder policies.
- Completed or canceled assignments do not receive further scheduled reminders.
- Inactive employees do not receive reminders.
- Changing the due date updates future reminders.
- An actual deadline change also queues an immediate deadline-change email using the shared delivery queue and eligibility checks. Saving an unchanged date sends no change notification.
- Delivery attempts and outcomes are recorded.
- For newly created assignments, skip elapsed reminder times, retain future ones, and queue the assignment email immediately.
- On scheduler recovery, send at most the latest eligible missed scheduled reminder. The due-day reminder supersedes the three-day reminder, and both expire at the assignment's deadline.
- Reactivation resumes only future scheduled reminder times, with no catch-up for times missed while inactive. After the deadline, follow-up uses manual nudges.

### Epic C: Employee Experience

#### US-08 — View assigned training

As an employee, I want to see my assigned training, due dates, and status so that I can prioritize my work.

**Acceptance criteria:**

- Employees can only see their own assignments unless their role permits otherwise.
- Assignments show progress and deadline status.
- Employees can open the correct published training version.

#### US-09 — Complete training

As an employee, I want to acknowledge the training slides and pass the associated quiz when one is included so that I can satisfy the training requirement.

**Acceptance criteria:**

- Slides appear in defined order.
- Employees may navigate freely; navigation alone does not acknowledge a slide. Preserve acknowledgments and resume position.
- Every slide requires an explicit acknowledgment, which records the employee's action without establishing attention or comprehension.
- Training without a quiz completes when every slide is acknowledged. Training with a quiz also requires a passing attempt.
- Quiz submission is validated and graded server-side.
- All slides must be acknowledged before quiz submission.
- Each attempt is recorded separately.
- Passing an assessment completes the assignment once every slide has been acknowledged.

#### US-10 — Retry assessment

As an employee, I want to retry a failed quiz until I pass so that I can satisfy the assessment requirement.

**Acceptance criteria:**

- Unlimited retries are allowed until completion; new attempts after completion are rejected.
- Earlier attempts are preserved.
- Completion is recorded only when configured criteria are satisfied.
- Employees can review submitted answers, overall score, pass/fail, and attempt history. Correct answers, per-question correctness, and grading configuration are withheld, including after completion.

### Epic D: Administration and Reporting

#### US-11 — Monitor progress

As an administrator, I want to view pending, completed, and overdue training assignments so that I can identify follow-up needs.

**Acceptance criteria:**

- Dashboard supports filtering by employee, training, and status.
- Completion timestamps and relevant scores are available.
- Reporting uses assignment and attempt records as authoritative data.
- The default actionable-work view excludes inactive employees. Their assignments and evidence remain available through an explicit activity filter; cancellation remains a separate action.
- Archived assignments are excluded from routine lists and available through an explicit administrator archive filter. Completed archived work can be restored to the learner's completed list.

#### US-12 — Send manual nudge

As an administrator, I want to remind an employee about incomplete training so that I can encourage timely completion.

**Acceptance criteria:**

- Admin can trigger a reminder for an eligible assignment.
- The system records who triggered the nudge and when.
- At most one manual nudge request is accepted per assignment in a rolling 24 hours, enforced across administrators.
- Manual nudges leave scheduled reminders unchanged; show the upcoming scheduled reminder beside the action. Scheduled and manual messages may arrive close together.

## Proposed future stories

These stories extend the existing training definition, assignment, and execution evidence model. Product decisions for `US-13`–`US-18` were recorded on 2026-10-27, with local implementations described in [docs/plans/extended-stories.md](plans/extended-stories.md). The later quiz-outcomes addition is described in its [chart design](design/admin-quiz-outcomes.md). The [implementation plan](implementation-plan.md#deferred-scope) still governs the MVP release boundary; these additions do not authorize live employee use.

### Epic E: Reporting and Assignment at Scale

#### US-13 — Export training status

**Local status:** Implemented in Slice 14.

As an administrator, I want to export the assignment records shown by my dashboard filters so that I can share a dated progress snapshot with authorized stakeholders.

**Acceptance criteria:**

- An administrator can export the current filtered assignment set as a CSV file; employees cannot access the export.
- Each row identifies the employee, training and published version, assignment state, due date, progress, deadline status, completion timestamp, and relevant quiz score when available.
- The export derives status and scores from the same authoritative assignment and attempt records as the dashboard, including the same treatment of inactive employees and archived assignments.
- The export records when it was generated and the organization time zone used for displayed dates.
- Employee-entered values are encoded safely for spreadsheet opening. Quiz answer keys and submitted answers are excluded.
- The generated file is the export's record; no separate export log is kept.

#### US-14 — Assign training by group

**Local status:** Implemented in Slice 16.

As an administrator, I want to maintain employee groups and assign a published training version to a group so that I can assign common requirements without selecting each employee individually.

**Acceptance criteria:**

- Administrators can create, rename, and maintain group membership using active employee records.
- Before submission, the assignment review shows the resolved recipients, published version, due date, and known conflicts.
- A group assignment is limited to 100 resolved recipients, the same limit as selecting employees individually. A larger group is rejected at review.
- Submission creates a separate assignment record for each eligible employee and reports an outcome for every resolved recipient.
- Existing one-outstanding-assignment rules, due-date validation, idempotency, and assignment notifications apply to every individual assignment.
- Membership is resolved when the administrator submits; later membership changes do not silently create, cancel, or alter assignments.

#### US-15 — Renew required training

**Local status:** Implemented in Slice 17.

As an administrator, I want to schedule a new training obligation after a completed requirement reaches its renewal date so that employees can maintain recurring qualifications.

**Acceptance criteria:**

- An administrator can set a training's renewal interval in whole months. A completion's renewal date is its completion date in the organization time zone plus that interval, clamped to the end of the month.
- Renewals are created only when an administrator starts one: they choose the published training version and due date, and review the eligible employees and their renewal dates before any new assignment is created. Nothing is renewed automatically.
- Each renewal creates a new individual assignment; earlier assignments, published versions, attempts, and completion evidence remain intact.
- A renewal does not create a second active, incomplete assignment for the same employee and logical training. Conflicts are reported for administrator action.
- New assignments use the existing due-date, notification, reminder, and completion rules.
- Administrators and employees can distinguish the current cycle from prior completed cycles.

#### US-16 — Search and browse growing lists

**Local status:** Implemented in Slice 15.

As an administrator, I want to search and page through trainings, quizzes, employees, and assignments so that I can find records as the organization grows.

**Acceptance criteria:**

- Administrator lists support relevant title or employee-name searches and preserve applicable status filters.
- Results show a total count and have a stable, understandable sort order across pages.
- Opening a result and returning to the list preserves the current search, filters, and page when practical.
- Search results respect the same authorization and archive visibility rules as the unfiltered lists.

### Epic F: Supporting Learning Material

#### US-17 — Include supporting learning material

**Local status:** Implemented in Slice 20.

As an administrator, I want to add supporting links to a training draft so that employees can consult policies and examples while learning.

**Acceptance criteria:**

- Supporting material is a named `https` link with an optional description. Documents are linked from the organization's existing document store; uploading files is outside this story.
- Authors can add, edit, reorder, and remove supporting material in a draft and review it before publication.
- Employees can open the material associated with their assigned published version, including after completion.
- Publishing fixes the material referenced by that version; later draft changes do not alter existing assignments.
- Links have descriptive names and remain usable with keyboard and assistive technology.
- Access to material follows the same assignment ownership and administrator permissions as the training itself.

### Epic G: Administrative Exceptions

#### US-18 — Record a requirement waiver

**Local status:** Implemented in Slice 19.

As an administrator, I want to record a justified waiver for an employee's assignment so that reports distinguish an approved exception from completed training or unfinished work.

**Acceptance criteria:**

- A waiver requires an authorized actor, a reason, and a recorded decision time.
- Waiving an assignment preserves its version, due date, slide acknowledgments, quiz attempts, and administrative history; it does not set a completion timestamp or passing score.
- The employee and administrator views show a waiver as a distinct outcome, and routine pending-work lists and exports treat it consistently.
- Future unsent reminders for a waived obligation are suppressed with a recorded reason.
- Any administrator may waive an outstanding assignment except their own.
- A waiver is final. If the requirement must apply again, an administrator creates a new assignment; the waived assignment is not reopened.

### Epic H: Assessment Reporting

#### US-19 — Review quiz outcomes by training

**Local status:** Implemented. See the [chart design](design/admin-quiz-outcomes.md).

As an administrator, I want to compare quiz outcomes across trainings and see the employees behind each count so that I can find people who need a retry.

**Acceptance criteria:**

- The Assignments page offers separate pending-workload and quiz-outcomes chart modes. Each mode's values disclosure describes the data plotted by that mode.
- Quiz outcomes use pending and completed, nonarchived assignments with a quiz. Activity, employee, training, and search filters apply to both the chart and its detailed results.
- Each assignment cycle is counted once as passed, latest attempt failed and retry needed, or not attempted. A failed attempt remains retryable; a later pass moves that cycle into passed.
- The values disclosure identifies employees, published versions, cycles, latest scores and attempt times, and links to assignments. It includes every matching training even when the chart plots only eight.
- Slide-only, canceled, waived, and archived assignments do not appear as quiz passes or failures. The interface reports empty results clearly; the chart design documents the cohort.

## Planning notes

- `US-13` is the smallest next slice because it uses existing assignment evidence. `US-14` and `US-15` add the most value once assignments are managed at scale.
- Group size, renewal timing, and waiver authority were decided on 2026-10-27; see [ADR-0005](adr/0005-renewal-interval-on-training.md) and [ADR-0006](adr/0006-final-waivers-by-any-other-administrator.md). Supporting material is links only, and exports keep no separate log.
- Production sign-in, external email delivery, deployment proof, PostgreSQL concurrency checks, backup and restore, accessibility evaluation, and the real-data gate remain in the [release criteria](implementation-plan.md#mvp-release-criteria) and [HCI validation](hci-validation.md); these proposed stories do not replace those gates.
