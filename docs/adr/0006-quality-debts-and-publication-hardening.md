# ADR 0006: Quality debts and publication hardening

Status: accepted. Implemented in `auth_utils.py`, `graphs/research_checks.py`, `prompts/`, `scripts/audit_tracked.py`,
`.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `backend/Dockerfile`, `frontend/Dockerfile`,
`docker-compose.yml`.

## Context

Before the project is published, a few decisions were still implicit or inconsistent: the password hashing relied on a
library that is no longer maintained, two prompt files were no longer used, the grounding check missed company names
written in lowercase, nothing stopped a secret or a personal address from entering a commit, and the base images were
referenced by tag only. This ADR records the decisions taken, and the limits of the heuristics that were added.

## Decisions

1. **Password hashing uses `bcrypt` directly; `passlib` is removed.** Existing `$2b$` hashes verify unchanged, so no user
   has to reset a password. `verify_password` returns `False` for a password longer than 72 bytes instead of silently
   truncating it, and for any malformed hash. The minimum length is `PASSWORD_MIN_LENGTH` (12 by default). The rounds
   come from `BCRYPT_ROUNDS`.
2. **Unused prompt templates are deleted.** `score_item.v1` and `cover_letter.v1` had no callers after the legacy
   endpoints were removed (ADR 0003 and the legacy-endpoint batch). The prompt registry no longer lists them.
3. **Lowercase company names are checked in grounding.** A company written in lowercase is detected only after one of
   the introducers `at`, `by`, `chez` or `par`, followed by one or two words. The check does not try to find every
   company in running text. Its limits:
   - a company named with a common word (`at Apple`, `by Design`) is caught only if the word is not in the list of
     phrase words; the list is kept with the check, so a false positive is fixed there, not in the prompt;
   - a company written without an introducer (`Acme ships the tool`) is not detected by this rule. Capitalised company
     names are covered by the capitalised-term check, as before;
   - the check flags, it does not rewrite. A flagged name is reported as a grounding problem for the user to fix.
4. **Email links and addresses are checked against the master CV.** A link or an address in the generated email body that
   is not present in the master CV is reported as `unknown_source`. The haystack is the master CV's own serialised
   content, so a link must appear there to pass.
5. **Base images are pinned by digest** (`python`, `node`, `nginxinc/nginx-unprivileged`, `pgvector/pgvector:pg16`). A
   digest is immutable, so a build does not change under a reviewer. Updating a digest is a reviewed change, made through
   Dependabot's Docker ecosystem or by hand, and the image is rebuilt and checked before it is merged.
6. **The publication audit scans tracked and staged files for secrets and personal data.** `scripts/audit_tracked.py`
   reports the file, the line and the rule name, never the value. The pre-commit hook runs it on staged files, and the CI
   job runs it on the whole repository. Placeholder values are allowed only through an explicit allowlist: reserved domains
   (`example.com`, `*.example`, `*.test`), placeholder tokens and the documented sample data. An allowlist entry is a
   decision, so it is reviewed like code. A real-looking test address in a fixture is a finding, not a placeholder.
7. **Fixtures are validated.** A test checks that every literal fixture used as a model input is valid for its schema.
   The one exception is code deliberately invalid inside `pytest.raises`.

## Consequences

- A user who had a password hashed by the old code can log in unchanged. A user with a password longer than 72 bytes must
  use a shorter one, and the API says so.
- The grounding check has known blind spots (the lowercase company rule above). A flagged item is shown to the user for
  review; the check never approves content on its own.
- The audit is a heuristic. It reduces the risk of publishing a secret; it does not replace a review before publication.
- Digest pinning makes the Docker build reproducible, and makes security updates a deliberate step.

## Not decided here

- The licence (see `docs/decisions/license.md`).
- The security and conduct contacts, which are placeholders in `SECURITY.md` and `CODE_OF_CONDUCT.md`.
