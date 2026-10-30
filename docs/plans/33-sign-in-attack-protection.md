# Plan 33: sign-in attack protection

Follows [plan 30](30-production-deployment.md) step D, which moved rate limits into the shared `RateLimitCounter` table. Step D left one problem: the per-email sign-in cap (20 per 15 minutes) lets anyone lock an employee out by sending wrong passwords for that email. This plan blocks the attacker instead of the employee.

Codex reviewed a first draft on October 30, 2026. Its corrections are folded in below.

## Owner decisions (October 30, 2026)

- Block the address that sends the attack, not the employee who is attacked.
- An address block expires after 24 hours. An administrator can lift it early or make it permanent.
- Every employee gets a trusted-device cookie, so an attack on an email can't lock that employee out of a browser they already use.
- Only administrators can get past an address block, so an attacker can't lock the whole office out and leave nobody able to lift the block.
- **Passkeys, not MAC addresses.** The owner asked for the administrator's MAC address. A web server can't read a client's MAC address: it doesn't cross routers, browsers don't expose it, and it can be copied. The owner chose a passkey (WebAuthn) instead. A passkey needs the device's PIN or biometric check. Some passkey providers (iCloud Keychain, Google Password Manager) sync a passkey to the user's other devices, so it isn't strictly tied to one machine. Accepting only device-bound passkeys would need attestation checks that common password managers fail, so synced passkeys are accepted.
- This is its own plan and PR, after the step D PR.

## Design

### A. Count failures, keep one attempt limit

- Before authentication, `limit_reached(scope, key)` reads a counter without changing it. After a failed password, `rate_limited(scope, key)` adds one. Only failures count toward `sign_in`, `sign_in_email`, `sign_in_device`, `trusted_email` and `address_failures`.
- `sign_in_client` (100 per 15 minutes per network) still counts every attempt before authentication. It bounds the attempts in flight at once.
- Parallel failures can pass a failure cap by the number of requests in flight. This is accepted.
- `_client_network(request)` takes the address from the existing `_client()`, which already handles trusted proxies and IPv4-mapped IPv6. It returns that address for IPv4 and the `/64` network for IPv6. A `/64` is a heuristic: a client with a larger allocation can move between networks.

### B. Trusted-device cookie (all employees)

- After a successful sign-in (password or passkey), the response sets `training_device`. It is a `TimestampSigner` value (its own salt) holding the employee ID, a random device ID (16 bytes) and a fingerprint: `salted_hmac` of the employee ID, password hash and `session_version`.
- On each use, the server checks the signature with `max_age` (`TRUSTED_DEVICE_DAYS`, 90). It also compares the fingerprint in constant time, and checks that the cookie's employee matches the email being tried. The cookie is `HttpOnly`, `Secure` outside debug, and `SameSite=Lax`.
- A changed password, a password reset or a deactivation changes the fingerprint, so every trusted-device cookie for that employee stops working. No device list is stored on the server.
- An attempt with a valid cookie for that email counts failures against two limits:
  - `sign_in_device`: 10 per 15 minutes, keyed on the device ID.
  - `trusted_email`: 30 per 15 minutes per email, across all trusted devices. This stops stolen cookies from adding up to unlimited guesses.

  It skips `sign_in` and `sign_in_email`.
- An attempt without a valid cookie counts failures against `sign_in` (network and email, 5) and `sign_in_email` (20), as today.
- Signing out keeps the cookie, because the cookie records that the browser was used before, not a session.

### C. Address blocks

- New `AddressBlock` model: `network`, `failure_count`, `created_at`, `expires_at` (null when permanent), `ended_at`, `ended_by` (null for an expiry or a command), `end_reason` (`lifted`, `expired`, `command`), `made_permanent_at` and `made_permanent_by`. A unique constraint on `network` where `ended_at` is null allows one open block per network.
- Each failed sign-in adds one to `address_failures` for the network: 50 per 24 hours. This happens inside a transaction that holds a PostgreSQL advisory lock for the network. When the count is over 50 and the network has no open, unexpired block, the request closes any expired block (`end_reason=expired`) and creates a new one that expires in 24 hours (`ADDRESS_BLOCK_HOURS`). Lifting a block takes the same lock, so lifting and creating can't interleave. A block missed after a crash is created by the next failure.
- A network with an open, unexpired block gets a 403 page on sign-in, registration and password reset: "Sign-in from your network is blocked. Contact an administrator." The page is the same for every email. Pages for a signed-in session, including the verification resend, aren't blocked, and neither is passkey sign-in.
- **Blocked addresses** page (`/accounts/blocks/`, linked from **Accounts**, for real administrators):
  - Open blocks show the network, start time, expiry or "Permanent", and failure count, with **Lift block** and **Make permanent** buttons (POST with CSRF).
  - The 20 most recently ended blocks show who ended them and why.
  - Lifting a block deletes every counter keyed on that network (`address_failures`, `sign_in_client`, `register`, `passkey_options`), so counting starts again from zero.
- `manage.py unblock_address <network>` does the same from the host. `manage.py unblock_address --list` lists open blocks.

### D. Passkeys (administrators only)

- Dependency: `webauthn` (py_webauthn, Duo Labs) `>=3.0.1,<4`. The browser side is a small `passkeys.js` that calls `navigator.credentials` with options from the server, with no bundler. Ceremony POSTs send the CSRF token.
- New `Passkey` model: `employee`, `credential_id` (unique), `public_key`, `sign_count`, `transports`, `name`, `created_at`, `last_used_at`. `Employee` gains `webauthn_user_handle`: 32 random bytes, created on first use, never the email.
- New `PasskeyChallenge` model: `challenge`, `purpose` (`register` or `authenticate`), `session_key`, `employee` (registration only), `expires_at` (5 minutes), `used_at`. Verification uses up the challenge with `UPDATE … SET used_at = now() WHERE used_at IS NULL AND expires_at > now()` and continues only if one row changed. A challenge from another session or purpose is refused.
- The relying party ID is the hostname of `APP_BASE_URL`, and the expected origin is its scheme, host and port. WebAuthn refuses an IP address as a relying party ID, so passkeys work locally only with `APP_BASE_URL=http://localhost:8000`. Update `.env.example` and the local development guide.
- **Adding a passkey** (on **Your account**, for active real administrators):
  - The administrator re-enters their password. This has its own limit, `reauthenticate` (5 failures per 15 minutes per employee). A success is stored in the session for 5 minutes.
  - Registration options require a discoverable credential (`resident_key=REQUIRED`) and user verification (`REQUIRED`). They name the stable user handle and list existing credentials in `exclude_credentials`.
  - Verification passes `require_user_verification=True`. The challenge must belong to this session and this employee, and the employee must still be an active real administrator.
  - The page lists the administrator's passkeys and warns when there is only one: "Add a second passkey in case you lose this device."
  - Removing a passkey also needs a recent password re-entry.
- **Signing in with a passkey:**
  - The sign-in page shows **Sign in with a passkey**. Options use `user_verification=REQUIRED` and no `allow_credentials`, so the browser offers discoverable credentials. `passkey_options` (300 per 15 minutes per network) limits how many challenges are issued.
  - Verification looks up the credential by ID, then requires the response's `userHandle` to equal its owner's `webauthn_user_handle`. It passes `require_user_verification=True`, and it updates `sign_count` and `last_used_at` under `select_for_update`.
  - The library accepts a count of 0 followed by 0, and it refuses an equal or lower count once either value is positive.
  - Failed verifications count against `passkey_failures` (10 per 15 minutes per credential), not per network, so office traffic can't use up an administrator's way in.
  - The employee must be an active real administrator. Then Django's `login()` and the existing real-session marking run, and a trusted-device cookie is set.
- A passkey stops working when its employee is deactivated or stops being an administrator, because every ceremony checks this.

### E. Security events

- New `SecurityEvent` model: `kind`, `employee`, `actor` (null for a command or an automatic event), `network`, `occurred_at`, `detail`. It records each block created, lifted, expired, made permanent or ended by a command, and each passkey added or removed. Events are kept until the data policy (plan 30 step H) sets a retention period.
- **Blocked addresses** shows the 50 most recent events.

### F. Reset-email cap per email

- Add `account_email_target`: 10 password-reset requests per hour for one email, from any address. The response stays the same whether or not the email exists. This limits floods to one recipient. It doesn't prevent mail-quota use across many recipients, and it lets someone delay one employee's reset for an hour; both are accepted.

### G. Settings

```python
ACCOUNT_RATE_LIMITS = {
    "sign_in": (5, 15 * 60), "sign_in_client": (100, 15 * 60), "sign_in_email": (20, 15 * 60),
    "sign_in_device": (10, 15 * 60), "trusted_email": (30, 15 * 60),
    "address_failures": (50, 24 * 60 * 60),
    "passkey_options": (300, 15 * 60), "passkey_failures": (10, 15 * 60), "reauthenticate": (5, 15 * 60),
    "register": (5, 60 * 60), "account_email": (3, 60 * 60), "account_email_target": (10, 60 * 60),
}
ADDRESS_BLOCK_HOURS = 24
TRUSTED_DEVICE_DAYS = 90
```

The longest window becomes 24 hours, so `prune_rate_limits()` keeps counters for a day.

## Administrator recovery

The deployment guide (plan 30 step G) gets a recovery section. On the host:

1. `manage.py unblock_address <network>` lifts a block and clears that network's counters.
2. `manage.py remove_passkeys --email <email>` removes a lost administrator's passkeys and records the event.
3. Password sign-in from an unblocked network, then add new passkeys.
4. If no administrator is usable, `manage.py create_administrator` refuses while one exists. Reactivating or recreating an administrator stays a database task, which the guide describes.

## Tests

- **Failures only:** a correct password after four wrong ones signs in. A correct password alone adds nothing to the failure counters.
- **Trusted device:**
  - After 20 failures for an email from other networks, the employee still signs in from a browser with a valid cookie.
  - The same browser without the cookie gets 429.
  - The cookie stops working after a password change, a reset, a deactivation, or 90 days (controllable time).
  - A cookie for one employee gives no exemption for another employee's email.
  - Two cookies each get their own device count, and they share `trusted_email`.
- **Address blocks:**
  - The 51st failure in a day, across several emails, creates one block. Parallel failures on PostgreSQL also create one block.
  - A block that wasn't created is created by the next failure.
  - A blocked network gets 403 on sign-in, registration and reset. Another network doesn't.
  - Blocks expire at 24 hours. Lifting a block clears that network's counters. A permanent block doesn't expire.
  - IPv6 addresses in one `/64` share a block, and IPv4-mapped addresses match their IPv4 block.
  - Only real administrators can use the page. `unblock_address` lifts a block and records an event.
- **Passkeys** (py_webauthn's test vectors or a fake authenticator):
  - Adding a passkey needs a recent password re-entry, and re-entry failures are limited.
  - Passkey sign-in works from a blocked network.
  - Passkey sign-in is refused for each of these: a user who isn't an administrator, a deactivated administrator, a mismatched `userHandle`, a reused challenge (including two at once on PostgreSQL), an expired challenge, another session's challenge, a wrong origin, and a sign count that goes backward.
  - Removing a passkey needs a re-entry and stops the passkey working.
- **Reset cap:** the 11th reset request in an hour for one email, from different networks, gets 429.
- **JavaScript:** `training/js_tests/passkeys.test.cjs` covers base64url conversion, the CSRF header, and the request flow against a stubbed `navigator.credentials`.

## Verification

- `./scripts/test-local.sh` and `npm test` pass.
- In Chrome at `http://localhost:8000`, use the DevTools **WebAuthn** panel with a virtual authenticator:
  - Add two passkeys as an administrator.
  - Block the local address by sending 51 wrong passwords across several emails.
  - Confirm that password sign-in gets 403 and passkey sign-in works.
  - Lift the block on **Blocked addresses**.
- Take screenshots of the sign-in page, **Your account** with passkeys, and **Blocked addresses** for the PR.

## Glossary

Add **Trusted device**, **Address block** and **Passkey** to `docs/GLOSSARY.md`.

## Risks and limits

- **Botnets:** an attacker with many networks stays under the address-failure count. The per-email cap still limits guessing, and a trusted device still lets the employee in. An employee on a new browser during such an attack waits for the window to pass.
- **Shared office address:** one infected machine can get the office network blocked for up to 24 hours. Employees can't sign in from the office until an administrator lifts the block with a passkey. This is intended.
- **Stolen cookie:** a stolen trusted-device cookie gives only the device and trusted-email limits for that email, not a sign-in. The password is still needed.
- **Fixed windows and in-flight requests:** the owner accepted that an attacker can reach up to twice a limit across a window boundary. Parallel requests can pass a failure cap by the number in flight.
- **Proxy trust:** network detection depends on `TRUSTED_PROXY_NETWORKS` and on `web` being reachable only through Caddy (plan 30 step E).

## Order

A → F → B → C → E → D, each as a commit on the `sign-in-attack-protection` branch, in one PR after the step D PR merges. Migration `0020` adds `AddressBlock`, `SecurityEvent`, `Passkey`, `PasskeyChallenge` and `Employee.webauthn_user_handle`.
