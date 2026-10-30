# Plan 35: registration removal and list previews

Follows [plan 34](34-account-admin-wayfinding.md), which listed registrations waiting for email confirmation on **Accounts**. The owner asked to remove those registrations, and to cap long lists with a link to the full list.

Codex reviewed a first draft on October 30, 2026. Its corrections are folded in below.

## Owner decisions (October 30, 2026)

- **Removing a registration:** an administrator can remove a registration that hasn't confirmed its email. Removing deletes the record and frees the email to register again. A security event records who removed it and when, without the applicant's email.
- **Expiry:** unconfirmed registrations are removed automatically 7 days after their most recent confirmation email.
- **Full lists:** the **Accounts** sections share one **All accounts** page with a status filter, not one page per section.
- **Scope of this pass:** **Accounts**, **Blocked addresses** and **Notifications**. Groups are left for later.
- **Email digest approved:** the removal event keeps a keyed digest of the email, so someone who knows an address can later check whether its registration was removed.
- **Passkeys stay with administrators:** `manage.py remove_passkeys` refuses an employee who isn't an administrator, because only administrators can have a passkey.

## When deleting is safe

- **Usually nothing refers to a registration.** Only approved employees can receive assignments or join groups (`assignable()` in [plan 31](31-real-employee-workflows.md)). Registration creates no notification, because account emails don't use that model.
- **One exception, now closed:** `manage.py remove_passkeys` recorded a security event for any real employee, even one with no passkeys. It now refuses anyone who isn't an administrator, so it can't attach an event to a registration.
- **Delete defensively:** catch `ProtectedError` for every delete. A manual removal is then refused with a clear message, and expiry skips that row and carries on. `Passkey` and `PasskeyChallenge` cascade, but a registration has neither.

## Design

### A. Remove and expire unconfirmed registrations

- **Which registrations are removable:** `removable_registrations()` returns real, active employees whose email isn't confirmed, whose `approval_status` is pending, and whose `approval_decided_at` and `approval_decided_by` are both null. It's the same set **Waiting for email confirmation** lists.
- **Locking:** every delete, manual or expiry, first takes the per-employee advisory lock that `send_account_email` holds while it sends (`_account_email_lock`). Then it takes the row lock (`select_for_update`), checks again that the registration is still removable (and, for expiry, still old enough), and deletes.
  - This serializes removal against a confirmation email being sent.
  - Confirming, approving and rejecting lock the same row, so removal serializes against them too.
  - A row deleted meanwhile is treated as already gone, not as an error.
- **`remove_registration(*, actor, employee_id)`:**
  - Requires a real account administrator.
  - A registration confirmed or decided a moment earlier is refused with "This registration has already been confirmed."
  - In the same transaction as the delete, it records a `registration_removed` security event:
    - `actor` is the administrator, and `employee` is null.
    - `detail` holds the removed record's ID, its registration date, and an email digest.
    - The digest is a keyed HMAC of the normalized email under `SECRET_KEY` (salt `training.registration_removed`), the same method as the rate-limit keys. Someone who knows an email can later check whether its registration was removed. The event itself never stores the email.
- **Remove button:**
  - Each row under **Waiting for email confirmation** gets a secondary **Remove** button, next to **Resend link**.
  - It opens an inline `<details>` confirmation ("Remove this registration? They can register again with the same email.") with a **Remove registration** button that posts `action=remove`. This uses the `<details>` pattern from the assignment detail page.
  - The next page view shows "Registration removed."
- **Effect on a signed-in applicant:**
  - **Next request:** the session has no link to the employee, so it simply becomes anonymous. Their trusted-device cookie and old verify link stop working, and rate-limit counters expire as usual.
  - **Requests in flight:** these must not crash. `PasswordChange` currently raises `DoesNotExist` when its employee vanishes, so it treats that as signed out. The `send_account_email` callback tolerates a missing employee, including in its failure handling.
- **Expiry clock:**
  - New field `Employee.verification_sent_at`. `send_account_email` sets it only for `verify` emails, before delivery, in the same transaction that raises `link_generation`.
  - **Definite failure:** both the clock and the link generation go back to their previous values, guarded on the issued generation, so a failed send doesn't restart the 7 days.
  - **Ambiguous failure:** both are kept, because the email may have arrived.
  - **Other emails:** reset, setup and existing-account emails don't touch the clock.
- **`expire_registrations(*, now)`:**
  - Removes removable registrations whose `Coalesce(verification_sent_at, created_at)` is at least 7 days before `now`. The cutoff is tested with a controllable clock at exactly 7 days.
  - Processes at most 100 rows per run.
  - Records `registration_expired` events with no actor and the same detail as manual removal.
  - `cleanup_training_imports` runs it each minute, next to `prune_rate_limits()`.
  - Setting: `UNCONFIRMED_REGISTRATION_DAYS = 7`.
- **Migration `0021`:**
  - Adds `verification_sent_at` and the two event kinds.
  - Sets `verification_sent_at` to the migration time for every registration that is unconfirmed at that point. Existing registrations therefore get a full 7 days after the upgrade, instead of being deleted on the first cleanup run.

### B. Accounts previews and the All accounts page

- **Accounts:**
  - Each waiting section (**Applications**, **Waiting for email confirmation**, **Waiting for setup**) shows its first 5 rows, oldest first, so the longest wait is on top. **Waiting for setup** changes from name order to creation order.
  - **Recent decisions** shows 10, newest first.
  - Each heading shows the total, counted before slicing, for example "23 waiting".
  - When there are more rows than shown, a **See all 23** link under the section opens the full list.
- **All accounts** (`/accounts/all/?status=applications|unconfirmed|setup|decided&q=…&page=…`):
  - Uses `browse()` and `_browse_controls.html` for search by name or email and 25 rows per page.
  - Filter links across the top switch the status and reset to page 1.
  - Pages use the `page.number` that `get_page()` returns, so removing the last row on the last page lands on the new last page.
- **Acting from All accounts:**
  - Row forms post to the same actions as **Accounts**.
  - Each form's action URL carries `?return_to=<the current All accounts URL>`. The existing `safe_return_to()` reads it from the query string, not the form, and accepts only an exact path match on `/accounts/all/` on this host.
  - After success it redirects there.
  - After an error it renders **All accounts** with the same status, search and page, and shows the error.
- **Queries:**
  - `pending_applications()` and `awaiting_verification()` become querysets so they can be paged.
  - "Verified" is expressed in SQL as `verified_email` not blank and `Lower(verified_email) = Lower(email)`, matching `Employee.email_is_verified`. Writes already lowercase both through `normalize_email()`.
  - SQL and Python lowercasing can differ only for unusual Unicode characters. A test covers mixed case and a non-ASCII address.
  - No index is needed at this table size.
  - The sidebar count uses `.count()`.
- **Templates:** a shared partial `_account_row.html` renders a row for any status, so **Accounts** and **All accounts** can't drift apart.

### C. Blocked addresses previews

- **Previews:** **Active blocks**, **Ended blocks** and **Security events** each show 10 rows, with the total in the heading and a **See all** link when there are more.
- **Slicing:** slices are taken in the view. `blocks_overview()` returns full querysets and loses its fixed slices (20 and 50), so `unblock_address --list` still lists every open block.
- **Full lists:** `/accounts/blocks/list/?show=active|ended|events` pages each list at 25 per page, ordered by time and then ID so the order is stable. Active blocks keep **Lift block** and **Make permanent**, using the same `return_to` handling as **All accounts**.

### D. Notifications paging

- **Paging:** the log pages through `browse()` before anything is evaluated.
  - The queryset and its prefetches stay lazy, and delivery guidance is built only for the current page's rows.
  - Ordering is `-created_at, -pk`, so equal times can't swap between pages.
  - The validated `status` and `kind` filters travel through `extra_params`, so page links keep them.
- **Counts:** the counts across the top stay computed on the unfiltered, population-scoped notifications.

## Preview sizes

| Page | Section | Preview | Order |
|---|---|---|---|
| Accounts | Applications, Waiting for email confirmation, Waiting for setup | 5 | Oldest first |
| Accounts | Recent decisions | 10 | Newest first |
| Blocked addresses | Active blocks, Ended blocks, Security events | 10 | Newest first |

The sizes live in one place (`PREVIEW_SIZES` in `training/browsing.py`), so a later page can reuse them.

## Not changed

- **Group membership checkboxes** stay on one page, because splitting a checkbox form across pages would lose selections. Large groups would need a search-to-add picker, which is separate work.
- **The Groups list** is left for a later pass.

## Tests

- **Removal:**
  - `remove_passkeys` refuses an employee who isn't an administrator, and records nothing.
  - An administrator removes an unconfirmed registration. The record is gone, the email can register again, and a `registration_removed` event names the administrator, holds the removed ID and the email digest, and doesn't contain the email.
  - Removal is refused for a confirmed, decided, approved, demo or inactive employee.
  - A registration with a protected reference (a `remove_passkeys` event) is refused cleanly.
  - Demo administrators and employees can't remove.
  - A signed-in applicant's next request is anonymous.
  - A password change in flight for a removed applicant doesn't crash.
- **Concurrency (PostgreSQL `TransactionTestCase`):**
  - Confirming and removing at the same time: exactly one wins.
  - Removing while a confirmation email is sending: removal waits for the send, and the send's callback doesn't crash.
- **Expiry (controllable clock):**
  - A registration is kept until just before 7 days and removed at exactly 7.
  - A resend on day 6 restarts the count. A definitely failed resend doesn't, and an ambiguous one does.
  - Reset and setup emails don't affect it.
  - Confirmed applicants and invited employees never expire.
  - A protected row is skipped while the rest are removed.
  - The migration gives existing registrations a full 7 days.
- **Accounts:**
  - With 7 applications, **Accounts** shows 5, oldest first, "7 waiting", and **See all 7**. With 5 there's no link.
  - **All accounts** filters by status and searches by email.
  - Removing the only row on page 2 returns to page 1.
  - An error keeps the filter and page.
  - A `return_to` for another path or host is ignored.
  - Demo administrators and employees can't open the page.
  - Verified-email matching covers mixed case and a non-ASCII address.
- **Blocked addresses:** previews cap at 10. The full lists page and keep the lift and make-permanent actions. `unblock_address --list` still shows all open blocks.
- **Notifications:** 30 notifications show 25 on page 1 and 5 on page 2. The filters survive paging, the counts still cover all 30, and equal timestamps keep a stable order.

## Verification

- `./scripts/test-local.sh` passes.
- **Screenshots** at desktop and phone width: **Accounts** with more than 5 rows in a section, **All accounts** filtered to Unconfirmed with the Remove confirmation open, **Blocked addresses** with **See all**, and page 2 of **Notifications**.

## Order

A → B → C → D, each as a commit, in one PR. Migration `0021` comes with A.

## Glossary

None of these are domain terms, so the glossary is unchanged. "Registration" stays the UI word for an applicant who hasn't confirmed their email.

## Status

Planned October 30, 2026, and reviewed by Codex. Not started.
