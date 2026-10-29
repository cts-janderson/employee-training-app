# 0009. Employee is the authentication user

**Status:** Accepted  
**Date:** October 29, 2026

## Context

The [email and password plan](../plans/email-password-authentication.md) asks for one authoritative source for login email, role and activity, while keeping every existing foreign key to `Employee`. `django.contrib.auth` isn't installed yet, so no auth tables or user references exist.

The options were:

1. **`Employee` as the custom user model.** `Employee` extends `AbstractBaseUser` and `AUTH_USER_MODEL` points at it.
2. **A separate account linked to `Employee`.** A one-to-one `Account` (or Django's default `User`) holds the password and links to the employee.
3. **Hand-written sessions.** Store password hashes on `Employee` and manage sessions without `contrib.auth`.

## Decision

Use option 1. `Employee` extends `AbstractBaseUser` with `USERNAME_FIELD = "email"`. It doesn't use `PermissionsMixin`; access is decided by the application's own role and account state.

Because `contrib.auth` was never installed, setting `AUTH_USER_MODEL` now needs no table swap. One migration adds `password` and `last_login`, and gives every existing employee an unusable password.

Email uniqueness stays the existing case-insensitive constraint. Django's check `auth.W004` recognizes `unique=True` or a field-based unique constraint, but not this expression constraint, so it's silenced, and a custom backend looks up the address with `iexact`.

## Consequences

- Email, role and activity have one home. Deactivation and password changes end existing sessions through Django's own session checks.
- Django's password hashing, validators, `login()` session rotation, password change and token generator are used as they are.
- The model can't later move to a separate account table without a data migration. A separate account would only help if one person needed several logins, which the plan rules out.
- Django's `createsuperuser` isn't supported. `manage.py create_administrator` creates the first real administrator instead.
