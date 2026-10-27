# Slice 20: supporting learning material (US-17)

Part of [Extended user stories](extended-stories.md). It doesn't depend on other slices.

## Story

[US-17](../user-stories.md#us-17--include-supporting-learning-material): authors add, edit, reorder and remove supporting links or documents on a training draft. Publishing fixes them for that version. Employees open them from their assignment, including after completion, and the same access rules apply as for the training itself.

## Problem

Supporting material is version content, like slides and the attached quiz. Three places define what a version contains, and each needs a change:

1. **Immutability.** `TrainingVersion`, `Slide`, `Quiz`, `Question` and `QuestionOption` each override `save()` and `delete()` with their own "is my version published?" query in [models.py](../../training/models.py). A new model would be a sixth copy.
2. **Revisions.** `create_revision` in [services.py](../../training/services.py) copies the slides inline and the quiz through `copy_quiz`. Material must be copied too, or a revision silently loses it.
3. **Publication.** `publication_problems` adds up title, slide and quiz problems. Material problems belong there too.

This slice adds material in all three places. It also deepens the first one, the cheapest: one shared guard instead of a sixth copied override.

## Decision

**Decision (2026-10-27): A.** Links only. No ADR, because uploads can be added later without undoing anything.

- **A. Links only (recommended first).** Each item is a name, an `https` URL and an optional description. No file storage, upload scanning or backups are needed. That fits the [deferred scope](../implementation-plan.md#deferred-scope) ("formatting tools and media"). Documents can be links to the organization's existing document store, such as SharePoint, which also enforces its own access control.
- **B. Links and uploaded documents.** This needs a `STORAGES["default"]` backend, size and type limits, a download view that checks access (never a public media URL), and backups for the files. Record the storage choice in a new ADR, and add backups to the [release criteria](../implementation-plan.md#mvp-release-criteria).

The rest of this plan follows **A**. If uploads are added later, add a `file` field alongside `url`, with a check constraint that exactly one of them is set. Serve files only through an access-checked view.

## Change

### 1. Glossary

Already added to [GLOSSARY.md](../GLOSSARY.md) on 2026-10-27. Adjust it only if implementation sharpens the definition:

> **Supporting material**:
> A named reference, such as a policy or example, that a training version offers for consultation alongside its slides; consulting it is not required for completion.
> _Avoid_: Attachment, resource, slide

### 2. One published-content guard

Add a small mixin in [models.py](../../training/models.py):

```python
class VersionContent(models.Model):
    """Rows that belong to a training version and freeze when it is published."""
    class Meta: abstract = True
    published_version_lookup: str  # e.g. "version", "quiz__version", "question__quiz__version"

    def save(self, *args, **kwargs): ...   # reject if the old or new parent version is published
    def delete(self, *args, **kwargs): ... # reject if the version is published
```

Move `Slide`, `Quiz`, `Question` and `QuestionOption` onto it. Each one only sets its lookup. Keep each model's current error message: either let the mixin take a message class attribute, or keep today's shared message if they already match. Read them first. `TrainingVersion` keeps its own guard, because it is the version. This is a refactor. All existing immutability tests must pass unchanged. Do it as its own commit before the material model.

The deletion test: deleting `VersionContent` would bring back five copies of the lookup. So it earns its place.

### 3. Model (one migration)

```python
class SupportingMaterial(VersionContent):
    published_version_lookup = "version"
    id = UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = ForeignKey(TrainingVersion, PROTECT, related_name="materials")
    position = PositiveIntegerField()
    name = CharField(max_length=200, blank=True)
    url = URLField(max_length=2000, blank=True)
    description = CharField(max_length=500, blank=True)
    # unique (version, position); position >= 1; ordering by position
```

`blank=True` lets a draft keep unfinished rows, the same way slides can be saved incomplete ([US-01](../user-stories.md#us-01--create-training)).

### 4. Authoring

In [services.py](../../training/services.py):

- Add `save_materials(*, version_id, expected_revision, rows, action)`. It mirrors `save_draft`: `_locked_draft` for the stale-draft check, the same add/remove/move-up/move-down actions, positions rewritten in one pass, and `version.revision += 1`. It raises `DraftInputError` with a `target=("material", index, field)` for structural errors such as a name over 200 characters.
- Add `_material_problems(rows)`, returning targeted `Problem`s:
  - "Material N: add a name."
  - "Material N: add a link."
  - "Material N: use an https:// link." Use a check that accepts only `https` with a host. It rejects `javascript:`, `data:` and plain `http`.

  `publication_problems` includes them, so the review page groups them under a **Supporting material** heading ([Slice 10](10-structured-draft-problems.md) targets).
- `create_revision` copies the materials after the slides, in position order.

UI: a **Supporting material** section on the draft editor, below the slides, with its own form and save button so it can't overwrite slide edits. It has a name field, a URL field and an optional description for each row, plus reorder and remove buttons that work without JavaScript, as the slide controls do. The preview page and the publication review list the materials.

### 5. Learner access

- **Assignment detail and slide pages:** add a **Supporting material** section listing `assignment.version.materials`. It's shown for any assignment that `learner_access` allows, including completed ones (and waived ones, once Slice 19 lands).
- Render each item as `<a href="{{ url }}" target="_blank" rel="noopener noreferrer">{{ name }}<span class="visually-hidden"> (opens in a new tab)</span></a>`, with the description in a `<p>` after it. The link text is the descriptive name, never the bare URL. That meets the keyboard and assistive-technology criterion.
- With links only, the app has no material URL of its own, so access works like this: only assignment pages that pass `learner_access` list the material, and only administrators see the editor and preview. Under option B, the download view calls `learner_access` (or checks for an administrator) itself.
- Because the material belongs to the published version, an assignment always shows the material of *its* version. Later drafts don't change it.

## Tests

`training/test_supporting_material.py`:

- `test_draft_material_can_be_added_reordered_edited_and_removed`.
- `test_incomplete_material_saves_but_blocks_publication_with_targeted_problems`.
- `test_only_https_links_are_publishable`, rejecting `javascript:alert(1)`, `data:` and `http://`.
- `test_published_material_is_immutable`, through the model guard.
- `test_revision_copies_material_and_later_edits_leave_published_version_unchanged`.
- `test_assigned_employee_sees_their_version_material_after_completion`.
- `test_other_employees_and_canceled_assignments_do_not_show_material`.
- `test_material_links_have_descriptive_names_and_new_tab_notice`.
- `test_stale_material_save_conflicts_like_slide_save`.

The existing immutability tests in `tests.py` must pass after step 2, before step 3 starts.

## Out of scope

- Uploaded documents (Decision B).
- Tracking whether an employee opened the material. That would be engagement data, which is deferred, and it isn't a completion requirement.
- Material on library quizzes.
