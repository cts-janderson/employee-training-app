# Plan 34: account administration wayfinding

Follows [plan 29](29-sign-in-skeleton.md) (applications and invitations) and [plan 33](33-sign-in-attack-protection.md) (address blocks and passkeys). Both built their pages, but the owner couldn't find them in the running app on October 30, 2026.

## Problem

- **Accounts** (`/accounts/`) and **Blocked addresses** (`/accounts/blocks/`) exist. Only a signed-in, active, *real* administrator sees the **Accounts** link (`templates/training/base.html`), and `account_admin_required` refuses everyone else.
- Locally, the usual way in is **Switch profile** as the demo administrator. Demo administrators can't decide real applications (plan 29), so the link is hidden and nothing says why.
- A fresh local database has no real administrator until someone runs `manage.py create_administrator`. Nothing in the app points to that command.
- **Blocked addresses** is reachable only from a button on **Accounts**. The sidebar doesn't mark it as current, and nothing shows that applications are waiting or that a network is blocked.
- A registration stays `unverified` until its verify link is opened. Locally that link is only in the `runserver` log (`Local verify link for …`), so a new applicant never appears in the queue unless someone reads the log.

## Design

### A. Sidebar for real administrators

- Keep **Accounts** in the **Manage** group. Mark it current on `account_admin` and `address_blocks`.
- Add a count badge to **Accounts** for applications waiting for a decision (`pending_applications()`). Show the open, unexpired block count on the **Blocked addresses** link instead. Hide each badge at zero. The badge text includes the noun for screen readers, for example `2 applications waiting`.
- Add **Blocked addresses**, with its count, as an indented link under **Accounts**, shown only while at least one block is open. The page stays linked from **Accounts** at all times.
- Compute the counts in one context processor, run only for real administrators, so other pages don't pay for the queries.

### B. Explain the split to demo administrators

- Show demo administrators an **Accounts** link too. It opens a short page, not a 403: "Real employee accounts are managed by real administrators. Sign out and sign in with an administrator email and password." In debug with the stub on, the page adds: "No real administrator exists yet. Create one with `manage.py create_administrator`." It adds this only when that is true.
- Demo administrators still can't see or decide applications. The page shows no names, counts or emails from the real population.

### C. Local first-run hint on sign-in

- When `demo_enabled` is true (debug, stub and loopback only) and no real administrator exists, the sign-in page shows the `create_administrator` command. Production never renders it, because `demo_enabled` is false there.

### D. Local account outbox (debug and stub only)

- New page `/dev/outbox/`, linked from the sidebar footer next to **Switch profile** while `demo_enabled` is true. It lists account emails (verify, setup, reset, existing-account notice) that this `runserver` process sent, newest first, with recipient, subject and the link.
- It reads `django.core.mail.outbox` from the in-memory backend. The list empties when `runserver` restarts, and it doesn't show notification emails, because the notification worker is a separate process. The **Notifications** pages already show those records.
- The view requires the stub and a loopback request, like the switcher, so it never runs with the Graph backend.

### E. Registrations waiting for email confirmation

Added after the owner couldn't find an unconfirmed registration, October 30, 2026.

- **Accounts** gets a **Waiting for email confirmation** section between **Applications** and **Waiting for setup**. It lists active, pending, real registrations whose email isn't confirmed, with the same row layout, and an **Email not sent** badge after a failed send.
- **Resend link** sends a new verify link. It has no approve or reject action: verification still comes before approval (plan 29). The resend counts against the same `account_email` limit as the applicant's own resend, so the two can't add up to more emails.
- Both resend buttons on **Accounts** now confirm with a one-time notice, for example "Confirmation link sent to …".

## Tests

- A real administrator sees **Accounts** with the application and block counts, and the link is current on both account pages.
- **Blocked addresses** appears in the sidebar only while a block is open, and disappears after the block is lifted or expires.
- Employees, pending applicants and anonymous visitors see neither link nor counts.
- A demo administrator's **Accounts** page shows the explanation, no real-population data, and the `create_administrator` hint only while no real administrator exists.
- The sign-in hint appears with the stub on and no real administrator, and not when one exists or the stub is off.
- An unconfirmed registration is listed under **Waiting for email confirmation** with no approve action, and isn't counted in the sidebar. An administrator's resend sends one verify link, and after confirmation the applicant moves to **Applications**. Resends share the applicant's limit. Demo administrators can't resend, and a confirmed applicant can't be sent one.
- `/dev/outbox/` is refused with the stub off, refuses a non-loopback request, and lists a verify link after a registration.

## Verification

- `./scripts/test-local.sh` passes.
- In Chrome at `http://localhost:8000`: as the demo administrator, open **Accounts** and read the explanation. Run `create_administrator`, sign in, register a second address, open its verify link from `/dev/outbox/`, and approve it from **Accounts**. Block the local address (51 wrong passwords), confirm the sidebar shows **Blocked addresses**, sign in with a passkey, and lift the block.
- Take screenshots of the sidebar with badges, the demo explanation page and the outbox for the PR.

## Order

A → B → C → D → E, each as a commit. A–C can join the `sign-in-attack-protection` PR if it is still open, because they make its pages reachable. D is optional and can be its own PR.

## Status

**Implemented October 30, 2026,** on the `sign-in-attack-protection` branch, in `training/context_processors.py`, `templates/training/base.html`, `training/account_views.py` (`demo_accounts`, `local_outbox` and the sign-in hint), `templates/training/demo_accounts.html` and `templates/training/local_outbox.html`.

- **Accounts** shows only the waiting-application count. The open-block count is on the **Blocked addresses** sidebar link, which appears only while a block is open. **Accounts** is current on **Blocked addresses** only while that link is hidden.
- The demo administrator's page is `/accounts/demo/` (`demo_accounts`). A real administrator who opens it is sent to **Accounts**.
- `/dev/outbox/` is always routed. Like the switcher, it refuses with 403 when the stub is off or the request isn't on loopback. It lists recipient, subject and link, not a send time, because the in-memory backend doesn't record one.
- `real_administrator_exists()` in `training/accounts.py` is now the one rule for both the hints and `create_administrator`.
- Below 50rem, an `employee-row`'s forms now sit in the text column. Before, **Approve** and **Reject** fell into the avatar column on phones.
- Tests: `training/test_account_wayfinding.py`.
