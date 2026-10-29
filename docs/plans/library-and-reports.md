# Training library and reporting implementation plan

Implemented locally, 2026-10-28. The user authorized this plan for implementation. All six slices are present; browser layout and keyboard review are left for direct user review at the user's request. See [validation](../hci-validation.md#library-and-completion-reports-october-28-2026) and [US-20 / US-21](../user-stories.md#epic-i-library-and-completion-reports).

Adapt the six [reference screenshots](../screenshots/) into a clearer training library, a guided draft import, and administrator reports by training and employee. Keep the app's separation of training definitions, assignments, and execution evidence. Deliver the library and reports first; text file import is a separate extension.

## Follow on content redesign

The later [document training model proposal](document-training-model.md) changes the authoring direction from flat slides to a nested collection of documents with structured content. The library, reports, and text importer described here are implemented against the current slide model; their content units and import conversion will need the coordinated changes described in that proposal.

## Design direction

Use the current shell and final color tokens in [app.css](../../training/static/training/app.css): navy navigation, a pale canvas, white surfaces, royal blue actions, serif page headings, sans-serif body text, and green/amber status badges. Carry over the screenshots' compact toolbars, aligned rows, breadcrumbs, summary panels, and drilldowns. Keep meaningful titles readable and actions available as the viewport narrows.

The following imagegen concepts use synthetic examples. They depict the proposed result, including routes and features that do not exist yet. This plan governs behavior when image details differ.

| Reference screenshot | Adapted concept | Intended behavior |
| --- | --- | --- |
| 12:42:46 library and 12:48:01 creation menu | [Library and creation](../design/screenshot-alignment/library-create-concept.png) | Searchable training rows, simultaneous published/draft badges, clear actions, and a small creation menu. |
| 12:41:48 content report | [Reports overview](../design/screenshot-alignment/reports-overview-concept.png) | Summaries by training or employee, explicit assignment counts, completion rates, and filtered CSV exports. |
| 12:41:59 training report | [Training report](../design/screenshot-alignment/training-report-concept.png) | Employee assignment rows and a version-specific slide acknowledgment view. |
| 12:42:13 employee report | [Employee report](../design/screenshot-alignment/employee-report-concept.png) | One employee's assignment cycles, progress, deadlines, scores, and completion history. |
| 12:46:44 import dialog | [Import training drafts](../design/screenshot-alignment/import-drafts-concept.png) | Select text files, review the proposed drafts, create them, and open the editor. |

The squiggle and obscured wording are obsolete and have no product meaning. The [cleaned reference](../screenshots/import-content-cleaned.png) leaves that area blank. All five new concepts were generated with built-in imagegen; the [full prompt set](../design/screenshot-alignment/prompts.json) is retained.

## Scope and existing foundations

The current [training library](../../templates/training/list.html) already has search, pagination, draft/published filters, version history, and edit/assign actions. [Training display titles](28-training-display-title.md) already prefer the newest published title, then the draft title. [Library quizzes](../../templates/training/library_quiz_list.html) remain independently reusable; attachment creates a copy in a training draft.

The [assignment report module](../../training/assignment_reports.py) already provides status filters, scores, CSV output, and the data for the [pending workload](../design/admin-tracking-relay.md) and [quiz outcomes](../design/admin-quiz-outcomes.md) charts. The employee page currently edits profiles; a training report needs its own destination. The app has no file importer, upload storage, content taxonomy, ownership, or signature model.

| Deliver in this plan | Leave for a separate feature |
| --- | --- |
| Library presentation and creation menu; reports by training and employee; slide acknowledgment summaries; matching CSV exports; bounded Markdown/text import into new drafts | Companies, policy/process categories, nested subjects, global training order, requests, templates, ownership, verification, signatures, job titles, manager hierarchies, and last-active tracking |
| Existing training, slides, attached quizzes, library quizzes, and supporting links | Flowchart, video, checklist, SCORM, or general document authoring |
| Plain text extraction from `.md` and UTF-8 `.txt` | DOCX, PDF, PPTX, XLSX, OCR, embedded media, AI-generated content, and attachment hosting |

Supporting material remains a named HTTPS link under [US-17](../user-stories.md#us-17--include-supporting-learning-material). Importing a file creates training draft content; it does not turn the file into supporting material.

## Proposed product rules

These rules govern the local implementation and are recorded in US-20 and US-21.

### Completion and report populations

- The unit of reporting is one **assignment cycle**. A completed cycle and its pending renewal are two records. Preserve published version and cycle in detail rows and assignment CSVs.
- Default completion reports include pending and completed, nonarchived assignments for active synthetic employees. They include every published version and cycle unless filtered. They exclude canceled and waived assignments from the completion denominator.
- Completion rate is `completed assignments / (completed + pending assignments) × 100`. Show exact counts alongside the rate; show `—` and “No eligible assignments” when the denominator is zero. Round displayed percentages to one decimal when needed; sort by the unrounded value.
- Show **Employees** as the distinct employees in the selected assignment population and **Assignment cycles** as the record count. Do not sum employee counts across training rows. Label headline rates “Assignment completion”; do not describe them as current compliance or an average quiz score.
- Pending overdue is a subset of pending assignments, derived from the existing deadline rules at one captured request time. Display dates in the organization time zone.
- Allow an explicit activity filter and separate history views for canceled, waived, and archived assignments. History rows retain their actual ending and progress. Show no completion percentage in an exceptions-only view. Switching views resets incompatible filters visibly.
- A training with no eligible assignments shows zero assignments and no rate. The training overview includes published trainings; draft-only trainings remain in the authoring library. The employee overview includes synthetic employees matching its activity filter, including those with no assignments.
- The top-level default counts every eligible cycle. A version filter is available on a training report. A future “latest cycle only” or compliance view requires its own definition; it must not silently replace this denominator.

For example, 15 completed and 5 pending assignments produce 75%, even if they belong to only 12 employees. An earlier completion and a pending renewal remain visible together.

### Progress and evidence

- Keep **State**, **Slides acknowledged**, **Latest quiz score**, and **Completed at** separate. Six of six acknowledgments with a failing quiz remains pending. A slide-only assignment shows `N/A` for its quiz score.
- Reuse the existing completion, grading, and deadline rules. An acknowledgment records an explicit action and does not establish attention, comprehension, verification, or an electronic signature.
- The **By slide** view requires one published version. Its denominator is the same eligible assignments for that version, including separate cycles. Every slide displays position, title, acknowledged count, assignment count, and acknowledgment rate. The view offers assignment links behind each count.
- Current group membership may appear as contextual information on an employee report. Do not infer historical recipients from it or label current members as assigned. Group filtering is outside the first delivery.
- Historical assignment rows use their assigned version's title. Training summary rows use the canonical training display title; changing a draft never relabels historical evidence.

### Search and exports

Scope controls apply to summaries, totals, detail rows, and exports before pagination. Use the same validated report request for all of them. Report search matches employee name/email and assigned version title, as the existing assignment search does; show a persistent search indicator. Preserve matching empty training/employee rows only when no search is active, so searching cannot populate unrelated results.

Offer clearly named exports: **Export training summary CSV**, **Export employee summary CSV**, **Export assignments CSV**, and **Export slide acknowledgments CSV**. Each exports the complete filtered population, identifies generation time and organization time zone, preserves the relevant IDs/version/cycle, and uses the existing spreadsheet-safe value encoding. No answer keys or submitted answers appear in these files. Export links preserve cohort, search, version, and history mode, even when a particular presentation filter has no equivalent in the old exporter.

## Navigation and proposed routes

Keep **Assignments** as the operational destination for workload, quiz outcomes, reminders, deadlines, and administrative endings. Add **Reports** for completion summaries. Link to reports from training rows and employee profiles, and to the existing assignment detail from report rows.

| Proposed route | Purpose |
| --- | --- |
| `/reports/?view=training` | Training summary, the default report view. |
| `/reports/?view=employee` | Employee summary. |
| `/reports/trainings/<training_id>/` | Training detail with By employee and By slide tabs. |
| `/reports/employees/<employee_id>/` | Employee assignment history and summary. |
| `/reports/export.csv` | Summary CSV; validated `view` selects its schema. |
| `/reports/trainings/<training_id>/export.csv` | Assignment CSV or slide summary CSV for the selected tab. |
| `/reports/employees/<employee_id>/export.csv` | Employee assignment CSV. |
| `/trainings/import/` | File selection and validation. |
| `/trainings/import/<batch_id>/` | Review, commit, and result states for an owned import batch. |

Every new route requires the existing administrator check and synthetic-data restriction. Use UUID parameters and safe return links. GET requests render state; creation and import commit require CSRF-protected POST requests. Add the import menu item only when its complete workflow ships. Until then, the creation menu contains Training and Library quiz.

## Delivery sequence

### 1. Define shared report queries and calculations

Supports **US-11**, **US-13**, and **US-19**. Extend their acceptance criteria to cover the new completion report population while retaining the existing chart populations.

Add a focused `completion_reports.py` module for a validated report request, eligible assignment query, summary calculations, and report rows. Reuse `ReportFilters`, `report_assignments`, `assignment_status`, canonical training labels, displayed quiz scores, and export encoding where their semantics fit. Make differences explicit: the current dashboard defaults to pending work, whereas completion reports need pending and completed records together. The existing CSV endpoint cannot be reused unchanged when that would export canceled or waived records alongside the report's completion population.

Aggregate assignment counts independently from slide acknowledgments and quiz attempts to avoid multiplying rows through joins. Count distinct employees separately. Build summaries in database queries before pagination; fetch assignment evidence for the current detail page. Reuse these calculations in exports without computing a second set of business rules.

**Acceptance:** fixtures covering renewals, multiple versions, failed-then-passed attempts, slide-only training, deadlines, inactive employees, every ending, and archives produce the documented counts. Empty populations show no percentage. Summary/export totals agree beyond page one. Query counts do not grow per displayed row, and existing workload and quiz-outcome tests remain green.

### 2. Refine the training library and creation menu

Supports **US-01**, **US-03**, **US-04**, and **US-16**. Work primarily in [views.py](../../training/views.py), [list.html](../../templates/training/list.html), and the shared stylesheet.

- Keep the All, Has draft, and Published filters, canonical title ordering, search, pagination, and version history. Preserve filters in return links.
- Add bounded slide and quiz-presence summaries from the version being described. Label draft facts separately when they differ from the latest publication; avoid a count that appears to describe both versions.
- Keep one primary row action: Edit draft or Start revision. Keep Assign training for published content and Review renewals where already applicable. Add View report when the report route ships.
- Implement a small creation disclosure with Training and Library quiz, using the existing POST creation endpoints. Add Import training drafts in Slice 6.
- Allow Title A–Z and Title Z–A through an allowlisted sort parameter and stable ID tie-breaker. Use real controls rather than decorative sort icons.

**Acceptance:** mixed draft/published states remain visible together; draft-only records cannot be assigned. Long titles, multiple badges, expanded history, and empty results remain readable. The disclosure works by keyboard, closes predictably, and restores focus. Capture desktop and narrow layouts.

### 3. Add the reports overview and summary exports

Supports **US-11**, **US-13**, and **US-16**. Add `report_views.py`, summary templates, URL patterns, and an administrator-only Reports navigation item in [base.html](../../templates/training/base.html).

Provide By training and By employee views, a compact scope toolbar, search, pagination, and column sorting. Training rows show title, distinct employees, assignment cycles, completed cycles, assignment completion, and pending overdue. Employee rows show name/email, assignment cycles, completed cycles, assignment completion, and pending overdue. Zero-assignment rows show `—` for rates. Headline totals count the full selected population, including records outside the current page; completion rate is a ratio of totals, never an average of row percentages.

Use a labeled HTML progress bar with visible numerator and denominator. Keep the existing workload and quiz charts on Assignments. This comparison does not need an advanced chart or a new chart library; the existing SVG approach remains available if a later validated comparison calls for one.

**Acceptance:** filters, sort order, summaries, drilldown scope, and both summary CSV schemas agree. Invalid filters produce a clear error rather than silently broadening the population. Administrators can move from a training or employee summary to its detail and back with scope preserved. Learners cannot access reports or exports.

### 4. Add training details and slide acknowledgment summaries

Supports **US-11**, **US-13**, and **US-16**, with the evidence rules in **US-09** unchanged.

Show a training summary, published-version filter, and By employee / By slide tabs. By employee contains one row per assignment cycle, so an employee may appear repeatedly. Include assigned version, cycle, state, deadline status, acknowledged/total slides, latest quiz score, due date, completed timestamp, and Open assignment. Expose passing score through assignment detail as today.

By slide prompts for a published version instead of merging different slide definitions. Recompute its population from the same scope plus that version. Changing versions resets the slide selection. Show every slide, including those with zero acknowledgments; an empty assignment denominator yields no rate. Provide an accessible drilldown into the assignments behind a count and the corresponding tab-specific export.

**Acceptance:** no slide count crosses version boundaries; cycles remain distinct. All-slides-acknowledged plus a failed quiz remains pending. Assignment links preserve the return context without weakening URL checks. The CSV includes the same version and population as the visible tab.

### 5. Add employee training reports

Supports **US-11**, **US-13**, and **US-16**. Add a View training report link to employee profiles and preserve Edit employee as a separate action.

Show name, email, role, active status, optional current groups, completion counts, and the employee's assignment rows. Retain repeated trainings across renewal cycles and versions. Support the same filters and CSV rules as training details. If an inactive employee is opened directly, select and visibly label that employee's inactive scope so the default activity filter does not hide their history.

**Acceptance:** a completed training and its pending renewal both appear, deactivation preserves evidence, and exception/archive views retain their true endings. Empty employees have a clear zero-assignment state. Profile edits and activity changes still use their existing routes and authorization.

### 6. Add bounded text file import into new drafts

This is a new authoring story alongside **US-01**, **US-02**, and **US-04**. Add its story and acceptance criteria to [user-stories.md](../user-stories.md) before implementation. Import is independently deliverable after Slice 2; it need not block reports.

Use a full-page workflow in the shared shell, with Select files, Review plan, Import, and Done steps. Provide a normal file chooser and optional drag-and-drop. The first release accepts up to five `.md` or UTF-8 `.txt` files, at most 1 MB each. Bound the entire request at the server as well as in the form. Reject unsupported or invalid content with a file-specific explanation and preserve valid review inputs.

Conversion rules:

- One selected file creates one new training with draft version 1. No existing training is replaced, no quiz is inferred, and nothing is published or assigned.
- Markdown: use the first level-one heading as the proposed training title, or the filename stem when absent. Level-two headings start slides; retain lower headings in the body. Ignore heading syntax inside fenced code. Put content before the first slide heading into an Introduction slide. Without level-two headings, create one slide from the remaining content.
- Text: propose the filename stem as the training title and one slide containing the text. Let the administrator rename and split it in the existing editor.
- Render imported bodies as escaped plain text using the existing slide presentation. Strip structural heading markers used for titles; preserve remaining text. Do not fetch links, execute embedded content, or claim rich Markdown fidelity.
- Apply the editor's current limits: training title and slide header 200 characters, slide body 10,000 characters, and at most 100 slides. Block oversize conversions with a correction path; do not truncate. Empty titles/bodies are review warnings and remain subject to the existing publication checks.

Review shows each filename, editable proposed title, slide count, a slide preview, and warnings. Administrators can remove a file or go back. The final action states exactly how many drafts it will create.

Implement parsing separately from persistence in `training_imports.py`. Store the normalized review plan in an administrator-owned `ImportBatch`, with a plan revision, expiry, state, and committed result IDs. Keep raw uploads out of permanent storage; discard temporary files after parsing. Plan validity expires after one hour. A CSRF-protected commit locks the batch, validates its owner/revision/expiry, and creates all selected drafts in one transaction using the existing draft services. Store the resulting training IDs in that transaction. Repeated submissions return those results instead of creating duplicates. An expired uncommitted batch requires a new selection; it never commits from stale client content.

Provide a cleanup command for expired review content and results older than seven days; configure it alongside the existing local operational commands. Once cleaned up, an old batch URL returns an expired/not-found response and cannot create drafts. Removing an import record never deletes the created trainings. Record the short-lived review storage decision in the next sequential ADR before adding its migration.

**Acceptance:** canceling before commit creates nothing; a conversion failure creates no partial batch of trainings; duplicate commit and concurrent commit create one result set. Test another administrator's batch, expired plans, invalid encoding, renamed unsupported files, request limits, oversize bodies, fenced-code headings, empty files, and retained title edits. Created drafts open in the ordinary editor and pass through normal publication review. Run concurrency checks on PostgreSQL for the batch lock.

## Verification and delivery checks

- Validate this plan's relative links and the five concept files. Keep imagegen prompts with the images. Imagegen examples illustrate hierarchy and content; browser captures of the implemented UI supply layout evidence.
- For implementation, run focused tests for each completed slice, then the full Django suite, `manage.py check`, and `manage.py makemigrations --check --dry-run`. The report and library slices should need no schema migration; the importer adds its own migration and must be exercised from a clean database and an upgraded database.
- Compare visible counts, drilldowns, and CSVs using a fixed clock and synthetic fixtures. Cover more than 25 assignments so pagination cannot hide aggregation errors. Verify CSV formula escaping and role restrictions.
- Check keyboard flow, visible focus, table headings, progress text, error association, file selection, and disclosure behavior. At 320 CSS pixels, 400% zoom, desktop width, and short window heights, preserve all facts and actions. Restrict horizontal scrolling to named, keyboard-accessible data regions, following the [responsive layout plan](responsive-layout-and-visibility.md).
- Capture the library, each report view, the slide tab, empty/history states, and import review/error/done states. Add the browser verification record to [HCI validation](../hci-validation.md).
- Retain the [real-data release gates](../implementation-plan.md#mvp-release-criteria). This plan uses synthetic employees and does not change identity, notification delivery, or rollout readiness.

## Delivery order

Build Slice 1 first. Slice 2 can follow independently of report UI work. Deliver Slice 3, then Slices 4 and 5. Deliver Slice 6 after the library menu is available. Each slice is complete only when its acceptance checks pass; keep unavailable menu actions out of the shipped interface.
