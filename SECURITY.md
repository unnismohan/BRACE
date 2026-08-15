# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for a security problem.

Report it privately through GitLab: **Issues → New issue → tick "This issue is
confidential"**, or email the maintainer address listed on the project page.

Include what you can — affected version, reproduction steps, and impact. You will
get an acknowledgement, and credit in the release notes unless you prefer not to
be named.

## Supported versions

The latest release on `main`. This is a small project; older tags are not
back-patched.

## What BRACE already does

Worth knowing before assessing a finding:

- Passwords are bcrypt-hashed. Sessions are JWTs signed with `JWT_SECRET`.
- Git tokens, the AI API key and the SMTP password are encrypted at rest with
  Fernet (`BRACE_ENCRYPT_KEY`) and are never returned to the browser.
- The controller **refuses to start** outside a local environment if
  `JWT_SECRET` is still the built-in default.
- Every filesystem path from a request is resolved and containment-checked
  against the project directory before use.
- Project membership is enforced per request; cross-project identifiers are
  rejected rather than trusted.
- The container runs as a non-root user with all capabilities dropped.

## Deployment notes that are your responsibility

BRACE cannot enforce these for you:

- **`JWT_SECRET`** — anyone who knows it can forge a session as any user,
  including admin. Use 32+ random bytes and keep it in a Secret, not in a file.
- **`BRACE_ENCRYPT_KEY`** — without it, tokens and the SMTP password are stored
  in plain text. The startup log warns loudly when it is missing or malformed.
- **Change the bootstrap `admin` password.** On a fresh database with no
  `BRACE_ADMIN_PASSWORD` set, the account is `admin`/`admin` with a forced change
  at first login. Do not leave it exposed before that first login.
- **BRACE runs arbitrary code by design.** A Robot Framework suite is a program,
  and anyone who can edit scripts or trigger runs can execute code inside the
  container. Treat "can edit scripts" as equivalent to "can run code on that
  pod", and scope project membership accordingly.
- **The AI debug assistant sends test source and logs** to whichever endpoint is
  configured. Leave it disabled if that is not acceptable; users still get the
  copy-a-prompt option, which sends nothing.
- **Email notifications carry test names and failure messages.** Consider where
  your SMTP provider sits relative to your network boundary.
