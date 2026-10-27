# Administrator tracking chart

## Design requirements

- Help an administrator see which training has the largest pending workload and whether that work is overdue, started, or untouched.
- Count assignments from the same filtered report as the dashboard and CSV export. The chart includes every matching record, including records beyond the current page.
- Use four mutually exclusive categories: overdue/not started, overdue/in progress, on time/not started, and on time/in progress. Their sum must equal the bar's total.
- Sort trainings by pending assignment count, then overdue count, then name. Use neutral styling for every row and show its count at the bar end.
- Use the four blues as the category key. Place only the assignment count inside each segment when it fits; keep exact counts for small segments in the values table.
- Preserve the detailed assignment list for employee-specific follow-up and actions. Show chart values in an accessible table as well as the SVG.
- Show at most eight trainings in the SVG to keep labels readable; state when more are available. Retain filters and the complete list for the rest.
- Scale to the dashboard content area and allow horizontal scrolling at narrow widths without compressing labels to unreadable sizes.
- Keep the title, legend, bars, and axis in the chart. The assignment list already shows the filtered result count, so the chart needs no separate summary or source footer.

## Style and chart choice

The repository has no existing chart or charting library. The chart uses a white board and four blue segments. Its title and legend sit above the bars without a decorative banner. The page uses Albert Sans from Google Fonts when available and a sans-serif fallback.

The [Style Ranking prompt](https://www.emergentmind.com/chart-prompts/style-ranking) is suited to one value per item, while this dashboard needs to show four parts of each training's pending workload. The [Completion Funnel prompt](https://www.emergentmind.com/chart-prompts/completion-funnel) suggests sequential drop-off; these assignment categories are a snapshot of distinct records, so a funnel would imply movement that the data does not establish. This is a design inference from the app's assignment model and the chart descriptions, not an empirical comparison of chart performance.

## Rendered preview

[Open the SVG preview](admin-tracking-relay-preview.svg). It uses 54 synthetic assignments across eight training titles. The live `/assignments/` chart is generated from the current filtered synthetic records and will differ from this preview.

Regenerate it with `.venv/bin/python scripts/render-admin-chart-preview.py` from the project root. The script uses an in-memory SQLite database and synthetic records.

The SVG preview uses the current chart template and synthetic records. A live local dashboard view was checked in Chrome after the clutter cleanup: the title, legend, bar segments, value labels, and gridlines aligned. The preview's segment counts add to each bar total.

## Dashboard review

The [desktop dashboard capture](admin-assignment-chart-desktop.png) and [320 CSS-pixel capture](admin-assignment-chart-320.png) show the earlier chart in the assignment report with 56 synthetic pending assignments across nine trainings. The current chart displays the top eight and places a short scope note below it only when more trainings match. These captures predate the clutter cleanup and came from a Django-rendered static dashboard snapshot.
