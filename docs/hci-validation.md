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
