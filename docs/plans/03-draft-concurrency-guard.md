# Slice 3: shared draft concurrency guard

Part of [Deepen the training modules](deepen-modules.md).

## Problem

Three functions repeat the same steps: lock the training version, require draft status, then require the expected revision.

- `save_draft` in [services.py](../../training/services.py)
- `attach_library_quiz` in [services.py](../../training/services.py)
- `save_quiz_draft` in [quiz_authoring.py](../../training/quiz_authoring.py)

## Change

Add `_locked_draft(version_id, expected_revision, *, stale_message)` to [services.py](../../training/services.py). Call it inside the existing `transaction.atomic()` block. It:

1. runs `TrainingVersion.objects.select_for_update().get(pk=version_id)`;
2. raises `DraftConflict("This version has already been published.")` if the status isn't draft;
3. raises `DraftConflict(stale_message)` if the revision doesn't match;
4. returns the version.

Keep that order: status check first, then revision.

| Caller | `stale_message` |
|---|---|
| `save_draft` | `"A newer draft was saved. Reload before editing again."` |
| `save_quiz_draft` | `"A newer draft was saved. Reload before editing again."` |
| `attach_library_quiz` | `"The training draft changed after attachment review."` |

[quiz_authoring.py](../../training/quiz_authoring.py) already imports from `.services`. Import `_locked_draft` the same way.

## Out of scope

- `publish_saved_draft`. It checks the revision first and treats an already published version as success.
- Revision bumping. Leave it inline in each function.

## Tests

None needed. The existing conflict tests in [tests.py](../../training/tests.py), [test_quizzes.py](../../training/test_quizzes.py) and [test_library_quizzes.py](../../training/test_library_quizzes.py) cover this.
