# Security policy

## Supported versions

The project is pre-release. Only the `main` branch receives security fixes.

## Reporting a vulnerability

Please report security problems privately. Do not open a public issue, a pull request or a discussion about them.

Contact: **[SECURITY CONTACT: to be set by the project owner, for example a private e-mail address or the repository's
private vulnerability reporting]**

Please include:

- what you found and where (file, endpoint, configuration);
- the steps to reproduce it, or a proof of concept that does not touch other people's data;
- the impact you expect;
- whether you want credit in the fix.

You will receive an acknowledgement, and the fix will be discussed with you before anything is published.

## What is in scope

- The API (`backend/`), the web interface (`frontend/`) and the Docker configuration.
- Anything that lets one user read, change or send another user's data, or that bypasses the approval step before an
  e-mail is sent.
- Leaks of credentials, tokens or personal data through logs, responses, URLs or committed files.

## What is out of scope

- Reports that need a compromised machine or a compromised provider account.
- Missing security headers on a development server (`npm run dev`, `uvicorn --reload`), which are not for exposure.
- Findings from automated scanners without a demonstrated impact.

## Our defaults

- Passwords are hashed with bcrypt. Failed logins are slowed down per account and address.
- OAuth tokens and SMTP credentials are encrypted at rest (Fernet).
- The database port is published on `127.0.0.1` only.
- Sending is dry-run unless explicitly enabled, and a real send needs a redirect address or an explicit opt-in.
- `scripts/audit_tracked.py` looks for keys, private keys and real-looking personal data before each commit.
