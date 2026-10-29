# Quiz pages layout

## Context

This visual pass follows the quiz editor UX follow-on in [Slice 27](27-question-type-presentation.md#follow-on), the [search and browse plan](15-search-and-browse.md), the [HCI review](../hci-review.md), and the existing [library design direction](../design/README.md). It keeps the existing all-questions-at-once learner form, question types, save behavior, and independent library quiz copies.

## Imagegen study

[Quiz pages concept](../design/quiz-pages-concept.png) explores three connected surfaces: the quiz library, quiz editor, and learner quiz. The study suggested aligned library facts, roomier answer controls, a visible question count, and a calmer result summary. Its topic filter, question-type selector, per-question pagination, and invented sample rows are visual exploration only.

Built-in imagegen prompt:

```text
Use case: ui-mockup
Asset type: design reference board for an existing Django employee training app
Primary request: Create one wide, high fidelity design board with three distinct desktop content-area concepts labeled exactly "Quiz library", "Edit quiz", and "Take quiz". The existing application uses a dark navy sidebar, white top bar, off-white canvas, blue primary controls, fine borders, and restrained green and amber readiness states. Quiz library: reusable quiz rows with a clear title, question count, passing threshold, readiness, edit action, and an attach-to-draft disclosure; search and pagination. Edit quiz: title and passing threshold, compact question outline, one clear question card with prompt and spacious answer option rows, quieter remove controls, question type addition close to the question list, save action. Take quiz: calm readable numbered questions, large selectable answers, progress by question count, submit action. Use synthetic text only, such as "Workplace safety basics". Preserve the practical semantic HTML form and list patterns of an internal app.
Style/medium: crisp contemporary enterprise product UI mockup, straight-on desktop screenshots, precise spacing and typographic hierarchy, no decorative imagery.
Composition/framing: three page concepts side by side in one wide board; focus visual detail on main content. All three visible and legible as concepts.
Constraints: no logos, photos, illustrations, fabricated analytics, new features, drag and drop, timers, answer keys, watermarks, or misleading state. The concept is visual guidance, not a code specification.
```

## Implemented

- The library shows a consistent quiz, question count, threshold, readiness, and action layout. The count uses the total matching quizzes across pages, and an empty search explains the absence of matches.
- The editor moves add-question controls next to the question heading, gives answer text more room, quiets remove-option controls, and explains the passing threshold. Its top save button submits the existing editor form.
- The learner quiz gives every question and choice a larger reading and click area. The result page emphasizes the recorded score and outcome while preserving submitted-answer review without answer keys.
- Quiz-specific layouts stack at narrow widths. Form names, validation anchors, and server-side grading remain as before.

## Follow-on

The remaining interaction ideas from [Slice 27](27-question-type-presentation.md#follow-on) are implemented and recorded in its [follow-on implementation](27-question-type-presentation.md#follow-on-implementation). This visual pass had already delivered roomier option rows, narrow layouts, nearby add-question controls, and the threshold explanation.

## Verification

- Django configuration check passed.
- Full `training` suite passed on the documented in-memory SQLite test configuration: 230 tests, 5 skipped.
- Focused library, question presentation, quiz, and browsing tests passed after the last template change: 39 tests.
- Local documentation links resolved, and `makemigrations --check --dry-run` reported no changes.
- A live browser preview could not run in this sandbox because binding the local Django server returned `Operation not permitted`.

## Guided creation roadmap: first delivery

Implemented locally on October 28, 2026. The user selected quiz improvements first, followed by the [document authoring redesign](document-training-model.md).

- Both quiz editors provide Setup, Questions, and Review anchors in one workspace.
- Review uses the existing domain publication/attachment checks against saved content. Issues link to fields without marking untouched fields invalid.
- Editing shows an explicit unsaved-review notice. Validation failures, removal review, cancellation, and conflicts retain submitted fields; conflicts withhold attachment/publication next actions.
- Ready library quizzes offer draft-specific copy review links. Attached quizzes lead to training publication review, which still validates the entire training.
- Preview describes saved content and records no answers or attempts. Entry descriptions distinguish reusable quizzes from training-specific copies.
- Existing endpoint contracts, four formats, grading, explicit saves, and revision locking are retained. No schema migration is needed.

Verification: 277 Django tests passed on SQLite with six database-specific tests skipped; eight Node event checks passed. Migration drift and static build checks passed. The preview server was started with a separate temporary synthetic database, but this session exposed no browser, so screenshots, visual layout, and keyboard review remain unverified. This delivery does not complete the document milestones or their feasibility gate.
