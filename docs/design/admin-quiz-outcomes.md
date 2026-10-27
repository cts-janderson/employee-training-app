# Administrator quiz outcomes chart

## Purpose and scope

The Assignments page has two chart modes. **Pending workload** retains the existing relay chart. **Quiz outcomes** shows active, nonarchived quiz assignments across pending and completed states by default. The activity, employee, training, and search filters apply to both its chart and result list. The quiz mode deliberately does not apply the pending view's state, progress, or deadline filters: those would remove passed work from a pass/fail comparison.

Each assignment cycle is one observation. An employee with a completed cycle and a renewal can appear twice; the employee table displays the cycle and published version. Canceled, waived, archived, and slide-only assignments are outside the quiz chart. The empty state reports when no eligible quiz assignments match.

## Outcome rules

- **Passed:** at least one recorded quiz attempt passed.
- **Latest attempt failed · retry needed:** one or more attempts exist, none passed, and the latest attempt failed. This is a retryable state, not a final training failure.
- **Not attempted:** no quiz attempt exists yet.

The chart uses the saved `QuizAttempt.passed` value. It does not recompute results against a later training version or passing threshold. The three groups are exclusive and sum to each training's chart total. Percent labels use whole-number rounding that sums to 100; the table preserves exact assignment counts.

## Visual source and adaptation

The chart uses ruled rows and 100% stacked horizontal bars. A red hatch distinguishes retry needed without relying on color alone. Each row shows the retry count and assignment count, so equal percentages from different sample sizes do not appear equivalent. The chart omits presentation footers that repeat the page filters and totals.

The SVG plots at most eight trainings, sorted by retry count, then retry share, then training name. **View chart values** includes counts and employee rows for every matching training, including those outside the plot. The employee rows show published version, cycle, outcome, latest score and attempt time, and a link to the assignment. No answer keys or submitted answers appear in the chart.

## Verification

The chart and table are derived from the same filtered assignment query before pagination. Focused tests cover failed-then-passed attempts, no attempt, canceled and waived endings, inactive and archived records, slide-only training, a renewal cycle, search and training filters, records beyond page one, and the top-eight plot limit. The SVG is parsed as XML in a test, and the rendered page and disclosure were inspected in Chrome using synthetic demo data.
