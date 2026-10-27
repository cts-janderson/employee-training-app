# Deepen the training modules: round 3

## Context

Rounds [1](deepen-modules.md) and [2](deepen-modules-round-2.md) removed most of the places where one rule lived in several modules. Feature slices 13–21 (report, export, browse, groups, renewal, endings, waivers, supporting material and the admin chart) added new copies. A third deep-module review on 2026-10-27 found seven of them. Each slice below fixes one.

The review also found one live defect. The learner's quiz result page shows **Overdue** for a waived assignment that is past its due date. [Slice 22](22-assignment-status.md) fixes it.

Do the slices **in order, one at a time**, and make sure the suite is green before starting the next one. Slices 23 and 26 build on Slice 22, and Slice 28 builds on Slice 26. Slice 24 adds two PostgreSQL-only tests, so the skipped count rises from 3 to 5. The others don't depend on each other, but the order puts the most valuable ones first.

| Slice | File | Summary | Behaviour change |
|---|---|---|---|
| 22 | [22-assignment-status.md](22-assignment-status.md) | One assignment status (state, progress, deadline and labels) in Python and SQL | Yes: a waived assignment's quiz result shows no deadline status |
| 23 | [23-learner-review-access.md](23-learner-review-access.md) | `learner_access` decides whether a learner may review or act | No |
| 24 | [24-recipient-sources.md](24-recipient-sources.md) | One adapter per recipient source; one renewal eligibility rule | Yes: renewal review reports an employee who already has an outstanding assignment as a conflict |
| 25 | [25-ending-an-assignment.md](25-ending-an-assignment.md) | One way to end an assignment; one error-to-status mapping in the admin views | Only on paths the decorators block: a 400 or 500 becomes a 403 detail page |
| 26 | [26-dashboard-report.md](26-dashboard-report.md) | The dashboard report owns the chart modes; one quiz record per assignment | Only on tied attempt times: the report and the chart pick the same latest attempt |
| 27 | [27-question-type-presentation.md](27-question-type-presentation.md) | Each question type owns its editor and learner templates | No |
| 28 | [28-training-display-title.md](28-training-display-title.md) | One training display title for sorting and display | Small: a blank draft title sorts as "Untitled training", and the filter menu gets a defined order |

## Ground rules

The ground rules from [round 1](deepen-modules.md#ground-rules) and [round 2](deepen-modules-round-2.md#ground-rules) still apply:

- A slice may change behaviour only as its plan describes, and only after its **Decision needed** section is recorded. Add tests for the new behaviour.
- If an existing test fails, stop and report it. Don't edit it unless the slice plan names that test.
- Use glossary terms from [GLOSSARY.md](../GLOSSARY.md) in new names. Where a slice introduces a new term, add it to the glossary in the same slice.
- Respect the ADRs in [docs/adr/](../adr/). Slice 25 keeps [ADR-0004](../adr/0004-archive-assignment-without-deleting-evidence.md)'s rule that archiving cancels unfinished work in the same transaction. Slice 24 keeps [ADR-0001](../adr/0001-one-outstanding-assignment-per-training.md) and [ADR-0005](../adr/0005-renewal-interval-on-training.md).

## Test command

```bash
DATABASE_URL=sqlite:///:memory: DJANGO_DEBUG=1 DEV_AUTH_STUB=1 .venv/bin/python manage.py test training
```

The baseline on 2026-10-27 is **187 tests, OK (skipped=3)**. After each slice, the count should rise only by the tests you added.

## Final verification

1. Run the test command. It must pass with `skipped=5`, after Slice 24's two PostgreSQL-only tests.
2. With the same environment variables, run `.venv/bin/python manage.py makemigrations --check --dry-run`. It must print "No changes detected".
3. Check that these searches find no leftover copies. Each slice's **Done when** section has the full list.

   ```bash
   grep -n 'in {"canceled", "waived"}' training/*.py                                   # no matches (Slice 22)
   grep -n "report_progress\|report_deadline" training/*.py scripts/*.py               # no matches (Slice 22)
   grep -n "LearnerDenial.WAIVED" training/*_views.py                                  # no matches (Slice 23)
   grep -n "source ==\|source not in" training/assignments.py training/assignment_views.py  # no matches (Slice 24)
   grep -n "_record_cancellation" training/*.py                                        # no matches (Slice 25)
   grep -n "len(reason) > 1000" training/assignments.py                                # one match (Slice 25)
   grep -n "attempts\[-1\]\|max(attempts" training/*.py                                # quizzes.py only (Slice 26)
   grep -n 'question.type ==\|question.type !=' templates/training/quiz_editor.html templates/training/take_quiz.html  # no matches (Slice 27)
   grep -n "Coalesce" training/views.py                                                # no matches (Slice 28)
   ```

4. Repeat the smoke test from [round 1](deepen-modules.md#final-verification) against local PostgreSQL. Then also:
   - Waive an assignment that has a quiz attempt and is past its due date. Its quiz result page must not show **Overdue**.
   - Open the assignment dashboard in both chart modes, with and without filters.
   - Edit an attached quiz and a library quiz. Every question type must look as it did before Slice 27.
5. Run the PostgreSQL-only tests from Slice 24 against local PostgreSQL. SQLite skips them.
6. For each slice, report the test count and any departure from these plans, with the reason.

## Decisions

All decisions were recorded on 2026-10-27, in each slice file and below.

| Slice | Decision | Recommended |
|---|---|---|
| [22](22-assignment-status.md#decision-needed) | What the quiz result page shows for a waived assignment's deadline | **Recorded: "—"**, as every other page shows |
| [23](23-learner-review-access.md#decision-needed) | Whether completed assignments get review access only, like waived ones | **Recorded: no.** Completed work keeps today's responses; folding it in would change four responses and break `test_quizzes.py:620` |
| [24](24-recipient-sources.md#decision-needed) | Whether a renewal reports an employee who already has an outstanding assignment for the training | **Recorded: option B, report it as a conflict.** ADR-0005, US-15 and Slice 17 require this, but the code silently leaves the employee out today |
| [25](25-ending-an-assignment.md#decision-needed) | The status for `AssignmentAccessError` in the admin views; the 500s in archive and restore; whether completion shares the ending operation | **Recorded:** a 403 detail page everywhere; send archive and restore through the shared mapping; leave completion out |
| [28](28-training-display-title.md#decision-needed) | The filter menu's fallback title, and the sort order | **Recorded:** "Untitled training"; sort by the display title, case-insensitive |

Slices 26 and 27 need no decision.

The Slice 24 decision is the only one that changes what administrators can observe today. The decisions in Slices 25 and 28 cover paths that the view decorators or the publish rules already make unreachable.

## Review status

A deep-module review of `training/` on 2026-10-27 produced the candidates. Each slice plan was then written against the code, and every file and line reference was checked. Writing the plans corrected the review in several places:

- **Slice 22:** `my_training` has no visible defect. It works out **Overdue** for waived rows, but the template doesn't show it.
- **Slice 24:** there is no import cycle between `assignments.py` and `renewals.py`, and the two forms of the renewal rule don't disagree. Writing the plan found the missing renewal conflict report instead. Slice 17's conflict test and its PostgreSQL concurrency test were never added.
- **Slice 25:** the 400 vs 403 mismatch and the archive and restore 500s can't be reached over HTTP. `demo_admin_required` already rejects every actor that `is_administrator` would reject.
- **Slice 26:** a single dashboard function can't own the whole view. `test_admin_chart.py` patches `report_assignments` and `report_rows` in the view module, so the view keeps calling them.
- **Slice 28:** neither fallback title can be reached today. The real changes are the undefined filter-menu order and case-insensitive sorting.

These plans haven't had an independent review yet. Round 2 was reviewed by Codex.

## Local implementation status

Slices 22–28 were implemented in order on 2026-10-27. The SQLite test suite and migration check passed after each slice.

| Slice | Tests after slice | SQLite result |
| --- | ---: | --- |
| 22 | 191 | OK; 3 skipped |
| 23 | 195 | OK; 3 skipped |
| 24 | 205 | OK; 5 skipped |
| 25 | 214 | OK; 5 skipped |
| 26 | 220 | OK; 5 skipped |
| 27 | 226 | OK; 5 skipped |
| 28 | 229 | OK; 5 skipped |

The local PostgreSQL 18 service accepts connections from the user's terminal, but this sandbox denies both TCP and Unix-socket connections (`Operation not permitted`). The user ran the complete PostgreSQL suite after the test workers were changed to close their connections in `finally`: **229 tests passed, including the two Slice 24 concurrency tests, and Django destroyed the test database cleanly**. The 1280px editor comparison remains open. Slice 24 keeps the assignment page's `selected_source` choices; the literal `source ==` search still finds those two view branches, which this plan explicitly leaves in place. No model migration was needed.

## Out of scope

- The quiz editor UX improvements. They are listed in [Slice 27's follow-on](27-question-type-presentation.md#follow-on) for a later feature slice.
- The shared `AdministratorRequired` exception. It's low value while every route has the view decorator.
- Merging `admin_chart.py` and `quiz_outcomes_chart.py`. They scale and sort differently, so merging them would only move code.
