# Responsive layout and visibility plan

## Goal

Make the interface predictable as the browser window changes size. A learner or administrator should always be able to find the same information and actions, and should be able to tell when a wide chart or table has more content to scroll into view. This plan covers layout and presentation only; the workflows and server-side rules in the [MVP](../mvp.md) stay the same.

## Current baseline

This is a source review, not a completed browser audit. The shared layout is in [base.html](../../templates/training/base.html), and most responsive rules are in [app.css](../../training/static/training/app.css). The stylesheet uses a grid for the sidebar and workspace, grids for records and forms, and wrapping flex containers for navigation and actions. It switches these layouts at several viewport widths: 600, 640, 760, 860, 900, 1100, 1200, and 1250 pixels.

The following are priority inspection points, not confirmed visual defects:

- The sidebar occupies about 15.5rem on desktop, while row layouts change according to the **whole viewport**. At a given viewport width, the space left for a training, quiz, assignment, or notification row is much narrower than the media query suggests.
- Training and quiz library rows, assignment results, notification rows, filters, and quiz option rows use minimum column widths or fixed action widths. Their content may be squeezed before the next breakpoint. The training library, assignment results, and quiz library also hide desktop column headings when rows reflow; each fact needs a clear label or self-contained presentation then.
- The training library subtitle uses a one-line clamp. The actor name uses an ellipsis at narrow widths. The notification row hides its secondary “Training email” text below 1250px. Check whether any of these conceal information users need, and provide an obvious way to see the full value where it matters.
- The chart and quiz outcomes table have deliberate horizontal scrolling. That is appropriate for dense data, but the scroll region needs a visible cue, keyboard reachability, and a way to obtain exact values without relying on the cropped view.
- Later CSS rules override earlier shared rules. In particular, the shared page heading and sidebar have later visual overrides after their first responsive rules. Audit the computed styles around each breakpoint before changing values.

## Layout rules to implement

1. Use **CSS Grid for page structure and aligned records**: shell, cards, form fields, and data rows. Give text tracks `minmax(0, 1fr)` and children `min-width: 0` where needed. Size columns from the actual content area. Switch a row to a simpler grid before titles, status, or actions collide.
2. Use **Flexbox for one-dimensional groups**: navigation links, action groups, badges, and compact metadata. Set explicit `gap`, wrapping, and sensible item growth. Keep the primary action in a predictable place within each row after wrapping. Do not rely on incidental flex wrapping to decide the reading order.
3. Preserve one DOM and keyboard order from wide to narrow views. Reflow to stacked sections in that order: identity or heading, key status and facts, then actions. Avoid visual reordering that makes tab order disagree with what users see.
4. Treat a breakpoint as the width at which **that component's content no longer fits**, including its longest realistic label and button. Prefer container queries for reusable rows inside the sidebar layout, or compute media-query thresholds from the available workspace width if container queries would add unnecessary complexity. Consolidate duplicate responsive overrides while touching those components.
5. Do not silently hide task information or actions at narrow widths. Wrap text, stack it, or show an explicit disclosure with a descriptive label. If a heading is removed from a card-style row, label the corresponding facts in the row. Truncation is acceptable only for secondary text with a visible full-value path.
6. Keep ordinary pages free of horizontal scrolling at 320 CSS pixels and at 400% browser zoom. Constrain horizontal scrolling to actual wide data, such as the relay chart and values table. Label each scroll region, show that more content exists, and ensure keyboard users can reach and operate it. Do not use `overflow: hidden` on a parent to mask a layout failure.
7. Let height change naturally. A short or narrow window must keep navigation, form controls, validation, and final actions reachable by vertical scrolling, with no overlap from sticky or fixed elements.

## Delivery sequence

### 1. Record a reproducible baseline

- Use synthetic administrator and learner data to capture the shared shell and representative pages: My training, training library, quiz library and editor, assignment dashboard and results, notification log, employee/group directory, and learner slide/quiz pages.
- At 320, 375, 640, 768, 1024, 1280, and 1440 CSS pixels, record screenshots and a short issue list. Resize slowly across each existing breakpoint and test a short window height. For each issue, note the element, viewport and content width, whether it moved, wrapped, clipped, disappeared, or requires scrolling, and whether its destination remains obvious.
- Inventory every CSS `display: none`, line clamp, ellipsis, `white-space: nowrap`, fixed/minimum width, and `overflow: hidden` affecting content. Classify each as intentional decoration, a disclosed data scroller, or a visibility problem. Keep this inventory with the implementation review.

**Done when:** each reported movement or apparent disappearance has a reproducible example and a proposed layout or visibility treatment.

### 2. Stabilize the shared shell and controls

- Adjust the sidebar/workspace grid, top bar, page and section headings, navigation, `.actions`, and `.browse-controls` in [app.css](../../training/static/training/app.css). Choose a narrow layout based on usable workspace width; let navigation wrap or become an explicitly accessible menu if wrapping becomes too tall.
- Make headings and action areas stack intentionally. Keep search, pagination, create/save actions, and the current page indication visible and in reading order. Check the demo actor and long names without concealing identity that is needed for the task.
- Keep focus indicators and the skip link unobscured after reflow.

**Done when:** no shared control overlaps, clips, or unexpectedly changes position across the tested widths and heights; every primary action remains visible without horizontal page scrolling.

### 3. Rework dense record grids and forms

- Revisit the training, quiz, assignment, employee/group, and notification row layouts in [app.css](../../training/static/training/app.css) and their templates. Set a deliberate wide, intermediate, and stacked arrangement based on the content each row carries. Put status and actions in stable locations, and use in-row labels when column headings disappear.
- Rework the assignment and notification filters, employee/group forms, quiz settings, and question option rows so fields grow within their columns and stack before labels or buttons collide. Preserve visible validation messages next to their fields.
- Replace unexplained truncation of meaningful titles, names, statuses, dates, and action labels with wrapping or an explicit full-text disclosure. Keep optional descriptions compact only if their full text remains available.

**Done when:** every visible row fact and action can be found at each supported width, with no overlap, clipped text, or ambiguous unlabeled values.

### 4. Make wide data and final actions clear

- Review the assignment relay chart, quiz outcomes values table, and any other tabular content. Keep scoped horizontal scrolling only where reflow would destroy the data relationship. Add a visible scroll cue and accessible region name; verify exact values remain available in text or table form.
- Check learner slides, quiz questions, answer controls, and submit/result actions at narrow widths and short heights. Keep the action sequence and form state clear while the page grows vertically.

**Done when:** users can distinguish a cropped data view from missing data, and can complete the learner and administrator flows without losing an action to window size.

## Acceptance and verification

- **Visibility:** For each representative page and state, the same information and actions are available at 320 CSS pixels, desktop width, and 400% zoom. Any intentional disclosure or horizontal scroller has a visible label or cue. No task-critical text depends on hover to reveal its full value.
- **Reflow:** The document has no horizontal page scroll at 320 CSS pixels; only named data regions may scroll horizontally. There is no overlap, clipped focus outline, inaccessible off-screen control, or content hidden by a short window height.
- **Stability:** Resize continuously across the existing breakpoint boundaries. Items may change from columns to stacked sections, but their order and grouping remain understandable. Tab order follows visual and reading order.
- **Content stress:** Repeat the check with long synthetic training titles, employee names, validation messages, multiple badges, two or more actions, empty states, and populated charts. Check both administrator and learner roles.
- **Functional regression:** Run `manage.py check` and focused Django workflow tests for the templates touched. Add a layout or rendering assertion only when it protects a real regression; use browser screenshots and manual keyboard/zoom checks for visual behavior. Record before/after captures and unresolved findings in [HCI validation](../hci-validation.md).

This work supports the responsive and accessibility checks already identified in the [HCI review](../hci-review.md). It does not change the [MVP release gates](../implementation-plan.md#mvp-release-criteria).

## Implementation status

The layout and visibility changes are implemented in the shared stylesheet and affected templates. The latest request prioritizes desktop behavior, so the existing compact navigation remains in place. Component layouts now respond to the width left after the desktop sidebar, and content, status, and actions retain a clear order as rows reflow. The [validation record](../hci-validation.md#responsive-layout-pass-october-27-2026) lists the browser review, automated checks, and remaining human checks.
