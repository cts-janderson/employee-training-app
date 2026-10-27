# Administrator assignment chart: implementation and acceptance plan

## Goal and scope

Give administrators a quick view of pending work by training on `/assignments/`, while retaining the assignment list for employee-specific follow-up. This supports [US-11](../user-stories.md#us-11--monitor-progress) and [HCI-06](../hci-implementation-plan.md#hci-06--find-follow-up-work-and-interpret-reminder-outcomes). The chart is an overview of the current **pending** report filters; completed, canceled, waived, archived, and all-state views continue to use the detailed list.

The chart uses four exclusive groups: overdue and not started, overdue and in progress, on time and not started, and on time and in progress. Bar length and the number at its end are the pending assignment count for that training. The page's results heading carries the filtered count; the chart omits a duplicate summary and source footer. [The design record and rendered preview](../design/admin-tracking-relay.md) specify the presentation.

## Current implementation

The first integration is already in the workspace:

- [Chart aggregation](../../training/admin_chart.py) groups the full filtered `report_assignments` queryset, including records beyond the current page. It renders up to eight training bars.
- [The dashboard view](../../training/assignment_admin_views.py) passes the chart only for a valid pending-state request. The same query supplies the paged list and CSV filters.
- [The dashboard template](../../templates/training/assignment_dashboard.html) includes the [plain-SVG chart](../../templates/training/_assignment_relay_chart.html) above the list. The chart has a values table for exact counts and uses [responsive styles](../../training/static/training/app.css).
- [Focused chart tests](../../training/test_admin_chart.py) cover category arithmetic, chart rendering, and records beyond page one. [The preview script](../../scripts/render-admin-chart-preview.py) uses only synthetic in-memory data.

This plan is for completing and validating that integration, rather than adding a second chart path.

## Implementation review

The pending chart now shows a filter-scoped empty state, rejects unexpected report groups, and has focused checks for the report/list/CSV contract, due-time equality, current published labels, ties, and the top-eight rule. The [HCI validation record](../hci-validation.md#administrator-assignment-chart-review-october-27-2026) links desktop and 320 CSS-pixel captures and records the passing SQLite suite. A human screen-reader review and PostgreSQL grouping-query measurement remain open because they could not be completed in this sandbox.

## Remaining work

### 1. Lock the report contract

- Confirm that every pending assignment selected by employee, training, activity, progress, deadline, and search filters belongs to exactly one of the four chart groups. Use the report's annotated progress and deadline values; do not maintain separate status rules for the chart.
- Define the empty result explicitly: for valid pending filters with zero matches, show a brief chart-area empty state. The nearby results heading names the current filter scope. For other assignment states, omit the pending chart without implying that zero records exist.
- Confirm that the training label follows the current published-version label used by the filter menu; keep full names in the values table when the SVG must shorten them.

**Acceptance:** The sum of all four group counts across all trainings equals the dashboard's filtered result count and the CSV row count for the same pending filters. The overdue count equals the sum of the two overdue groups. No archived or inactive record appears unless the corresponding filter explicitly includes it.

### 2. Finish the chart interaction and layout

- Keep the chart above the results and the detailed list below it. The chart is a summary; assignment changes and reminders remain available from the list and detail pages.
- Verify the title, color legend, numeric segment labels, numeric bar-end labels, and scale in the actual dashboard layout. Labels that cannot fit inside a narrow segment may be omitted there only when the exact value remains in the chart's values table.
- Review the top-eight rule with more than eight trainings. A short note below the chart must say how many trainings are shown, and the full result must remain reachable through the values table.
- Check desktop, 320 CSS-pixel width, and 400% zoom. Horizontal scrolling may contain the SVG, but it must not hide the filters, values table, or assignment actions.

**Acceptance:** Every shown bar's segment counts add to its end number. There are no clipped names or overlapping labels in the supported layouts. The chart has no decorative header, duplicate summary, or source footer.

### 3. Accessibility and data checks

- Test keyboard access to the values disclosure and the assignment results that follow it. Confirm focus order and a visible focus ring.
- Review the SVG title and description and the values table with a screen reader. The four groups and each training's exact count must be understandable without relying on color.
- Extend focused tests for combinations of report filters, zero records, ties, and the exact overdue boundary. Check that the chart and list use one clock value for the request.
- Measure the extra grouping query on a representative synthetic dataset under PostgreSQL. Keep rendering bounded at eight bars without excluding records from the aggregate counts. Add an index or change the query only if measurement shows a concrete problem.

**Acceptance:** A screen-reader user can recover every plotted value from the table. The chart remains administrator-only. The filtered chart counts agree with the list and export for tested combinations, including records beyond the first page.

### 4. Record the review

- Capture desktop and narrow-width screenshots using synthetic records and record any fixes in [HCI validation](../hci-validation.md).
- Run Django's system check, focused chart/report/administrator tests, and then the full training suite. Note the PostgreSQL-specific checks that cannot run in SQLite.
- Keep this visualization within the synthetic local workflow until the repository's real-data readiness gate is satisfied.

**Done when:** The data, interaction, accessibility, layout, and performance acceptance checks above pass; the preview and documentation match the live chart; and no assignment follow-up workflow is lost.
