# ADR 0007: The application email is assembled by the code

Status: accepted. Implemented in `cv/email.py`, `cv/grounding.py` (`mixed_sentences`, `verify_email`), `cv/lexicon.py`
(`UNSUPPORTED_SCOPE_CLAIMS`, `GENERIC_WORDS`), `graphs/application_graph.py` (`draft_email`), `cv/latex.py`
(`_summary_block`), with the setting `EMAIL_MAX_FACTS`.

## Context

The model wrote the whole email, and the grounding check read it back. A check on words, numbers and listed terms passes
sentences that are true word by word but false together. Real drafts contained:

- "designing and deploying": scope words that no fact contains (the check did not know them);
- "engineered RAG pipelines using pgvector to power a document question-answering service": a sentence that joins a
  tool from one fact and a service from another, a claim that no fact makes;
- "improving user experience": a scope phrase without any fact behind it.

A stricter prompt does not fix this: the model still chooses the words, and the check can only refuse what it knows.

## Decisions

1. **The model writes three things only**: the subject, one opening sentence (why the candidate applies to this job
   title) and one closing sentence (a request for a conversation). It describes no experience, skill, tool, result,
   number or date.
2. **The facts are quoted by the code**, one fact per sentence, as written in the master CV or the knowledge base. Facts
   are never merged or rephrased. They are the experience and project facts the tailored CV cites, in CV order, up to
   `EMAIL_MAX_FACTS` (3 by default, between 1 and 6).
3. **The greeting, the sign-off and the signature are built by the code**, in the language of the job title: "Madame,
   Monsieur," and "Cordialement," in French, "Dear hiring team," and "Best regards," in English. The language is read from
   the job title only; a title with no marker of either language is English.
4. **Two checks are added to the email, and they are deterministic**:
   - *Mixed facts.* A sentence whose content words that appear in the facts are spread over two or more facts, with no
     single fact covering them all, is refused. Content words are folded, at least four letters, and not generic (`GENERIC_WORDS`:
     function words and words every application uses). Job-title words are set aside.
   - *Scope words.* "deploying", "designing", "engineered", "hands-on", "optimization" and "improving user experience" are
     claims. They pass only when a cited fact uses the same word.
5. **The fixed lines are not checked as claims.** The verifier reads the body without the greeting, the sign-off and the
   signature (`without_fixed_lines`). The same function is used at approval, so an approved body is checked the same way.
6. **A summary fact is the summary paragraph of the CV.** A CV line whose cited fact is of kind `summary` is rendered as
   one paragraph under the header, never as an experience bullet. The model still cannot choose the summary section
   (`Section` has no `summary` value), so the rule is applied by the renderer, from the fact kind.

## Consequences

- The email is less varied: the middle of the email is a list of facts, one sentence each. This is intended. Tone comes
  from the opening and closing sentences.
- A fact that is badly written in the master CV is quoted badly in the email. The fact is the source of truth, so the
  fix belongs in the master CV.
- The mixed-facts check is a heuristic on words. It can refuse an honest sentence that shares two generic-looking words
  with two facts, and it can miss a mix that uses only synonyms. Both cases are shown to the user, who decides. The
  check never approves a sentence on its own.
- The CV lines are not subject to the mixed-facts check. A CV line may cite a project and the skill it used. The check
  applies to the email only.
- The review page shows the section the model gave a summary line (usually "experience") even though the PDF renders
  it as the summary. This is a known inconsistency, to fix with the review chip.
- Existing stored emails (which contain the model's full body) still verify: the fixed lines are removed when present and
  kept when absent.
