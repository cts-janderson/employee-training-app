# Training library layout

## Goal

Make draft-only, published-only, and published-with-draft trainings equally easy to scan. Keep the existing authoring, assignment, renewal, search, and pagination workflows.

## Design direction

[Training library concept](../design/training-library-concept.png) is an imagegen layout study. Use its aligned list, restrained colors, and consistent action positions. The implementation will combine version and status information in one column so the same fact is not repeated.

## Implementation

1. Add `All`, `Has draft`, and `Published` filters to the training list. Apply each filter before pagination, preserve it through search, page links, and editor return links. A training with a published version and a draft appears in both filtered views.
2. Give each row fixed desktop columns for training identity, version states, and actions. Show both the latest published version and the current draft when present.
3. Keep a published training's title stable while a revision is being edited. If the draft title differs, show it as secondary text. For a draft-only training, show its draft title.
4. Use one main action per row: `Edit draft` or `Start revision`. Keep assignment and renewal review as quieter links for published trainings, and place the published version list in a compact disclosure.
5. Stack the same information in reading order on small screens. Keep status meanings in text as well as color.

## Verification

- Check all three lifecycle combinations and a changed draft title.
- Check search, filter, pagination, and editor return links together.
- Check the page at desktop and narrow widths, then run Django configuration and focused tests.

## Result

Implemented in the training list view, template, and stylesheet. A rendered page with synthetic draft-only, published-only, and mixed rows was reviewed at desktop, intermediate, and narrow effective viewports. The full Django training suite passed: 180 tests, 3 skipped on SQLite. `manage.py check` reported no issues.
