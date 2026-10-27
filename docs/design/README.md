# Main destination designs

- [Administrator tracking chart](admin-tracking-relay.md): live pending-workload chart design, source notes, and SVG preview.
- [Administrator quiz outcomes](admin-quiz-outcomes.md): the chart mode for passed, retry-needed, and not-attempted quiz assignments, with employee-level values.
- [Training library concept](training-library-concept.png): imagegen layout study for aligned training rows with published and draft states shown together. The [implementation plan](../plans/training-library-layout.md) records the adapted layout and checks.
- [Quiz pages concept](quiz-pages-concept.png): imagegen study of the library, editor, and learner quiz. The [quiz pages layout plan](../plans/quiz-pages-layout.md) records the adapted changes and full prompt.

### Training library concept prompt

Built-in imagegen was used for the concept. The examples are synthetic, and the image is a design reference rather than an application screenshot.

```text
Use case: ui-mockup. Asset type: visual design concept for a Django employee-training admin library, desktop 1440px wide. Design a high-fidelity, realistic application screenshot for the Trainings page. Preserve the app's editorial visual language: dark navy/teal left navigation, warm off-white main background, white list surface, Georgia-like page heading, restrained teal actions, coral accent, muted green and amber status chips. Main content: page title 'Trainings', short description, one 'Create training' button; a compact search/filter toolbar; then a single aligned list/table with header columns 'Training', 'Versions', 'Status', 'Actions'. Show three representative rows: 'Workplace Safety' with 'Published v2' and 'Draft v3' chips together, primary action 'Edit draft' and secondary 'Assign'; 'Code of Conduct' with 'Published v1', primary action 'Start revision' and secondary 'Assign'; 'New Starter Guide' with 'Draft v1', primary action 'Edit draft'. Keep row heights equal, text alignment clean, generous whitespace, one prominent action per row, history as a quiet link. Add a small filter row 'All', 'Has draft', 'Published' above the list. Exact text when possible. No charts, decorative gradients, excessive cards, hover effects, or whimsical illustrations. Goal: clear visual hierarchy across mixed lifecycle states, polished and implementable with HTML/CSS.
```

## Route-wide redesign (October 2026)

- [Current-route concept](current-routes-concept.png): imagegen reconstruction of the seven navigation destinations from the templates and CSS. It is a concept board, not a browser screenshot; sample records and some fine details are illustrative.
- [Improved-route concept](improved-routes-concept.png): imagegen direction for the same destinations. The implementation follows its palette, page hierarchy, status treatment, and list rhythm while retaining the controls and data in the Django templates.

The seven destinations are `/my-training/`, `/trainings/`, `/quizzes/`, `/employees/`, `/groups/`, `/assignments/`, and `/notifications/`. The application defines 43 URL patterns in `training/urls.py`; record-specific URLs use UUID parameters rather than text slugs. Detail pages and form actions under those seven destinations share the redesigned shell and component styles. The Groups page was also brought into the same heading, form, and directory layout as the other destinations. These images were produced with the built-in imagegen tool and use synthetic example content.

### Current-route concept prompt

```text
Use case: ui-mockup
Asset type: current-state route and page inventory image for a Django employee training app
Primary request: Visualize the current interface architecture as a contact sheet of seven distinct desktop pages labeled /my-training/, /trainings/, /quizzes/, /employees/, /groups/, /assignments/, /notifications/. Show the shared deep navy sidebar, white top bar, gray canvas, blue buttons, white cards and list rows, and small status badges. Represent each page's existing content pattern: learner summary and next assignment; two searchable libraries; employee form and directory; group form and list; assignment filters and results; notification filter and log.
Constraints: no new features, no charts, no photos, no illustrations, no watermarks.
```

### Improved-route concept prompt

```text
Use case: ui-mockup
Asset type: improved visual design board for the same seven Django employee training routes
Input images: current-route concept as reference for route coverage and content patterns
Primary request: Design a cohesive desktop interface with a warm off-white canvas, ink-navy typography, deep teal navigation, restrained terracotta highlights for time-sensitive work, green success, and ivory cards with fine borders. Give My training a clear next action and progress. Make libraries, directories, Assignments, and Notifications easier to scan. Keep familiar semantic controls and the existing workflows.
Constraints: no new features, fabricated analytics, photos, illustrations, or watermarks.
```

These imagegen references guided the content areas of the six main navigation destinations. The layouts in the app use existing data and actions; illustrative entries and controls in the images are not application features.

- [Learning and library concepts](learning-library-concepts.png): My training, Trainings, Quizzes.
- [Management concepts](management-concepts.png): Employees, Assignments, Notifications.

## Imagegen prompts

Built-in imagegen was used for both references.

### Learning and library

```text
Use case: ui-mockup
Asset type: design reference board for a Django employee training web app, three desktop page content concepts in one wide image
Primary request: Show three polished desktop content-area designs labeled 'My training', 'Trainings', and 'Quizzes' side by side as separate page concepts. The existing app has a deep navy left sidebar and a simple white top bar; depict them minimally and focus visual design effort on the main content under the top bar. My training needs an inviting next-up assignment with due date, progress, clear Continue action, and a clean completed section. Trainings needs a library of draft and published training with distinct status, primary Create training action, and clear edit/assign links. Quizzes needs a reusable quiz library with question count, passing threshold, readiness status, and Create quiz action. Use realistic synthetic entries only.
Style/medium: high fidelity product UI mockup, clean contemporary enterprise software, intentional typography, restrained navy, blue, warm amber, soft neutral surfaces, generous but efficient spacing, subtle borders and depth.
Composition/framing: three distinct desktop screenshots in one board, each roughly 16:10, straight-on, readable hierarchy; show content areas prominently.
Constraints: no logos, no stock photos, no decorative illustrations, no charts implying invented data, no extra workflows, no watermarks. Keep controls feasible for semantic HTML/CSS implementation.
```

### Management

```text
Use case: ui-mockup
Asset type: design reference board for a Django employee training web app, three desktop page content concepts in one wide image
Primary request: Show three polished desktop content-area designs labeled 'Employees', 'Assignments', and 'Notifications' side by side as separate page concepts. The existing app has a deep navy left sidebar and a simple white top bar; depict them minimally and focus design effort on the main content under the top bar. Employees: synthetic employee directory with name, email, role, active status and a compact add employee form. Assignments: a clear filter area for state, employee activity, employee, training, progress and deadline; readable assignment results with due dates and status. Notifications: status filter and delivery log showing kind, employee and training, outcome, dates, attempt count. Use realistic synthetic entries only.
Style/medium: high fidelity product UI mockup, coherent with clean contemporary enterprise software, intentional typography, restrained navy, blue, warm amber, soft neutral surfaces, generous but efficient spacing, subtle borders and depth.
Composition/framing: three distinct desktop screenshots in one board, each roughly 16:10, straight-on, readable hierarchy; show content areas prominently.
Constraints: no logos, no stock photos, no decorative illustrations, no charts implying invented data, no extra workflows, no watermarks. Keep controls feasible for semantic HTML/CSS implementation.
```
