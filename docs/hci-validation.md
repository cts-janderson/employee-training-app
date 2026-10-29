# HCI implementation validation

The six [HCI slices](hci-implementation-plan.md) are implemented for the synthetic local workflow. This record separates automated checks from human evaluation still needed before treating the redesign as validated.

## Automated checks

- `DATABASE_URL=sqlite:///:memory: DJANGO_DEBUG=1 DEV_AUTH_STUB=1 .venv/bin/python manage.py test training --verbosity 0`: 96 tests run, 1 PostgreSQL-specific test skipped. This includes a synthetic administrator-to-learner journey through publication review, assignment review, quiz correction and retry, overdue follow-up, reminder dispatch, and a canceled replacement, plus quiz-library and assignment-archive checks.
- `DATABASE_URL=sqlite:///:memory: DJANGO_DEBUG=1 DEV_AUTH_STUB=1 .venv/bin/python manage.py check`: no issues.
- Existing policy tests continue to cover ownership, immutable versions, grading, deadline boundaries, idempotent assignment and acknowledgment, notification rate limits, and worker outcomes. PostgreSQL row-lock behavior still needs the configured PostgreSQL test environment.

## Human review still needed

- Review the touched pages with keyboard only, a screen reader, 320 CSS-pixel width, and 400% zoom. Check focus order, heading structure, reflow, labels, and error announcements. Record any task-blocking findings and fixes here.
- Conduct moderated synthetic-data sessions with representative administrators and employees. Record baseline and post-change task completion, errors, wrong turns, and whether participants distinguish queued reminders from sent ones.
- Screenshots were waived for this implementation wave because local headless Chrome exits before capture. The user will review the interface directly.

The [MVP release gates](implementation-plan.md) for real employee data, authentication, PostgreSQL concurrency, deployment, and worker operation remain separate.

## Administrator assignment chart review (October 27, 2026)

- The chart and dashboard use the pending report query before pagination. Focused tests compare chart, list, and CSV counts across employee, training, activity, progress, deadline, and search filters. They also cover empty results, due-time equality, ties, the top-eight rule, and the current published training label.
- `DATABASE_URL=sqlite:///:memory: DJANGO_DEBUG=1 DEV_AUTH_STUB=1 .venv/bin/python manage.py check` passed. The focused chart, report, and administrator tests passed. The full training suite passed with 175 tests and 3 skips on SQLite.
- The [desktop](design/admin-assignment-chart-desktop.png) and [320 CSS-pixel](design/admin-assignment-chart-320.png) captures use a Django-rendered snapshot with 56 synthetic assignments across nine trainings. Chrome inspection found the SVG title, legend, segment labels, bar-end counts, scale, and footer legible at desktop width. At 320 CSS pixels, the SVG and values table scroll independently of the filters and assignment list. The chart was also inspected at 400% browser zoom.
- Chrome's accessibility tree exposes the SVG title and description, a keyboard-focusable values disclosure, column headers for the four groups, row headers with full training names, and exact cell counts. A human screen-reader pass remains to be done.
- PostgreSQL was unavailable on `localhost:5432`; an isolated cluster also could not initialize because the sandbox denied shared memory creation. The PostgreSQL grouping-query measurement and concurrency checks remain open. The sandbox denied a loopback development server, so the visual review used the rendered static snapshot, not a live HTTP session.

## Administrator quiz outcomes review (October 27, 2026)

- The [quiz outcomes design](design/admin-quiz-outcomes.md) defines three exclusive outcomes per quiz assignment cycle and keeps the existing pending workload chart as a separate mode. The chart and its employee table use the same filtered query before result pagination.
- Focused tests cover a pass after a failed attempt, a retry needed, no attempt, excluded states, repeat cycles, all matching rows beyond page one, and more than eight trainings. The SVG is parsed as XML. The full training suite passed with 186 tests and 3 SQLite skips; `manage.py check` reported no issues.
- A Django-rendered snapshot using synthetic assignments was inspected in Chrome. The chart labels, percentages, retry and cycle counts, expanded values table, grouped employee rows, and assignment links were visible and legible at desktop width. Chrome's accessibility tree exposed the SVG title and description, values disclosure, table headers, group header, and employee rows. A human keyboard, screen-reader, and narrow-width review remains open.

## Chart clutter cleanup (October 27, 2026)

- The pending and quiz charts now keep their titles, legends, bars, counts, and accessible descriptions while dropping report-style headers, repeated filter summaries, and source footers. A scope note appears only when more than eight trainings match.
- The earlier screenshots above predate this cleanup. The current pending chart was inspected in a live local Chrome view. The full training suite passed (187 tests, 3 skips), and `manage.py check` reported no issues with the local SQLite configuration. The quiz chart's revised layout still needs a visual review.

## Quiz editor follow-on (October 27, 2026)

- The attached and library editors now return focus after structural actions, show saved-question readiness, allow single-choice and multiple-selection conversion, and link to a learner-style saved-quiz preview. True/false labels and duplicate-rank feedback were updated. The preview renders answer controls without answer keys or a submit action.
- A Django-rendered synthetic library quiz editor was inspected in Chrome at desktop width and 400% zoom (about 420 effective CSS pixels). The question card and actions reflowed without horizontal clipping. A live Django server could not bind a loopback port in this sandbox, so this was a static snapshot of the rendered template.
- The complete SQLite training suite passed with 236 tests and 5 expected PostgreSQL-only skips. `manage.py check` and `makemigrations --check --dry-run` found no issues or model changes. A human screen-reader pass remains open under the HCI validation plan.

## Responsive layout pass (October 27, 2026)

- The shared stylesheet now uses the available content width for library, assignment, notification, and quiz editor reflow. Training descriptions and actor names wrap instead of being silently truncated. Collection panels no longer clip focus outlines. The desktop sidebar scrolls internally when the window is short.
- Assignment status gains an in-row label when its column heading is hidden. The pending and quiz charts and their values tables have named keyboard-focusable horizontal scroll regions, with visible scroll guidance when space is limited.
- Chrome was used to inspect Django-rendered static snapshots with synthetic seed data. The training library was checked at 320 and 1024 CSS pixels and in a wide desktop window; quiz library, assignment dashboard, notification log, and quiz editor were checked at 320 pixels; quiz library, assignment dashboard, and notification log were checked in the wide desktop window. At 320 pixels, the training and assignment pages measured document width equal to viewport width. The chart cue, stacked status, and notification field labels were visible. These were static snapshots because the sandbox denied a loopback server bind.
- The complete SQLite training suite passed: 236 tests, 5 expected PostgreSQL-only skips. `manage.py check` and `collectstatic --noinput` passed.
- A long synthetic training title and administrator name were inspected in the wide desktop snapshot; the title wrapped within its grid column and the row grew without hiding version or action controls. Filtered empty states now offer direct clear/reset links, and initials have a fallback if a name is missing. These server-rendered pages have no separate in-page loading state.
- A live keyboard and screen-reader review and an actual 400% browser zoom check remain open. The latest request prioritizes desktop layout; mobile navigation redesign is outside this pass.

## Library and completion reports (October 28, 2026)

- Implemented all six slices of the [library and reports plan](plans/library-and-reports.md): library summaries and creation menu, shared completion populations, training/employee overviews, assignment-cycle and version-specific slide details, matching CSV schemas, and reviewed text draft import. [US-20 and US-21](user-stories.md#epic-i-library-and-completion-reports) define the added behavior.
- `./scripts/test-local.sh` passed on PostgreSQL: **266 tests**, no skips, Django configuration and migration checks clean, and static assets built. The new import migration was exercised from an empty test database and applied successfully to the existing local development database through migration 0013. `bash -n scripts/start-local.sh` passed.
- Tests cover separate renewal cycles, multiple versions and historical titles, distinct employee totals, exceptions and archives, inactive/real employee exclusion, empty entities, search, ordering, totals beyond page one, spreadsheet formula encoding, safe report return links, and learner denial on every report/export route. Detail evidence loads in two queries for eight rows; grouped summaries use one query. Acknowledging all slides with a failed quiz remains pending, and a later passing attempt does not multiply assignment counts.
- Import checks cover named conversion errors, escaped previews, UTF-8/type/binary/size/slide limits, fenced headings, title edits and removal, owner/revision/expiry checks, CSRF, cleanup without training deletion, rollback after a later draft fails, and simultaneous PostgreSQL commits producing one result set. The normal startup script runs short-lived review cleanup every minute and stops it on script exit.
- Chrome review was stopped at the user's request. Browser screenshots, keyboard interaction, screen-reader behavior, 320 CSS-pixel reflow, short-height layouts and 400% zoom have **not** been validated for this wave and remain for direct review. The imagegen concepts are design references, not implementation screenshots.
- These local synthetic-data checks do not complete the remaining deployment, identity, external delivery, backup, human evaluation, or real-employee-data release gates.

## Create content control follow-up (October 28, 2026)

- The user reported that activating Create content appeared to do nothing. The earlier server tests did not cover opening the disclosure. Its native disclosure interaction was not reproduced in a browser; browser review remains waived at the user's request.
- Replaced the native details/summary control with an explicitly toggled button and a hidden choices panel, including `aria-expanded`, Escape focus restoration, outside-click dismissal, and focus-out dismissal. An ordinary Create content link remains available until initialization succeeds, leading to an administrator-only page containing the same three actions. The stylesheet explicitly hides elements carrying the hidden attribute. The revised script URL avoids reusing the previous cached script.
- `node --test training/js_tests/create-menu.test.cjs`: five event-behavior checks pass without browser automation. These cover opening/closing, interaction within choices, Escape, outside clicks/focus, and preserving the fallback when initialization cannot complete. They do not validate browser rendering.
- Four CSRF-enforced HTTP workflow tests follow the rendered menu actions: create a training and open its draft editor; create a library quiz and open its editor; import Markdown, review, create and open an ordinary draft; and navigate to creation choices without scripting. The fallback test failed before implementation and passes afterward.
- `./scripts/test-local.sh`: all 270 PostgreSQL tests pass; configuration/migration checks and static build pass. Live HTTP checks return 200 for the library, fallback choices, and revised script, whose contents match the source. The local server on port 8011 was restarted with automatic reload enabled.

## Guided quiz creation (October 28, 2026)

The [quiz workspace delivery](plans/quiz-pages-layout.md#guided-creation-roadmap-first-delivery) adds Setup, Questions, and Review navigation, saved-only readiness links, an unsaved-review notice, and next actions for attachment/publication review. Automated coverage verifies saved-versus-submitted state, correction links, preservation during removal/cancellation/conflicts, role checks, and publication after copy attachment. The full SQLite suite passed (277 tests, six database-specific skips); eight Node event checks passed. Migration drift and static build checks passed.

Browser inspection and screenshots remain pending: a separate synthetic preview server could start after sandbox permission, but the computer-use browser inventory was empty. No visual, narrow-layout, keyboard, or assistive-technology review is claimed. Document authoring remains proposed; its editor feasibility gate is not complete.


## Document authoring (October 28, 2026)

Implemented the [document model](plans/document-training-model.md) with [version-owned structured content](adr/0008-version-owned-document-content.md). New interface creation/revision actions use document format; legacy published slides and evidence remain intact. Authors can use nested sections/documents, rich text, explicit save, reviewed subtree removal, moves/copies, quiz authoring, and publication review. Learners have document navigation, resume, per-document acknowledgment, derived section counts, quiz gating, document reports, and exports. Imports record document versus legacy slide conversion in the review plan.

- Full PostgreSQL verification passed: 299 Django tests, no skips, including competing document saves, duplicate acknowledgments, and an upgrade from migration 0014 with existing published slide/resume/completion/acknowledgment evidence. Configuration, migration drift, and static build checks passed.
- Thirteen Node tests passed, including actual ProseMirror toolbar commands, undo/redo, schema-aware paste, safe link insertion, submitted serialization, nested lists, Unicode/whitespace/footer round trips, and unsaved-navigation notification for formatting-only edits.
- The dependency installation was corrected to patched `prosemirror-view` 1.42.8 and compatible `prosemirror-model` 1.25.12. npm reported zero known vulnerabilities. The generated bundle and third-party notices are served locally.
- Native Chrome loaded the editor against a separate temporary synthetic database. The accessibility tree exposed the outline, labeled rich-text field, formatting controls, document actions, and saved revision. A screenshot captured the desktop layout and rendered headings/list/callout. Training settings were subsequently collapsed by default to bring the editing canvas into view sooner.
- Full manual keyboard/assistive-technology review, 320 CSS pixel and 400% zoom checks, and captures of every learner/report/error state remain open. The native browser check establishes loading and visible controls; the automated editor tests establish interaction behavior. No broader manual validation is claimed.

Normal local setup must apply migration 0015 and restart Django before using document authoring. The real-data readiness gate is unchanged.


## Training creation visual refinement (October 28, 2026)

Refined the creation landing page and document workspace using a scoped authoring stylesheet. Creation options distinguish a blank training, text import, and a reusable quiz. The document editor now uses a compact contents panel, selected-item styling, a paper-like title/canvas, grouped formatting controls, and disclosure panels for training details, reference insertion, content organization, and supporting material. Blank drafts provide a clear first-document action. Existing form field names, CSRF protection, explicit save, revision checks, error/removal reviews, and publication behavior remain intact.

Verification: 40 focused Django tests passed with three PostgreSQL-specific skips; 13 JavaScript tests passed. Configuration, static build, and whitespace checks passed. Native Chrome verification created a synthetic training, added a document, entered a title/body, and saved revision 2 successfully. The toolbar wrapped at 150% zoom and the contents/editor stacked at 250% zoom; zoom was restored afterward. The user requested no further screenshots. Full manual assistive-technology and exact 320 CSS pixel review remain unclaimed.


## Library quiz editor visual refinement (October 28, 2026)

Refined the library editor with a compact outline column, clearer Setup/Questions/Review hierarchy, saved-preview and save actions, described question-format buttons, a welcoming empty state, and roomier question/answer cards. Correct-answer selections receive a subtle border/background treatment. Remove and reorder controls are quieter, and saved readiness/attachment information remains visible in the review area. Container queries stack the outline, setup fields, format controls, and option rows as available space narrows.

The stylesheet is loaded only for library quizzes and scoped to the library workspace. Existing field names, action values, validation/focus anchors, incomplete saves, conflict/removal handling, saved-only readiness, learner preview, and independent attachment copies remain intact. Attached and learner quiz layouts retain their styling.

Verification: 51 focused Django quiz, presentation, workspace, and creation tests passed; 13 JavaScript checks passed. Django configuration, static build, and whitespace checks passed. No screenshots were taken, following the user's preference. Visual and assistive-technology review of this refinement is not claimed.


## Quiz actions without page resets (October 28, 2026)

The reload/scroll regression was reproduced with `node --test training/js_tests/quiz-actions.test.cjs`: Add option did not prevent native form navigation. Both quiz editors now intercept form submissions, send the existing CSRF-protected POST, and replace the returned quiz workspace without navigating the browser or applying redirect hashes. Stable question identities preserve the viewport anchor; focus uses prevent-scroll behavior. Outline disclosure/scroll state survives updates. Removal review and validation/conflict feedback stay beside the affected question.

Delegated handlers continue working after every replacement, including ordering warnings and unsaved-navigation protection. Requests snapshot all form values and the clicked action before disabling controls to prevent duplicate submissions or edits being overwritten while saving. Network failures, unexpected responses, and timeouts retain the current fields, restore controls, and never retry an uncertain write automatically. Without enhancement support, the ordinary server form still works.

Verification: 48 focused Django quiz/workspace/presentation tests and 24 Node tests passed. The regression now checks prevented navigation, submitted action/CSRF/revision, repeated updates, retained scroll/URL, pending-request locking, failed requests, removal cancellation, server validation/conflicts, and post-update ordering warnings. Configuration, static build, and whitespace checks passed. No screenshots were taken. Native-browser scroll behavior and assistive-technology review remain unclaimed.


## Passing threshold slider (October 28, 2026)

Both quiz editors progressively enhance the threshold field into a keyboard-accessible slider with a live percentage readout. Values 1–100 set the passing percentage; the leftmost position leaves the draft threshold unset. The existing canonical form value and server validation are preserved, including blank/invalid submitted values until deliberately changed. Without JavaScript, the number field remains usable. The slider reinitializes after in-place updates and participates in unsaved-change tracking.

Verification: 48 focused Django tests and 28 JavaScript tests passed, including threshold display/submission, unset/clear behavior, invalid-input preservation, and slider updates after asynchronous saves. Configuration, static build, and whitespace checks passed. No screenshots were taken.


## Editable threshold percentage (October 28, 2026)

The percentage box beside the slider is now a labeled number field accepting whole percentages from 1 to 100. Typing updates the slider, moving the slider updates the field, and both submit one canonical threshold value. Invalid amounts are flagged before an enhanced save; clearing the field retains the existing unfinished-draft behavior. Synchronization survives in-place editor updates. The original number field remains the fallback without JavaScript.

Verification: 48 focused Django tests and 31 JavaScript checks passed, including two-way synchronization, bounds/integer validation, blank drafts, and numeric entry after asynchronous updates. Static build and whitespace checks passed. No screenshots were taken.


## Quiz presentation and application copy (October 29, 2026)

Refined the learner quiz, learner preview, and results with readable question cards, clearer answer selection, compact ordering controls, grouped submission actions, and responsive layouts. Ordering fields now use short, associated labels such as “Step 1.” Question identities, submitted field names, grading, validation, and answer preservation remain intact.

Reviewed visible wording across authoring, reports, assignments, employee/group management, imports, and notification history. Removed revision counters, repeated implementation explanations, the completion formula paragraph, and delivery-worker terminology from ordinary views. Save/conflict feedback, recipient guidance, recovery instructions, and notification outcomes remain actionable. Locally processed notifications are labeled “Processed”; this does not claim external delivery. Stored audit details, concurrency guards, report calculations, exports, and data-readiness restrictions are unchanged.

Verification: all 304 Django tests passed on SQLite with eight PostgreSQL-only checks skipped; all 31 JavaScript checks passed. Presentation coverage checks ordering label associations, visible copy, saved/conflicting submissions with hidden revision guards, and notification labels without changing stored status. The editor bundle, static build, migration drift, and whitespace checks passed. No screenshots were taken, following the user's preference. Browser rendering and assistive-technology review of this refinement remain unclaimed.
