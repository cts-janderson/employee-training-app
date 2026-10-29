# ADR-0007: Store normalized training import reviews temporarily

- Status: Accepted
- Date: 2026-10-28

Import needs a reviewable conversion and safe retries without retaining uploaded files. Store the normalized review in a short-lived, owned database batch and commit it atomically with the new drafts so a retry returns the original result. A session-only plan would lose shared locking and durable replay; permanent attachment storage would retain source files beyond this workflow's purpose.

## Context

Administrators need to review text conversions before creating drafts and safely retry a submission. Uploads must not become permanent attachments or bypass publication review.

## Decision

Store only the normalized filename, proposed title, slide text, and warnings in an administrator-owned import batch. Review expires after one hour. Lock the batch and create all selected drafts and their result IDs in one database transaction. A committed batch returns its existing results on retry. Check owner and revision on every mutation. Raw uploads are parsed from bounded temporary streams and closed after parsing; no upload is stored in the application model.

Run `cleanup_training_imports` regularly: delete expired uncommitted reviews and committed batches more than seven days after commit. This deletes review records, never created trainings. The local loop runner is `cleanup_training_imports --loop`; deployment must schedule this command before enabling imports outside local demonstrations.

## Consequences

PostgreSQL row locks serialize concurrent commits. A short-lived normalized copy of draft content resides in the database and backups, subject to the existing real-data gate. Cleanup makes old URLs unavailable. Losing or expiring a review requires selecting files again; created drafts remain ordinary versioned authoring records.
