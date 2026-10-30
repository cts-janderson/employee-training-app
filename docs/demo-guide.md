# Demo guide

Use this guide with a local PostgreSQL database and the [local development setup](local-development.md). All people and addresses below are synthetic. Apply migrations, then run `seed_demo` as described there. The command creates six learners, two groups, two published trainings, an editable draft, a reusable library quiz, and eleven assignments. Due dates are relative to the day you first seed the database.

## Seeded scenarios

| Learner | Workplace Safety Essentials | Data Handling Basics |
| --- | --- | --- |
| Maya Chen | Not started; due in 14 days | Completed late |
| Leo Martin | One slide acknowledged | Completed on time; due for renewal |
| Priya Shah | Failed quiz; ready to retry | Waived |
| Omar Rivera | Passed quiz and completed | Canceled |
| Casey Brooks | Overdue and not started | Canceled and archived |
| Rowan Ellis | Pending while inactive | No assignment |

**Workplace Safety Essentials** has three slides, a supporting link, and four quiz questions, one of each supported format. **Data Handling Basics** is slide-only and has a six-month renewal interval. **Incident Response Update** is an editable draft. **Privacy refresher** is a reusable library quiz. The **Demo Operations** and **Demo New Starters** groups cover the learners.

## Walk through the workflows

1. Open <http://localhost:8000/> as the synthetic administrator. In **Trainings**, edit the Incident Response draft, save changes, and use **Review saved version for publication** before publishing. Published versions are immutable; a new revision is a separate draft.
2. Open **Quizzes** to inspect the library quiz. A complete, titled library quiz can be copied into a training draft with **Attach from quiz library**. The saved copy can then be edited independently.
3. Open a published training's **Assign** action. Select synthetic employees or a group, choose the version and a due date, and review the recipients and conflicts before submitting. Existing active, incomplete work for the same training remains a conflict; cancel it with a reason before assigning a replacement.
4. Open **Assignments** to filter, search, inspect an assignment's evidence, and export the filtered result. Try the pending-workload and quiz-outcomes charts. Review Priya's waiver and Casey's archived assignment. Restoring an archived canceled assignment reveals its history without reopening it. From **Trainings**, open **Review renewals** to find Leo's due renewal.
5. Use **Switch demo employee** to select Leo or Priya. In **My training**, resume Leo's next slide or retry Priya's failed Safety Essentials quiz. Opening a slide saves the resume position; explicit acknowledgment records progress. A passing quiz completes quiz-based training after every slide is acknowledged.
6. Return to the administrator and start the [local notification worker](local-development.md#notification-worker). Open **Notifications** to review queued and processed assignment notices, reminders, and manual nudges. A queued reminder is a request, with delivery outcome recorded separately. The worker never sends external email in this setup.

The [user stories](user-stories.md) and [implementation plan](implementation-plan.md) define the behavior and release checks behind these demonstrations.
