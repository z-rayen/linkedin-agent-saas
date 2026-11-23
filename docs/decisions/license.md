# Decision needed: the licence of the project

Status: open. The owner chooses. No licence file and no licence field have been added to the repository, on purpose.
Until a licence is chosen, the default of copyright law applies: the code is all rights reserved, and nobody else may
copy, modify or redistribute it. Choose before the repository is published.

## The two candidates

**MIT.** A short permissive licence: anyone may use, copy, modify, merge, publish and sell the software, provided the
copyright notice and the licence text are kept. No patent grant, no statement about changes, no trademark clause.

**Apache-2.0.** A permissive licence of similar scope, longer. It adds an express patent grant from each contributor, a
patent-retaliation clause (a licensee who sues over a patent loses the grant), a clear rule that trademarks are not
granted, and a requirement to state significant changes made to the files.

## What matters for this project

| Question | MIT | Apache-2.0 |
| --- | --- | --- |
| Can a company use it inside a product? | Yes | Yes |
| Can a person run it for themselves? | Yes | Yes |
| Patent protection for contributors and users | Not stated | Express grant, with retaliation |
| Notice of changes to the files | Not required | Required for modified files |
| Length and reading effort | One page | Longer, but standard in enterprises |
| Compatibility with GPL-2.0-only dependencies | Compatible in one direction only | Not compatible with GPL-2.0-only |

## Points specific to this project

- The application handles personal data (CVs, applications, e-mail). A licence does not change the owner's duties under
  the GDPR or other data protection law, whichever is chosen. The licence is not a privacy policy.
- The project explicitly states the responsible-use rules (official API only, human approval before any e-mail, the user
  responsible for the terms of the services). Neither licence overrides them, and both allow a fork to remove them. If
  that matters, the owner may add a notice in the README; it cannot be enforced through the licence.
- Dependencies are permissively licensed (FastAPI, SQLAlchemy, React and similar). Check the licence of the LaTeX engine
  (Tectonic, MIT) and of the embedding model before publishing, as the model has its own terms.

## What to do

1. Choose one. For a project that may attract outside contributions and corporate users, Apache-2.0 is the safer
   default because of the patent grant. For the simplest possible text, MIT.
2. Add `LICENSE` at the repository root with the full text of the chosen licence and the copyright line.
3. Add the licence to `backend/pyproject.toml` (`license` field) and to `frontend/package.json` (`license` field).
4. Mention it in the README, section "License".
