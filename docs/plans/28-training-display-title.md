# Slice 28: training display title

Part of [Deepen the training modules: round 3](deepen-modules-round-3.md). Do [Slice 26](26-dashboard-report.md) first: both slices touch the dashboard's training labels. **Make the decisions below before you start.**

## Problem

Three places work out what to call a training, and each does it differently.

**The training list sorts by one rule and shows another.** `training_list` in [views.py](../../training/views.py) sorts in SQL, lines 47–53:

```python
latest_title = TrainingVersion.objects.filter(training_id=OuterRef("pk")).order_by("-version_number").values("title")[:1]
published_title = TrainingVersion.objects.filter(
    training_id=OuterRef("pk"), status=TrainingVersion.Status.PUBLISHED,
).order_by("-version_number").values("title")[:1]
trainings = Training.objects.annotate(display_sort_title=Coalesce(
    Subquery(published_title), Subquery(latest_title),
)).prefetch_related("versions").order_by("display_sort_title", "id")
```

It then picks the shown title again in Python, lines 60–70:

```python
published = [version for version in versions if version.status == TrainingVersion.Status.PUBLISHED]
latest = published[-1] if published else None
display = latest or draft
...
"display_title": (display.title if display and display.title else "Untitled training"),
```

The two rules pick the same version, because a training has only draft and published versions. They disagree on the title:
- A draft-only training with a blank title shows "Untitled training", but sorts as `""`, so it comes first in the list instead of among the U's.
- The sort uses the database's collation. SQLite compares bytes, so "Zeta" sorts before "alpha". PostgreSQL uses the database locale. The charts sort names with `casefold()`.

**The dashboard filter menu has its own rule and fallback.** `assignment_dashboard` in [assignment_admin_views.py](../../training/assignment_admin_views.py), lines 69–75:

```python
trainings = Training.objects.filter(assignments__isnull=False).distinct().prefetch_related("versions")
training_rows = []
for training in trainings:
    published = [version for version in training.versions.all() if version.status == "published"]
    label = published[-1].title if published else str(training.id)
```

- It falls back to the training's UUID, not "Untitled training".
- It has no `order_by`, and `Training` has no `Meta.ordering`. The menu order is whatever the database returns.

**The charts carry a third copy of the fallback.** `pending_load_chart` ([admin_chart.py](../../training/admin_chart.py) line 46) and `quiz_outcomes_chart` ([quiz_outcomes_chart.py](../../training/quiz_outcomes_chart.py) line 61) both use `training_labels.get(training_id, "Untitled training")`. The labels come from the dashboard's `training_rows` (line 77).

Today the dashboard fallbacks can't be reached. The menu lists only trainings with assignments. An assignment needs a published version ([assignments.py](../../training/assignments.py) line 313), and publishing needs a title (`_publication_problems`, [services.py](../../training/services.py) line 170). The fallbacks still disagree, and the next change that lists a draft would show it.

## Decision needed

**Decision: 1A and 2B**, recorded 2026-10-27. The filter menu falls back to "Untitled training", and trainings sort by display title, case-insensitive.

### 1. The filter menu's fallback

- **A. "Untitled training" (recommended).** One fallback everywhere. The menu would never show a UUID to an administrator.
- **B. Keep the UUID.** The menu keeps its own rule.

Neither changes what an administrator sees today, because the fallback is unreachable (see above). Option A only makes the rule the same.

### 2. Sort order

- **A. Sort by the display title as the database compares it.** A blank-titled draft moves from the top of the list to among the U's. Case order stays with the database collation.
- **B. Option A, case-insensitive (recommended).** `order_by(Lower("display_title"), "id")`. This matches the charts' `casefold()` for ASCII titles. SQLite's `lower()` folds ASCII only; PostgreSQL folds by locale. Note that in the slice report.
- **C. Keep today's sort expression next to the new display annotation.** Nothing visible changes, but the sort and the display stay two rules.

The filter menu uses the same order as the training list under A or B. Today its order is undefined, so a defined order is a visible change.

The existing test `test_training_library_filters_overlap_and_survive_search_and_pagination` in [test_browsing.py](../../training/test_browsing.py) expects "Draft only", "Published only", "Safety". That order holds under every option.

## Change

### 1. One display title

In [models.py](../../training/models.py), before `Training`:

```python
UNTITLED_TRAINING = "Untitled training"


class TrainingQuerySet(models.QuerySet):
    def with_display_title(self):
        """Annotate display_title: the newest published version's title, else the draft's, else UNTITLED_TRAINING."""
```

- Build it as `Coalesce(NullIf(Coalesce(Subquery(published_title), Subquery(draft_title)), Value("")), Value(UNTITLED_TRAINING))`.
  - The inner `Coalesce` picks the version the way `display = latest or draft` does.
  - The outer `NullIf` and fallback give "Untitled training" when that version's title is blank, as the Python does.
- `published_title` is today's subquery. `draft_title` filters `status=TrainingVersion.Status.DRAFT`; `one_draft_per_training` allows at most one.
- `TrainingVersion` is defined below `Training`. The method looks it up when it is called, so that is fine.
- Add `objects = TrainingQuerySet.as_manager()` to `Training`, following `AssignmentQuerySet` (models.py line 259). A manager adds no migration.

### 2. The training list uses it

In `training_list`:

- `Training.objects.with_display_title().prefetch_related("versions")`, ordered as decision 2 says, then by `"id"`.
- The row's `"display_title"` becomes `training.display_title`.
- Keep `draft`, `published`, `latest`, `display_description` and `draft_title` as they are. The template uses them.
- Remove the `OuterRef`, `Subquery` and `Coalesce` imports if nothing else in views.py uses them.

### 3. The dashboard menu uses it

Slice 26 may move this code. Apply the change where the dashboard builds its training labels after Slice 26 lands, and re-check these line numbers then.

- `Training.objects.filter(assignments__isnull=False).distinct().with_display_title()`, in the same order as the training list.
- `label` becomes `training.display_title`. Drop `prefetch_related("versions")`.
- Keep `"selected"`, the `labels` dict passed to both charts, and the "Unknown training" text for an unknown selected id (line 104). That is a different case: the selected id matches no listed training.

### 4. One fallback constant

- `pending_load_chart` and `quiz_outcomes_chart` import `UNTITLED_TRAINING` and use it in their `.get` fallback. Keep their signatures.
- `"Untitled training"` then appears in Python only in models.py.

### 5. Glossary

Add **Training display title** to [GLOSSARY.md](../GLOSSARY.md), after **Draft training version**: "The name administrators see for a training: the title of its newest published version, or of its draft before first publication." Keep the fallback text out of the definition.

## Out of scope

- The assign page heading, `"training_title"` in `assign_training` ([assignment_views.py](../../training/assignment_views.py) line 116). It is the selected published version's title, not the training's display title.
- Version titles in templates: `{{ version.title|default:"Untitled training" }}` in preview.html line 5, publication_review.html line 31 and list.html line 33, and `{{ draft.title|default:... }}` in library_quiz_list.html line 17. Each names one version.
- Search. `search_fields=("versions__title",)` still matches any version's title.
- Report and CSV columns that show `assignment.version.title` ([assignment_reports.py](../../training/assignment_reports.py)).
- The display description. It stays in Python.

## Tests

- `TrainingDisplayTitleTests`, calling `Training.objects.with_display_title()` directly:
  - published v1 and draft v2 with a different title: the published title;
  - two published versions: the newer one's title;
  - draft only: the draft's title;
  - draft only with a blank title: "Untitled training";
  - published v1 and a blank-titled draft v2: the published title.
- Training list:
  - every row's `display_title` equals that training's annotation;
  - a blank-titled draft sorts among the U's, not first (decision 2, A or B);
  - under B, "alpha" sorts before "Zeta".
- Dashboard:
  - the filter menu labels are the newest published titles, in the decided order;
  - the pending chart and the quiz outcomes chart show the same names as the menu.
- Keep [test_admin_chart.py](../../training/test_admin_chart.py), [test_quiz_outcomes_chart.py](../../training/test_quiz_outcomes_chart.py) and [test_browsing.py](../../training/test_browsing.py) unchanged.

## Done when

- The test command passes.
- `grep -n '"Untitled training"' training/*.py` matches only models.py and test files.
- `grep -n "display_sort_title\|Coalesce" training/views.py` finds no matches.
- `grep -n "str(training.id)" training/assignment_admin_views.py` matches only the `"selected"` comparison.
- `.venv/bin/python manage.py makemigrations --check --dry-run` prints "No changes detected".
