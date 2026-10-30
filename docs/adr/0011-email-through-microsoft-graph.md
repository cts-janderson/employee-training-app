# 0011. Email through Microsoft Graph

**Status:** Accepted  
**Date:** October 29, 2026

## Context

[ADR-0002](0002-application-stack.md) left the mail provider open and asked for an organization mail relay or another provider before live use. The organization uses Microsoft 365. [Production deployment](../plans/30-production-deployment.md) packages the application with Docker, so it can run on any host with outbound HTTPS. Account emails and notifications both need delivery. Notifications already distinguish retryable, permanent and ambiguous failures.

The options were:

1. **SMTP AUTH with a mailbox password.** Django's SMTP backend works unchanged. But Microsoft is disabling Basic SMTP AUTH by default for existing tenants from the end of December 2026, and Django's backend can't use OAuth.
2. **A Microsoft 365 relay connector.** This needs port 25 to the tenant's MX endpoint, plus a static IP address or a certificate for the connector. That ties the deployment to particular hosts.
3. **Microsoft Graph `sendMail` with the client-credentials flow.** An Entra app registration given `Application Mail.Send` through Exchange RBAC for Applications, scoped to one sender mailbox, needing only outbound HTTPS.

## Decision

Use option 3. `training/mail.py` gets a token with the client credentials, caches it until shortly before it expires, and posts plain-text messages to `/users/{sender}/sendMail`. A small Django email backend sends account emails through the same client, and the notification worker uses it through an adapter.

The client classifies every outcome:

- **Accepted (`202`):** sent. Graph's request ID is recorded.
- **Retryable:** the connection failed before the request was written, `429`, or `503`. These honor `Retry-After` in seconds or as an HTTP date, up to a day.
- **Permanent:** `400`, `403`, `404`, a refused token, or `401` after one token refresh.
- **Ambiguous:** a request written with no complete answer, `500`, `502` or `504`. Exchange may have accepted the message before those errors.

Graph's `sendMail` has no idempotency key, so a retry after an ambiguous outcome could send a second email. Ambiguous notifications go to administrator reconciliation instead of retrying.

## Consequences

- The application holds a client secret instead of a mailbox password. The secret expires and must be rotated; the deployment guide covers this.
- The mailbox scope is required, and the app must not also hold an Entra-level `Mail.Send` grant: that grant applies tenant-wide and would let it send as any mailbox. Set it up with [RBAC for Applications](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac), and confirm with a test send that another mailbox is refused.
- With debug off, the email backend must be Graph, and startup fails without `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID` and `GRAPH_CLIENT_SECRET`. The image build passes placeholder values, because `collectstatic` never sends mail.
- Acceptance by Graph isn't delivery to an inbox. Bounces arrive in the sender mailbox, not in the application.
- A failed account email doesn't fail the request. The employee is flagged on **Accounts** and the employee list, and the existing resend actions recover it.
- Account emails for one employee are serialized with a PostgreSQL advisory lock. Each new link is committed before its email leaves, so a delivered link survives a crash. A definite failure restores the earlier link; an ambiguous or unknown one keeps the new link. The trade-off: a crash between committing and sending supersedes the earlier link without delivering a new one, and the employee requests another.
