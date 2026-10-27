# HCI implementation plan: vertical slices

## Purpose and relationship to the MVP

This plan turns the findings in the [HCI review](hci-review.md) into six demonstrable changes to the existing application. Each slice delivers a complete user task across the template, view, server validation, feedback, and verification. The [MVP implementation plan](implementation-plan.md) remains the source for assignment, grading, notification, authorization, and release behavior. These HCI slices improve how people complete those tasks while preserving the confirmed policies and story identifiers in [the MVP](mvp.md).

Use synthetic employees and the local notification sink for demos. The real-data and deployment gates in the MVP plan still apply. The proposed accessibility evaluation target is WCAG 2.2 AA, pending the organization's standard; see the [W3C criteria](https://www.w3.org/TR/WCAG22/) and [form feedback guidance](https://www.w3.org/WAI/tutorials/forms/notifications/).

## Sequence

The first three slices address the two P0 findings: losing quiz answers after validation and unsafe authoring/publishing actions. The remaining slices improve the end-to-end learner and administrator journeys. Shared navigation, focus, responsive styling, and error patterns are introduced on the pages touched by each slice, then extended as later slices use them. There is no separate styling-only slice.

| Slice | Demonstrable user outcome | Depends on | Stories |
| --- | --- | --- | --- |
| HCI-01 | A learner corrects an invalid quiz response without re-entering valid answers. | Existing quiz workflow | US-09, US-10 |
| HCI-02 | An administrator edits a draft or quiz, sees that it saved, and can recover from mistakes or a stale edit. | Existing authoring workflow | US-01, US-02, US-03 |
| HCI-03 | An administrator reviews a complete saved version before its final publication. | HCI-02 | US-01–US-04 |
| HCI-04 | A learner finds the next training action, resumes, acknowledges slides in sequence, and sees completion clearly. | HCI-01 for quiz handoff | US-08–US-10 |
| HCI-05 | An administrator reviews a bulk assignment and understands each recipient's outcome. | Existing assignment workflow | US-05 |
| HCI-06 | An administrator finds work needing follow-up and distinguishes a queued reminder from a sent one. | HCI-05 for the complete administrator journey | US-11, US-12 |

## Rules for every slice

- Preserve server-side authorization, assignment ownership, immutable published versions, transaction boundaries, and idempotency. UI state must come from authoritative server state after each action.
- Include the visible success, error, and next-action states for the task. Errors identify the affected control, keep valid entered values, and provide a path back to correction. Use one consistent heading and status pattern as it is introduced.
- Check the touched pages with keyboard only, visible focus, screen reader, 320 CSS-pixel width, and 400% zoom. Check labels, headings, focus order, reflow, and error announcements. Record any exception and its follow-up.
- Add focused Django tests for new navigation and error states when a regression could lose work or misrepresent completion. Keep the existing domain tests as the policy check. Capture representative screenshots and run the slice demo with synthetic data.
- Review wording against [the glossary](GLOSSARY.md). Show the organization time zone wherever a due date or reminder time affects a decision.

## HCI-01 — Recover from quiz input errors

**Outcome:** A learner can correct one invalid response and submit the same attempt without rebuilding the rest of the form.

**Build:**

- Render question prompts as part of each question group name. Give each answer control a clear accessible name; keep the four native question formats and free retries.
- Validate completeness, option membership, and distinct ordering selections before grading. Return question-specific errors and a linked error summary on invalid input, with entered selections retained and the first error reachable by keyboard. Keep server validation authoritative even if the interface prevents duplicate ordering choices.
- Preserve the submission token through correction so a valid resubmission remains idempotent. A rejected response creates no `QuizAttempt` or completion event. Handle a completed or stale assignment with a clear route to its current assignment or attempt result.
- Show the result as **Passed** or **Did not pass** with the next action. Preserve submitted answers and attempt history while withholding answer keys and per-question correctness.

**Acceptance and verification:**

- Submit a quiz with valid earlier answers and a repeated ordering option. The same form returns with those answers selected, a question-linked error, and no recorded attempt; correcting it records one attempt.
- Repeat the accepted submission token and confirm that no second attempt or completion event appears. Retry a failed quiz with a new token and confirm that the earlier attempt remains.
- Test missing, foreign, duplicate, and stale responses. Confirm that an employee cannot submit another employee's quiz and that answers or grading configuration do not leak into the page.
- Demo: a synthetic learner makes an ordering mistake, fixes it, submits, and sees the result.

## HCI-02 — Save and recover authoring work

**Outcome:** An administrator can edit slides and quiz questions, verify a save, correct validation problems, and recover from an accidental remove or stale revision.

**Build:**

- Add a post-save confirmation using the saved revision and server response time. Keep draft and quiz editors' form values after recoverable validation errors; link the error summary to the affected title, slide, question, option, or threshold field.
- Give long slide and question lists a compact outline with named sections and a clear location for Add, Move, Save, and Remove actions. Make the save state visible before navigating between the draft and quiz editors. Warn about unsaved changes when leaving either editor.
- Make remove actions reviewable and keyboard operable, with an explicit confirmation or undo before the item is permanently lost. Preserve other unsaved field edits while a remove is reviewed. Do not let a remove action publish or overwrite a newer revision.
- For a stale revision, show what was saved on the server and preserve the administrator's submitted text for comparison or copying. Require an explicit decision before applying edits to the newer draft; retain revision checks on the server.

**Acceptance and verification:**

- Edit several slides and questions, move one, save, reload, and verify the exact order and content; the page shows that the save completed.
- Trigger a publication validation problem and navigate directly from the summary to the affected field. Fix it without re-entering unrelated content.
- Start a removal and back out; all saved and unsaved content remains. Confirm a removal and verify only the chosen item changes.
- Submit from two stale editor tabs; the newer save remains authoritative, and the older tab can recover its text without silently overwriting it.
- Demo: an administrator drafts a training and quiz, catches a mistaken removal, and resolves a stale edit.

## HCI-03 — Review and publish the saved version

**Outcome:** An administrator can inspect exactly what employees will receive and deliberately publish an immutable version.

**Build:**

- Provide a publication review of the **saved** draft: title, description, version number, ordered slide count/content, quiz presence, question count, and passing threshold. Indicate that publication makes this version immutable and does not assign it automatically.
- Validate the complete saved draft before showing a final publish action. Group blockers by slide or question and link back to their editor controls. Explain when edits on an editor page must be saved before review.
- On final publish, recheck authorization, draft status, and revision within the existing transaction. A changed or already published draft returns to a recoverable review state; a repeated request does not create another version. Keep the published preview as the confirmation page with the next available **Assign training** action.

**Acceptance and verification:**

- An incomplete draft cannot reach final publication; every blocker has a clear correction path.
- Save a complete draft, review its exact content, publish once, and confirm that the published version matches the reviewed revision. A concurrent edit between review and final submit is rejected without publishing unseen content.
- Verify earlier published versions and assignments keep their original content and that employee access to authoring/publishing is denied.
- Demo: an administrator reviews and publishes version 2 while version 1 remains assigned to an employee.

## HCI-04 — Complete assigned training with a clear next action

**Outcome:** A learner can tell what is due, resume the correct place, finish the remaining slides, and understand whether a quiz or completion follows.

**Build:**

- Present **My training** as unfinished work followed by completed work. Order actionable work by overdue state and due date, with one **Start**, **Resume**, **Take quiz**, or **Review** action per assignment. Show progress, due date, deadline state, and version without making the card a dense paragraph.
- Put the next required action first on assignment detail while retaining free access to all slides and attempt history. Show the first unacknowledged slide when there is no saved position; preserve saved resume behavior.
- Show “Slide N of M,” acknowledgment state, and remaining acknowledgments. After acknowledgment, navigate to the next unacknowledged slide or to the quiz/completion state. Repeated acknowledgments remain harmless; visiting a slide never acknowledges it.
- Give slide-only completion and quiz-based completion clear confirmation text. Preserve access to completed content and attempt history, including overdue and late states.

**Acceptance and verification:**

- Given not-started, in-progress, overdue, and completed assignments, a learner can identify the next task and due date from **My training**. Completed work does not bury unfinished work.
- Open a slide, leave, return, and resume. Acknowledge slides out of order, then follow the next-action path until the quiz opens or slide-only completion appears. The server records each acknowledgment once.
- Complete after the deadline and confirm that the learner sees completed-late status without losing content or evidence. Verify pinned versions and ownership.
- Demo: a synthetic employee resumes midway, finishes a slide-only assignment, then finishes a quiz assignment.

## HCI-05 — Review and submit bulk assignments

**Outcome:** An administrator can verify recipients, version, and deadline before assigning, then understand every recipient's result.

**Build:**

- Show the training title in the assignment form and make published version, due date, organization time zone, and recipient selection easy to scan. Preserve selected inputs after validation errors.
- Add a review step or equivalent review state showing selected version, due date, recipient count, and conflicts already known from current assignments. Label known conflicts as provisional; the submit transaction remains authoritative if state changes after review.
- Keep the existing operation identifier and per-recipient idempotent creation semantics. After submit, show counts and individual **Created**, **Already assigned/conflict**, or other recorded outcomes with links to created assignments. State clearly that queued assignment email is separate from delivery.
- Preserve entered name and email in the synthetic employee add/edit forms after a server error, with a field-specific correction route.

**Acceptance and verification:**

- Assign a published version to two employees; review shows both, the exact version, and the due date in the organization time zone. The result lists two distinct assignments.
- Include an inactive employee and an existing outstanding assignment. The review warns about known issues; the final result accurately reports each independent outcome, including a conflict discovered after review.
- Replay an operation and verify no duplicate assignments or notifications. An invalid date or missing recipient returns to the form with selections intact. Employee role cannot access the flow.
- Demo: an administrator reviews a three-recipient assignment, submits it, and resolves one conflict.

## HCI-06 — Find follow-up work and interpret reminder outcomes

**Outcome:** An administrator can find an overdue employee, inspect current progress, act, and tell whether a reminder was queued or sent.

**Build:**

- Keep the existing pending-active default and all filters. Put overdue actionable work first, show active filter values and result count, and make each assignment's employee, training, due date, progress, and action easy to scan. Use a narrow-width layout that does not hide the action or status. Give tables captions and associated headers where tables remain.
- On assignment detail, place current status, deadline change, cancellation, and manual reminder ahead of evidence. Group slide acknowledgments, quiz attempts, assignment history, and notification history under clear headings or disclosures without removing audit detail.
- Beside **Send reminder**, show the next scheduled reminder and any rate limit. After sending, display **Queued for delivery** on the notification record; later status must reflect the recorded worker outcome. Distinguish **Sent**, **Blocked**, **Suppressed**, and **Ambiguous** with an appropriate next action. Preserve retry and reconciliation restrictions.
- Keep notification filtering and detailed attempts, but make status and outcome the first facts on the log/detail pages. Show technical delivery keys and revisions after the action-oriented summary.

**Acceptance and verification:**

- With pending, overdue, completed-late, canceled, and inactive examples, default and filtered results agree with assignment evidence. At desktop and narrow widths, an administrator can find and open the overdue item.
- Change a deadline, cancel an assignment, and inspect the recorded old/new values and reason. A queued manual reminder is never labeled delivered; a worker-accepted reminder shows its actual outcome.
- Send two nudges within 24 hours and verify the second remains rate limited. Confirm the next scheduled reminder remains visible and unchanged. Employee role cannot access administrator reports or actions.
- Demo: an administrator filters overdue work, queues a reminder, checks the notification record, runs the sink worker, and sees the updated outcome.

## Integrated validation and release check

After HCI-06, demonstrate a complete synthetic administrator-to-learner scenario: author and review a version, assign it, resume and acknowledge slides, correct an invalid quiz answer, fail and retry, inspect completion, filter overdue work, and queue and dispatch a reminder. Include a canceled/replaced assignment and an inactive employee in the test data.

Run the existing Django suite and targeted new tests. Review screenshots for learner and administrator states at desktop and narrow widths. Perform manual keyboard, screen reader, and 400% zoom checks on each completed journey; log findings and fix task-blocking problems. Conduct moderated sessions with representative administrators and employees before treating the redesign as validated. Record baseline and post-change task completion, errors, wrong turns, and ability to distinguish queued from sent reminders. These checks support the HCI plan and do not replace the MVP's production-readiness gates.
