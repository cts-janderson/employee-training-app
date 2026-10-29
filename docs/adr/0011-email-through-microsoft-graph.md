# 0011. Email through Microsoft Graph

**Status:** Accepted  
**Date:** October 29, 2026

## Context

[ADR-0002](0002-application-stack.md) left the mail provider open and asked for an organization mail relay or another provider before live use. The organization uses Microsoft 365. [Production deployment](../plans/30-production-deployment.md) packages the application with Docker, so it can run on any host with outbound HTTPS. Account emails and notifications both need delivery. Notifications already distinguish retryable, permanent and ambiguous failures.

The options were:

1. **SMTP AUTH with a mailbox password.** Django's SMTP backend works unchanged. But Microsoft is disabling Basic SMTP AUTH by default for existing tenants from the end of December 2026, and Django's backend can't use OAuth.
2. **A Microsoft 365 relay connector.** This needs port 25 to the tenant's MX endpoint, plus a static IP address or a certificate for the connector. That ties the deployment to particular hosts.
3. **Microsoft Graph `sendMail` with the client-credentials flow.** An Entra app registration with the `Mail.Send` application permission, limited to one sender mailbox by an Exchange application access policy, needing only outbound HTTPS.

## Decision

Use option 3. `training/mail.py` gets a token with the client credentials, caches it until shortly before it expires, and posts plain-text messages to `/users/{sender}/sendMail`. A small Django email backend sends account emails through the same client, and the notification worker uses it through an adapter.

The client classifies every outcome:

- **Accepted (`202`):** sent. Graph's request ID is recorded.
- **Retryable:** the connection failed before the request was written, `429`, or `500`/`502`/`503`. These honor `Retry-After`.
- **Permanent:** `400`, `403`, `404`, a refused token, or `401` after one token refresh.
- **Ambiguous:** a request written with no complete answer, or `504`.

Graph's `sendMail` has no idempotency key, so a retry after an ambiguous outcome could send a second email. Ambiguous notifications go to administrator reconciliation instead of retrying.

## Consequences

- The application holds a client secret instead of a mailbox password. The secret expires and must be rotated; the deployment guide covers this.
- The access policy is required. Without it, `Mail.Send` as an application permission can send as any mailbox in the tenant.
- Production startup fails without `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID` and `GRAPH_CLIENT_SECRET`, unless another `EMAIL_BACKEND` is set explicitly. The image build sets a dummy backend.
- Acceptance by Graph isn't delivery to an inbox. Bounces arrive in the sender mailbox, not in the application.
- A failed account email doesn't fail the request. The employee is flagged on **Accounts** and the employee list, and the existing resend actions recover it.
