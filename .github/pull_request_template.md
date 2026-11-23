## What this changes

<!-- One paragraph. Why, not only what. -->

## How it was verified

<!-- Commands you ran and their result. For a change of behaviour, the test that proves it. -->

- [ ] Backend: `ruff check`, `ruff format --check`, `mypy`, `pytest` (and `-m integration` when the database changed)
- [ ] Frontend: `format:check`, `lint`, `typecheck`, `test`, `build`, `check:types`
- [ ] `python scripts/audit_tracked.py` reports no finding

## Rules this change respects

- [ ] No automation of LinkedIn beyond the official API
- [ ] Nothing is sent without explicit approval; dry-run stays the default
- [ ] Generated content stays grounded in the master CV and the knowledge base
- [ ] No secret, personal data or real address in the diff

## Notes for the reviewer

<!-- Decisions taken, trade-offs, anything to check by hand. Link the ADR when there is one. -->
