# Slice 24: recipient sources

Part of [Deepen the training modules: round 3](deepen-modules-round-3.md). **Record the decision below before you start.**

## Problem

[Slice 16](16-group-assignment.md) made the recipient source a seam, and [Slice 17](17-renewal.md) added its third adapter. But the three sources (an employee list, an employee group, and renewal) are not adapters in the code. Each one is a branch of `if recipients.source == ...`, and the branches are spread across two modules:

| Behaviour | Employee list | Employee group | Renewal |
|---|---|---|---|
| Fingerprint payload, `_fingerprint` ([assignments.py:130-139](../../training/assignments.py)) | `sorted(str(value) for value in ids)` | `"group", str(recipients.group_id)` | `"renewal", str(recipients.training_id), recipients.as_of.isoformat()` |
| Source check, `create_assignments` (293-294) | `if recipients.source not in {"employees", "group", "renewal"}` | same | same |
| Operation and recipient IDs, `create_assignments` (295-302) | `parse_assignment_request(operation_id, recipients.employee_ids)` | `uuid.UUID(str(operation_id))` | same as group |
| The same parse again, `_assignment_review` ([assignment_views.py:129-135](../../training/assignment_views.py)) | `parse_assignment_request(operation_id, recipients.employee_ids)` | `uuid.UUID(str(operation_id))` | same as group |
| Check before the transaction, `create_assignments` (306-307) | none | `EmployeeGroup.objects.filter(pk=recipients.group_id).exists()` | none |
| Check against the version, `create_assignments` (315-316) | none | none | `recipients.training_id != version.training_id` |
| Operation fields, `create_assignments` (319-321) | `"source": recipients.source, "group_id": recipients.group_id, "renewal_as_of": recipients.as_of` | same | same |
| Resolve, `resolve_recipients` (176-228) | 177-179 | 180-193: lock the group, "This group has no members.", "Choose a group with at most 100 members." | 194-227: lazy `from .renewals import renewal_candidates` (195) and `renewal_date` (208), then a locked re-check (210-223) |
| Review summary, `_assignment_review` (157) | `""` | `EmployeeGroup.objects.get(pk=recipients.group_id).name` | `""` |

The view that picks a source ([assignment_views.py:81-86](../../training/assignment_views.py)) is already an adapter choice: `Recipients.group(...)` or `Recipients.employees(...)`. The renewal page picks `Recipients.renewal(...)` at line 191. Both stay.

### The renewal rule is written twice

[renewals.py:36-47](../../training/renewals.py), used by review and creation:

```python
latest = {}
for assignment in assignments:
    if assignment.ending == Ending.CANCELED:
        continue
    latest[assignment.employee_id] = assignment
...
    if assignment.archived_at or assignment.ending != Ending.COMPLETED:
        continue
    due = renewal_date(assignment)
    if due and due <= as_of:
```

[assignments.py:216-219](../../training/assignments.py), the locked re-check during creation only:

```python
later = Assignment.objects.filter(
    employee=candidate.employee, training=training, assigned_at__gt=source.assigned_at,
).with_ending().exclude(ending_value=Ending.CANCELED).exists()
if not source.archived_at and due and due <= recipients.as_of and not later:
```

**The two forms are equivalent.** The re-check only runs on a candidate that the first form picked. That candidate is the employee's latest non-canceled assignment, ordered by `("assigned_at", "id")`. So at the time of the first read, no non-canceled assignment has a later `assigned_at`. `renewal_date` returns `None` unless the assignment's ending is `COMPLETED`, so `due and ...` repeats the completed check. Both forms skip canceled assignments, and both reject an archived completion. They give different answers only when the data changes between the two reads, which is exactly what the re-check is for. So this slice keeps one rule and runs it twice.

### Other findings

- There is no import cycle. [renewals.py](../../training/renewals.py) imports only `employees` and `models`. The two lazy imports in `resolve_recipients` can become a top-level import.
- The `IntegrityError` fallback (assignments.py:354-357) branches on `recipient.renews`, not on the source. It stays in `create_assignments`.
- [test_groups.py:8](../../training/test_groups.py) imports the private `_fingerprint`, and lines 60-62 call it both positionally and with `recipients=`. Its signature stays.
- No test creates an assignment between the renewal review and the submit, and no test checks the locked re-check.
- The `one_live_renewal_per_completion` constraint ([models.py:321-324](../../training/models.py)) tests the raw `canceled_at`. Changing it needs a migration, so it stays.

## Decision needed

[US-15](../user-stories.md#us-15--renew-required-training) says "Conflicts are reported for administrator action". [ADR-0005](../adr/0005-renewal-interval-on-training.md) says "[ADR-0001](../adr/0001-one-outstanding-assignment-per-training.md) conflicts are reported for administrator action". [Slice 17](17-renewal.md) says an employee "reassigned by hand, is reported as `CONFLICT` or `ALREADY_ASSIGNED`".

The code never does this. If an employee is assigned the training by hand after their completion reaches its renewal date, their latest non-canceled assignment is outstanding, so `renewal_candidates` skips them. The review doesn't list them, and the submit gives them no result. When they are the only candidate, the submit raises "No completed assignments are due for renewal on or before this date." This was checked against the code on 2026-10-27. Slice 17's own rule text ("no later assignment of the training exists") is the source of the gap.

- **A. Keep today's behaviour.** The slice is a pure refactor. The gap with US-15 stays, and is listed in the slice report.
- **B. Report the conflict (recommended).** It matches US-15, ADR-0005 and Slice 17's tests list. Change one function, `_due_for_renewal` (step 2). An employee is also a renewal recipient when all of these are true:
  - their latest non-canceled assignment is outstanding;
  - it renews nothing (`renews_id is None`), so it isn't a renewal already under way;
  - the non-canceled assignment just before it is a completion that isn't archived and has reached its renewal date.

  `renews` is that completion. `_plan_one` then reports `CONFLICT`, or `ALREADY_ASSIGNED` for the same obligation, as for every other source. These employees count towards the 100-recipient limit, and their presence stops "No completed assignments are due for renewal on or before this date." from being raised. The review shows them with today's conflict label.

B fits in this slice because the rule now lives in one place. Do steps 1-6 first, as a pure refactor with the suite green. Then do step 7.

**Decision: Option B**, recorded 2026-10-27. Renewal review reports an employee who already has an outstanding assignment for the training as `CONFLICT` or `ALREADY_ASSIGNED`. No assignment is created for them.

## Change

### 1. Recipient sources become adapters

In [assignments.py](../../training/assignments.py), `Recipients` becomes the base class. Each source is a frozen dataclass that owns its own rules:

```python
MAX_RECIPIENTS = 100


class Recipients:
    source = None  # an AssignmentOperation.Source value

    @classmethod
    def employees(cls, ids): return EmployeeListRecipients(tuple(ids))
    @classmethod
    def group(cls, group_id): ...      # today's parse and "Choose a valid employee group."
    @classmethod
    def renewal(cls, training_id, as_of): ...  # today's parse and "Choose a valid renewal training."

    def parsed(self): return self                          # EMPLOYEES: parse the IDs and check 1-100 distinct
    def fingerprint_parts(self): ...                        # the payload after actor, version and due date
    def check_before_version(self): pass                    # GROUP: the group exists
    def check_version(self, version): pass                  # RENEWAL: the version is of the renewed training
    def resolve(self, *, lock=False): ...                   # -> list[ResolvedRecipient]
    def operation_fields(self): ...                         # source, group_id, renewal_as_of
    def review_summary(self): ...                           # source, group_id, group_name


@dataclass(frozen=True)
class EmployeeListRecipients(Recipients):
    employee_ids: tuple
    source = "employees"

@dataclass(frozen=True)
class EmployeeGroupRecipients(Recipients):
    group_id: uuid.UUID
    source = "group"

@dataclass(frozen=True)
class RenewalRecipients(Recipients):
    training_id: uuid.UUID
    as_of: date
    source = "renewal"
```

- Keep the three factory methods and their messages. [test_groups.py](../../training/test_groups.py), [test_renewals.py](../../training/test_renewals.py) and both views call them.
- `fingerprint_parts` must hash to exactly today's values. Employee list: `[sorted(str(value) for value in self.employee_ids)]`. Group: `["group", str(self.group_id)]`. Renewal: `["renewal", str(self.training_id), self.as_of.isoformat()]`. Use the plain strings, as today.
- `_fingerprint(actor_id, version_id, due_date, employee_ids=None, *, recipients=None)` keeps its signature. Its body becomes `[str(actor_id), str(version_id), due_date.isoformat(), *recipients.fingerprint_parts()]`, with `recipients = recipients or Recipients.employees(employee_ids)`.
- `resolve` moves each branch of `resolve_recipients` into its adapter, unchanged, with every message character for character. Delete `resolve_recipients`. Its only other caller is `_assignment_review`.
- Use `MAX_RECIPIENTS` in place of the three `100`s in `parse_assignment_request` and the group and renewal `resolve`. The messages keep the literal "100".
- These are implementation names for existing glossary terms (employee group, renewal), so there is no new glossary term.

### 2. One renewal rule in `renewals.py`

In [renewals.py](../../training/renewals.py):

```python
def _due_for_renewal(assignments, *, as_of):
    """assignments: one training's, ordered by employee, assigned_at and id. Today's rule, lines 36-47."""

def renewal_candidates(training, *, as_of, employee_ids=None, lock=False):
    """With lock=True, the caller must already hold the employees' locks (pk order)."""
```

- Without `lock`, it behaves as today. `employee_ids` limits the read to those employees.
- With `lock=True`:
  1. apply `_due_for_renewal` to a fresh read;
  2. lock the picked completed assignments with today's query, `Assignment.objects.select_for_update().select_related("training").filter(pk__in=...).order_by("pk")`;
  3. apply `_due_for_renewal` to another fresh read, and keep a candidate only if it picks the same completed assignment.
- That replaces the `later` query in `resolve_recipients`. Delete it.
- `renewals.py` still imports nothing from `assignments.py`.

### 3. The renewal adapter

`RenewalRecipients.resolve(*, lock=False)` keeps today's steps and messages:

1. Load the training, or raise "Choose a valid renewal training.".
2. Call `renewal_candidates(training, as_of=self.as_of)`. Raise "No completed assignments are due for renewal on or before this date." if it's empty, and "Choose at most 100 renewal recipients." above `MAX_RECIPIENTS`.
3. Call `load_assignment_employees(..., lock=lock)`.
4. With `lock=True`, replace the candidates with `renewal_candidates(training, as_of=self.as_of, employee_ids=..., lock=True)`, passing the locked employees' IDs. Raise the "No completed assignments" message again if none are left.
5. Return the `ResolvedRecipient`s in the order of the employees, as lines 224-227 do today.

The lock order stays: employees in `pk` order, then the completed assignments, then the new assignments. Import `renewal_candidates` at the top of `assignments.py`.

### 4. `create_assignments` stops branching on the source

```python
def parse_recipients_request(operation_id, recipients):
    """Return (operation_uuid, parsed recipients). Raise today's messages in today's order."""
```

- It parses the operation ID first ("An assignment identifier is invalid."), then calls `recipients.parsed()`. For the employee list, that gives today's `parse_assignment_request` order. `parse_assignment_request` stays: `test_deep_modules_round_2.py` calls it. Build it from the same two private parsers.
- `create_assignments` keeps every step in today's order:
  1. the administrator check;
  2. the `version_id` parse;
  3. if `recipients is None`, `recipients = Recipients.employees(employee_ids or ())`, as today;
  4. if `recipients` isn't a `Recipients`, raise "Choose valid assignment recipients.";
  5. `parse_recipients_request`;
  6. the due date parse and `_fingerprint`;
  7. `recipients.check_before_version()`;
  8. inside the transaction, the version lookup and the published check;
  9. `recipients.check_version(version)`;
  10. `get_or_create` with `defaults={..., **recipients.operation_fields()}`;
  11. `validate_new_due_date`;
  12. `recipients.resolve(lock=True)`.
- The per-employee loop and its `IntegrityError` fallback don't change.

### 5. The review stops branching on the source

In [assignment_views.py](../../training/assignment_views.py), `_assignment_review`:

- calls `parse_recipients_request(operation_id, recipients)` in place of lines 129-135;
- then `validate_new_due_date`, then `recipients.resolve()`, as today;
- returns `**recipients.review_summary()` in place of the `source`, `group_id` and `group_name` keys. The values are today's: `"employees"`, `None`, `""` for the list; `"group"`, the group's ID and its name for a group; `"renewal"`, `None`, `""` for renewal.

The review still doesn't call `check_before_version`. A missing group is still reported by `resolve`, after the due date check, as today.

Drop `parse_assignment_request` and `resolve_recipients` from the view's imports. Leave `assign_training` lines 81-86 and `renew_training` line 191 alone.

### 6. Tidy up

- Delete the `source not in {...}` set and the `"Choose valid assignment recipients."` at the end of `resolve_recipients`. The guard in step 4 and the one in `assign_training` keep the message.
- Check that `grep -n "source ==" training/assignments.py training/assignment_views.py` finds nothing.

### 7. Report renewal conflicts

Change only `_due_for_renewal`, as the decision describes. The `renews` and `renewal_date` of a conflict recipient are those of the earlier completion. Nothing else changes: `_plan_one` already reports `CONFLICT` and `ALREADY_ASSIGNED`, and the review already labels them.

## Out of scope

- The `one_live_renewal_per_completion` constraint, and any other migration.
- The `select_for_update()` + `select_related("training")` in step 2 also locks the training row on PostgreSQL. Keep it.
- Making `_fingerprint` public, or changing [test_groups.py](../../training/test_groups.py)'s import of it.
- Moving "Choose a published version of this training." out of the views.
- Reporting candidates that the locked re-check drops. Today they get no result row. Keep it that way under both options.
- Recipient sources for any other page.

## Tests

Before you start, capture today's error precedence in tests. They must pass before and after the refactor:

- Group: a bad operation ID gives "An assignment identifier is invalid."; a missing group with a bad due date gives "Choose a valid due date."; a missing group with an unpublished version gives "Choose a valid employee group.".
- Renewal: a version of another training gives "Choose a version of the training being renewed.", even when the operation ID was already used.
- Review: a missing group with a past due date gives the due date message.

In [test_renewals.py](../../training/test_renewals.py):

- `test_group_and_renewal_fingerprints_match_stored_operations`: compare `_fingerprint(..., recipients=...)` with the SHA-256 of today's literal payloads. `test_groups.py` already covers the employee list.
- `test_renewal_rule`, one case per row:
  - the latest non-canceled assignment is a due completion: a candidate;
  - a canceled renewal: the completion is a candidate again;
  - a later waived assignment: no candidate;
  - the latest completion is archived, and an earlier one is due: no candidate;
  - a completed renewal that is due: a candidate, renewing the renewal;
  - a due completion followed by an outstanding assignment made by hand: a conflict recipient that renews the completion. With only this employee due, the submit reports `CONFLICT` and doesn't raise "No completed assignments are due for renewal on or before this date.".
- `test_locked_candidates_are_limited_to_the_given_employees`: `renewal_candidates(..., employee_ids=[alice.id], lock=True)` inside `atomic()` returns only Alice.
- `test_review_and_submit_agree_for_renewal`: the review rows and the `create_assignments` outcomes match, employee by employee.
- `test_renewal_submit_reads_assignments_made_after_review`: Alice and Bob are both due. Review lists both. Bob is then assigned by hand. The submit creates Alice's renewal and reports Bob as `CONFLICT`. The review, run again, lists Bob with the conflict label.
- `test_renewal_submit_skips_completion_archived_after_review`.
- `test_renewal_under_way_is_not_reported_as_conflict`. After a renewal is created, `renewal_candidates` is empty for that employee.

PostgreSQL only. Add `ConcurrentRenewalTests(TransactionTestCase)` with `@skipUnless(connection.vendor == "postgresql", "PostgreSQL row-lock behavior")`, like `ConcurrentNotificationIntentTests` in [test_notifications.py](../../training/test_notifications.py):

- `test_completion_archived_during_submission_is_not_renewed`. Thread A opens `atomic()`, calls `archive_assignment` on the completion, signals, waits half a second and commits. Thread B waits for the signal, then submits the renewal. B blocks on the completion's row lock, re-reads it, and raises "No completed assignments are due for renewal on or before this date.". No renewal is created.
- `test_concurrent_renewals_of_one_completion_create_one_assignment`. Slice 17 lists it, but it was never added. Two threads submit with different operation IDs. One creates the renewal. The other raises the "No completed assignments" message. There is still one assignment that renews the completion.

The skipped count rises from 3 to 5.

## Done when

- The test command passes. The count rises only by the tests you added, with `skipped=5`.
- `makemigrations --check --dry-run` prints "No changes detected".
- `grep -n "source ==\|source not in" training/assignments.py training/assignment_views.py` finds nothing.
- `grep -n "from .renewals import" training/assignments.py` finds one top-level import, and no indented one.
- `grep -n "assigned_at__gt" training/*.py` finds nothing outside tests.
- `grep -n "resolve_recipients\|parse_assignment_request" training/assignment_views.py` finds nothing.
- `grep -n "Choose valid assignment recipients" training/*.py` finds one line in `assignments.py` and one in `assignment_views.py`.
- `grep -n "from .assignments" training/renewals.py` finds nothing.
