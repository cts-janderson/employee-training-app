# HCI review and UI/UX improvement plan

## Scope and method

This review covers the local Django interface for learners and administrators as implemented on October 26, 2026. It traces the templates and their view behavior against [the MVP stories](mvp.md) and [the implementation plan](implementation-plan.md). The review considers task flow, visibility of status, error prevention and recovery, information hierarchy, and accessibility. It is an expert review of code and content, not a usability study, visual browser audit, or accessibility conformance claim. Findings about likely user difficulty should be tested with representative users. The existing synthetic employee and local notification setup remains the test environment.

The target is a clear, accessible interface for the confirmed workflows: author, publish, assign, complete, retry, monitor, and remind. The confirmed assignment, grading, reminder, versioning, and real-data policies remain the constraints for this plan.

## What already works

- The learner and administrator areas share consistent navigation and page structure. Most controls use native HTML elements and visible labels.
- Slides show plain-text paragraphs as authored, and the learner can navigate without acknowledging them. The assignment page distinguishes slide progress from quiz completion.
- Bulk assignment reports an outcome for each employee. Administrator assignment and notification details preserve evidence and history.
- The quiz supports keyboard-operable native controls for all four question types. Result pages show overall outcomes and submitted answers without answer keys.

## Findings

Priority reflects impact on task completion and error recovery, followed by breadth of use. **P0** is a release blocker for the UI work; **P1** should precede a broader pilot; **P2** can follow after the core journeys are validated.

| Priority | Finding and evidence | Likely user impact | Related stories |
| --- | --- | --- | --- |
| P0 | Server validation for a quiz submission returns a plain HTTP 400 response from `submit_quiz_view` instead of the quiz with answers and an error summary. Duplicate selections in ordering are possible in the native controls, but rejected by the server. | A learner can lose an entire attempt while correcting one mistake, and may not know which question to fix. | US-09, US-10 |
| P0 | The draft and quiz editors submit remove actions directly. Publishing is also a direct submit from the long draft form. There is no review step or undo for removing a slide/question/quiz, and publication makes the version immutable. | Accidental actions can discard authored material or publish the wrong version. | US-01–US-04 |
| P1 | In `assignment_slide.html`, acknowledging a slide redirects to the assignment overview. The next slide link is available before acknowledgment, but the learner must navigate back into the sequence afterward. The page says only “Slide N,” without total count or remaining acknowledgments. | The primary completion path is interrupted and progress is hard to judge. | US-08, US-09 |
| P1 | `my_training` lists all noncanceled assignments by due date, including completed work. Each card presents version, due date, progress, and deadline as equally weighted text; the title is the only way in. | Overdue or unfinished work may be buried, and the next action is unclear. | US-08 |
| P1 | Draft publication errors are listed at the top as plain text, without links to the relevant fields. The quiz editor uses an error list in the same way. Save redirects provide no visible saved confirmation. The draft conflict page says submitted changes were not saved but offers only reload. | Authors must search long forms, cannot easily verify a save, and have a difficult recovery path after a conflict. | US-01–US-04 |
| P1 | `assignment_dashboard.html` presents 11 columns and a reminder action in a horizontally scrolling table. The table and notification log lack captions and column header scopes. Reminder eligibility, schedule, scores, and status compete in each row. | Scanning for follow-up work is slow, especially at narrow widths or high zoom; table relationships are harder to interpret with assistive technology. | US-11, US-12 |
| P1 | The shared header has no skip link or current-page indication. CSS has no explicit focus treatment or responsive layout rules beyond wrapping and table overflow. The UI has not been checked at mobile widths, high zoom, or with a screen reader. | Repeated navigation and long forms may be tiring or confusing for keyboard and assistive technology users. This is a validation gap, not a measured conformance failure. | All stories |
| P2 | `take_quiz.html` puts every question on one page. Ordering uses one select for each position and lets the same option be selected more than once. The question prompt is adjacent to, rather than part of, the fieldset legend. | Long quizzes are harder to scan, and repeated ordering choices are easy to make. | US-09, US-10 |
| P2 | Administrator assignment and notification pages expose operational detail such as deadline revisions, delivery keys, and full history alongside the immediate decision. “Send reminder” queues a request, while its outcome is only visible after opening another record. | Administrators may mistake a queued reminder for a delivered one or struggle to find the action they need. | US-11, US-12 |
| P2 | The employee add form clears its fields after a server error, and several form errors appear only at the top of a page. | Re-entering data and locating the problem takes extra effort. | US-05 |

These findings follow established guidance to identify errors near the relevant input, preserve a route to correction, label controls clearly, and show a clear outcome after a transaction. See the [W3C form validation tutorial](https://www.w3.org/WAI/tutorials/forms/validation/), [W3C user notification guidance](https://www.w3.org/WAI/tutorials/forms/notifications/), [WCAG 2.2](https://www.w3.org/TR/WCAG22/), and [GOV.UK confirmation page pattern](https://design-system.service.gov.uk/patterns/confirmation-pages/). WCAG 2.2 AA is a recommended evaluation target for the redesigned interface; no compliance target is yet recorded in the MVP.

## Design direction

### Learner journey

1. Make **My training** an action list: unfinished work first, then completed work; show due date, deadline state, slide progress, and one clear **Start**, **Resume**, or **Review** action. Keep the version visible in secondary information.
2. On assignment detail, show the next required action before history: first unacknowledged slide, then quiz when all slides are acknowledged, then completion. Keep free slide navigation and prior attempt history available.
3. In the slide view, show “Slide N of M,” the acknowledgment state, and what remains. After acknowledgment, take the learner to the next unacknowledged slide or to the assignment completion/quiz action. Preserve the ability to move freely and never treat a visit as acknowledgment.
4. Make the quiz recoverable: return invalid submissions to the form with answers intact, a linked error summary, and field-level guidance. Prevent duplicate ordering choices where feasible, with server validation still authoritative. After a pass, show that the **assignment is complete**; after a fail, show **Try again** and **Review attempt history** without revealing answer keys.

### Administrator journey

1. Split long authoring pages into a clear content outline and editing sections. Keep explicit save controls, display a saved timestamp or confirmation, and warn before leaving with unsaved edits. Make publication readiness visible before the publish action.
2. Add a review step for publishing that summarizes title, version, slide count, quiz presence, and passing threshold. Require an explicit final publish action. For removals, use an accessible confirmation or an undo path that preserves the item until final save.
3. Make bulk assignment a reviewable transaction: show selected version, due date including organization time zone, recipient count, and already known conflicts before submission; retain the existing per-recipient results afterward. Recheck conflicts when the request is submitted.
4. Make the assignment dashboard task focused. Default to actionable pending work, put overdue items first, show a compact status summary, and place detail and reminder actions in a predictable position. Retain all filters and evidence, with secondary fields in the detail page. Provide a usable narrow-width presentation and table semantics.
5. On assignment detail, separate **Current status and actions** from **Evidence and history**. Label manual reminder outcomes as **Queued**, **Sent**, **Blocked**, or **Suppressed** as recorded. Keep the next scheduled reminder visible beside the manual action; do not imply delivery at queue time.

### Shared interaction rules

- Use one page title, clear section headings, a skip link, current-page navigation, visible focus, readable dates, and the organization time zone where it affects a decision.
- Show required and optional fields before entry. On errors, keep entered values, link a top summary to each affected field, and place the correction beside that field. Use server validation for every rule.
- Use consistent language for **assignment**, **training version**, **acknowledged**, **completed**, **due**, **overdue**, and **reminder**, following [the glossary](GLOSSARY.md). Explain technical delivery terms in administrator details only where they help action.
- Keep success feedback specific: what changed, whether it is final, and what to do next. Distinguish a queued notification from confirmed delivery.
- Build and check responsive layouts at 320 CSS pixels, 400% zoom, keyboard only, and with a screen reader. Use WCAG 2.2 AA as the evaluation target, including focus visibility, error identification, labels, reflow, and target size where applicable.

## Delivery plan

The detailed [HCI implementation plan](hci-implementation-plan.md) defines six vertical slices, each with a user outcome, build scope, acceptance checks, and demo. The first three address the P0 findings; shared accessibility and styling improvements are delivered within the affected workflows.

| Slice | User outcome | Stories |
| --- | --- | --- | --- |
| HCI-01 | Correct an invalid quiz response without losing other answers. | US-09, US-10 |
| HCI-02 | Save and recover draft or quiz edits. | US-01–US-03 |
| HCI-03 | Review a saved training version before publishing it. | US-01–US-04 |
| HCI-04 | Find, resume, and complete assigned training with a clear next action. | US-08–US-10 |
| HCI-05 | Review bulk assignments and understand each recipient's outcome. | US-05 |
| HCI-06 | Find follow-up work and interpret reminder outcomes. | US-11, US-12 |

## Validation before rollout

- Run moderated task sessions with at least one administrator and several representative employees, including someone who uses keyboard navigation or assistive technology. Use synthetic data. Observe task completion, wrong turns, recovery, and confidence; do not rely only on stated preference.
- Test five tasks: publish a three-slide training with a quiz; assign it to two employees; resume and complete it; correct an invalid quiz response; find overdue work and queue a manual reminder. Include a failed quiz and a canceled/replaced assignment in the sample data.
- Record a baseline before redesign and compare: completion rate, time on task, help needed, validation errors, accidental actions, and whether users correctly distinguish queued from sent. Treat targets as hypotheses until baseline results exist.
- Run automated accessibility checks on representative pages, then manual keyboard, screen reader, high zoom, and narrow-width checks. Inspect visible error behavior and focus after redirects. Automated checks alone cannot establish WCAG conformance.
- Keep the existing Django workflow tests for policy behavior. Add focused interface tests for error preservation, next-action states, confirmation outcomes, and accessible names where regressions would be costly. Verify screenshots of representative learner and administrator states before closing UI work.

## Decisions to confirm with users

- Which devices and assistive technologies employees and administrators actually use, including whether slide reading commonly happens on phones.
- The typical and upper-bound numbers of assignments, employees, slides, and quiz questions; these determine whether search, pagination, or a stepped quiz becomes necessary.
- Whether the organization has a required accessibility standard, design system, brand, or content style. Until then, use WCAG 2.2 AA as the proposed evaluation target.
