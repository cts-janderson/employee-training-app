# Employee Training

The shared language for assigning learning requirements within one organization and recording how employees fulfill them.

## Language

**Employee**:
A person within the organization whose assignments and training history remain attributable to them through name, email, or activity changes.
_Avoid_: Email address, login account

**Training**:
A reusable learning requirement consisting of learning documents or legacy slides and an optional quiz, independent of any employee's obligation to complete it.
_Avoid_: Course, module

**Published training version**:
An immutable edition of a training's content and assessment rules, shared by the employees assigned that edition.
_Avoid_: Live training, latest content

**Draft training version**:
An unpublished, editable edition of training that may contain unfinished learning content and questions.
_Avoid_: Published version, assigned content

**Training display title**:
The name administrators see for a training: the title of its newest published version, or of its draft before first publication.
_Avoid_: Version title

**Library quiz**:
A reusable assessment authored independently of a training and available to copy into training drafts.
_Avoid_: Employee assignment, live training quiz

**Attached quiz**:
The independent quiz copy belonging to one training version, including its questions, answer rules, and passing threshold.
_Avoid_: Live library link

**Assignment**:
One employee's obligation to complete a specific published training version by a deadline.
_Avoid_: Enrollment, training copy

**Slide acknowledgment**:
An employee's explicit confirmation of a slide within an assignment; it records the action without establishing attention or comprehension.
_Avoid_: Verified view, proof of reading

**Quiz attempt**:
An employee's submitted answers to an assignment's quiz together with the assessed outcome.
_Avoid_: Completion, quiz session

**Completion**:
Fulfillment of an assignment through acknowledgment of every required document (or every slide in a legacy version) and, when the training includes a quiz, a passing attempt.
_Avoid_: Mastery, attendance

**Pending assignment**:
An active, incomplete assignment, including one whose deadline has passed or whose employee is inactive.
_Avoid_: Not started, overdue

**Actionable assignment**:
A pending assignment belonging to an active employee.
_Avoid_: All pending work

**Review access**:
An employee's access to their own assignment's slides and quiz attempt history without the ability to acknowledge slides or submit quiz attempts. A waived assignment offers its employee review access only.
_Avoid_: Read-only mode, view access

**Cancellation**:
An explicit administrative ending of an unfinished assignment that preserves its prior activity without recording completion.
_Avoid_: Deletion, waiver, completion

**Archiving**:
An administrative removal of an assignment from routine lists that preserves its content and execution history; unfinished work is canceled as part of archiving.
_Avoid_: Deletion, completion

**Restoration**:
Return of an archived assignment to routine visibility without reopening a canceled obligation.
_Avoid_: Reactivation, reassignment

**Deactivation**:
An administrative change that makes an employee inactive while preserving their assignments and history.
_Avoid_: Cancellation, employee deletion

**Reactivation**:
Restoration of an employee's active status and access to outstanding assignments with their existing progress and deadlines.
_Avoid_: Reassignment, deadline reset

**Due date**:
The final calendar day on which an assignment can be completed on time, interpreted in the organization's configured time zone.
_Avoid_: Reminder date, employee-local date

**Scheduled reminder**:
An assignment follow-up triggered by a fixed time relative to its due date.
_Avoid_: Manual nudge, assignment email

**Deadline-change notification**:
A message informing an employee that an administrator has changed an assignment's due date.
_Avoid_: Scheduled reminder, manual nudge

**Manual nudge**:
An administrator-requested reminder about an employee's incomplete assignment, independent of its scheduled reminders.
_Avoid_: Scheduled reminder, assignment email

**Employee group**:
A named set of employees that administrators maintain to select assignment recipients. Membership alone creates, changes, or cancels no assignment.
_Avoid_: Team, department, role

**Renewal**:
A new assignment of a training, created after an employee's completion of it reaches its renewal date, that begins the employee's next cycle without altering earlier assignments.
_Avoid_: Reassignment, retake, extension

**Renewal date**:
The date on which a completed assignment's training becomes due for renewal.
_Avoid_: Due date, expiry

**Waiver**:
An administrator's justified decision that an employee need not fulfill an assignment. It ends the obligation without recording completion and preserves its prior activity.
_Avoid_: Exemption, completion, cancellation

**Supporting material**:
A named reference, such as a policy or example, that a training version offers for consultation alongside its slides; consulting it is not required for completion.
_Avoid_: Attachment, resource, slide

**Administrator**:
An employee authorized to manage training, employees, assignments, and results across the organization, who may also receive training assignments.
_Avoid_: Separate administrator identity

**Population**:
One of two separate sets of employees, with the trainings, library quizzes, groups and evidence that belong to them: the real organization, or synthetic demonstration data. An administrator works within exactly one population.
_Avoid_: Environment, tenant, workspace, sandbox


**Document**:
A titled, continuous unit of learning content within a training; its acknowledgment is required for assignment completion.
_Avoid_: Slide, attachment, page

**Section**:
An organizational grouping of documents and other sections within a training. Its completion is derived from its descendant documents.
_Avoid_: Acknowledgment unit, document

**Document acknowledgment**:
An employee's explicit confirmation of a document within an assignment, without establishing attention or comprehension.
_Avoid_: Verified view, proof of reading
