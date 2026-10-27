# Extended user stories: US-13 to US-18

## Context

[User stories](../user-stories.md) `US-13`–`US-18` are proposed additions beyond the confirmed MVP. Slices 13–20 are locally implemented. They do not change the MVP release gates. Three slices deepen the modules the stories would otherwise have to change in several places.

An architecture review of `training/` found three places where the new stories would spread the same rule across many modules:

1. **The dashboard's rules live in its view.** `assignment_dashboard` in [assignment_admin_views.py](../../training/assignment_admin_views.py) builds the authoritative assignment list inline. It filters by state and activity in SQL, then filters by progress and deadline in Python, scores the attempts, ranks the rows and sorts them. `US-13` needs the same rows for its export. `US-16` needs a count and pages, which Python-side filtering can't provide. `US-18` adds a new state. Today every one of those would edit the view or copy it.
2. **The meaning of "ended" is spread out.** Whether an assignment is still outstanding is decided by checking `completed_at`/`canceled_at` in `AssignmentQuerySet.outstanding()`, `in_state()`, the partial unique constraint, `assignment_state()`, three mutation guards in [assignments.py](../../training/assignments.py), `manual_nudge_problem`, `assignment_ended` and `_suppression_reason` in [notifications.py](../../training/notifications.py), and several templates. A waiver is a third way for an assignment to end, so `US-18` would edit every one of them.
3. **Assignment creation only accepts a hand-picked list.** `create_assignments` takes up to 100 explicit employee IDs and computes its idempotency fingerprint from them. `US-14` (groups) and `US-15` (renewal) both work out who the recipients are when the administrator submits. That breaks a fingerprint computed from IDs the browser posted. Both stories need one way to name *where* the recipients came from.

Do the slices **in order**, one at a time, with the suite green between them. A slice may depend on any earlier slice. The order puts the cheapest story first, as the [planning notes](../user-stories.md#planning-notes) suggest. The stories that need administrator decisions come later.

| Slice | File | Story | Depends on | Behaviour change |
|---|---|---|---|---|
| 13 | [13-assignment-report.md](13-assignment-report.md) | Deepening for `US-13`, `US-16` and `US-18` | — | No |
| 14 | [14-export-training-status.md](14-export-training-status.md) | `US-13` export | 13 | Yes: new CSV export |
| 15 | [15-search-and-browse.md](15-search-and-browse.md) | `US-16` search and paging | 13 | Yes: search, counts, pages |
| 16 | [16-group-assignment.md](16-group-assignment.md) | `US-14` groups | — | Yes: groups, group assignment |
| 17 | [17-renewal.md](17-renewal.md) | `US-15` renewal | 16 | Yes: renewal review and cycles |
| 18 | [18-assignment-endings.md](18-assignment-endings.md) | Deepening for `US-18` | — | No |
| 19 | [19-waivers.md](19-waivers.md) | `US-18` waivers | 13, 14, 18 | Yes: waived outcome |
| 20 | [20-supporting-material.md](20-supporting-material.md) | `US-17` supporting material | — | Yes: links on versions |

Slice 20 doesn't depend on any other slice, so it can be done at any point.

## Product decisions

These were decided on 2026-10-27, after the questions the [user stories](../user-stories.md#planning-notes) left open were worked through. Each slice records its decision and the rejected alternatives.

| Slice | Decision |
|---|---|
| 14 | The CSV file is the export's record. No export log is kept. |
| 16 | A group assignment has the same 100-recipient limit as individual selection. |
| 17 | A renewal interval in months is stored on the training, and administrators start each renewal ([ADR-0005](../adr/0005-renewal-interval-on-training.md)). |
| 19 | Any administrator may waive an assignment except their own, and a waiver is final ([ADR-0006](../adr/0006-final-waivers-by-any-other-administrator.md)). |
| 20 | Supporting material is `https` links only. |

## Ground rules

The [ground rules from round 1](deepen-modules.md#ground-rules) and [round 2](deepen-modules-round-2.md#ground-rules) still apply. These also apply:

- Slices 13 and 18 are pure refactors. If an existing test fails, stop and report it. Don't edit the test.
- Feature slices change only the behaviour their plan describes. Add tests named after observable behaviour, as the [testing guidelines](../../AGENTS.md#testing-guidelines) require. Use an injected `now` and the local email sink.
- Use [GLOSSARY.md](../GLOSSARY.md) terms. The new concepts (**Employee group**, **Renewal**, **Renewal date**, **Waiver**, **Supporting material**) are already in the glossary. Adjust a definition in its slice only if implementation sharpens it. Keep implementation details out of the definition.
- Respect the ADRs. Slices 16 and 17 keep [ADR-0001](../adr/0001-one-outstanding-assignment-per-training.md)'s one outstanding assignment, so a conflict is reported and never resolved automatically. Slice 19 keeps [ADR-0004](../adr/0004-archive-assignment-without-deleting-evidence.md)'s preserved evidence.
- Keep using synthetic employees (`is_demo=True`). These slices don't satisfy the [real-data gate](../implementation-plan.md#planning-assumptions-and-decisions).
- When a slice changes a visible page, include screenshots in its report, as the PR guidelines require.
- When a story is done, move it out of "Proposed future stories" in [user-stories.md](../user-stories.md) only if the product owner confirms it. Otherwise, note it as implemented beside its heading.

## Test command

```bash
DATABASE_URL=sqlite:///:memory: DJANGO_DEBUG=1 DEV_AUTH_STUB=1 .venv/bin/python manage.py test training
```

The baseline on 2026-10-27 is **137 tests, OK (skipped=3)**. After each slice, the count should rise only by the tests you added.

## Final verification

1. Run the test command. It must pass with the same number of skips.
2. Run `.venv/bin/python manage.py makemigrations --check --dry-run` with the same environment variables. It must print "No changes detected".
3. Check that these searches find nothing left over:

   ```bash
   grep -n "_sort_rank\|in_state(" training/assignment_admin_views.py                        # no matches (Slice 13)
   grep -n "completed_at or assignment.canceled_at\|completed_at and not assignment.canceled_at" training/*.py  # no matches (Slice 18)
   grep -n "canceled_at__isnull" training/*.py | grep -v models.py                           # no matches (Slice 18)
   ```

4. Against local PostgreSQL, run the [round 1 smoke test](deepen-modules.md#final-verification), then also:
   - Export the pending dashboard view. Open the CSV in a spreadsheet and check that a name starting with `=` shows as text.
   - Assign a training to a group of three employees, one of whom is inactive. You should see one outcome per employee.
   - Renew a completed assignment, and check that the learner sees the new cycle and the earlier completed cycle.
   - Waive a pending assignment that has a scheduled reminder. Its notification must show **Suppressed** with reason `assignment_waived`.
   - Open a supporting link from a completed assignment, using only the keyboard.
5. Run the PostgreSQL-only concurrency tests added by Slices 16, 17 and 19.
6. For each slice, report the test count and any place where you departed from its plan, with the reason.
