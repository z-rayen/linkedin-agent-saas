"""Vocabulary the grounding check relies on.

All entries below are in "folded" form: lowercase, without accents, with straight apostrophes.
The checker folds the text it reads the same way, so "dirigé", "Dirigé" and "dirige" match.

Four kinds of words:
- TECHNOLOGIES: tools and languages, checked in any case.
- CLAIM_WORDS: words that assert scope (led, dirige), a language level (fluent, courant) or a
  degree (master, licence). Claiming more than a fact says is a claim, even in lowercase.
- NON_CLAIM_WORDS: capitalised words that assert nothing: articles, openers, greetings, neutral verbs.
- Accents and apostrophes are folded, so "maîtrise" and "maitrise" are the same word.
"""

import unicodedata


def fold(text: str) -> str:
    """Lowercase, strip accents, and use straight apostrophes. Used on both sides of every comparison."""
    normalised = unicodedata.normalize("NFKD", text.replace("’", "'"))
    return "".join(char for char in normalised if not unicodedata.combining(char)).lower()


TECHNOLOGIES: frozenset[str] = frozenset(
    {
        "airflow", "angular", "aws", "azure", "bash", "c++", "c#", "docker", "elasticsearch", "fastapi",
        "flask", "gcp", "git", "golang", "hadoop", "java", "javascript", "kafka", "keras", "kubernetes",
        "langchain", "langgraph", "linux", "matlab", "mongodb", "mysql", "numpy", "pandas", "pgvector",
        "postgresql", "power bi", "pytorch", "react", "redis", "rust", "scala", "scikit-learn", "spark",
        "sql", "tableau", "tensorflow", "terraform", "typescript", "vue",
    }
)  # fmt: skip

SCOPE_CLAIMS: frozenset[str] = frozenset(
    {
        # English
        "architected", "coordinated", "directed", "drove", "founded", "headed", "hired", "in charge of", "lead",
        "leads", "led", "manage", "managed", "mentored", "oversaw", "owned", "spearheaded", "supervise", "supervised",
        "took charge of",
        # French (folded: "dirigé" -> "dirige", "encadré" -> "encadre", "mené" -> "mene")
        "coordonne", "coordonnee", "dirige", "dirigee", "dirigent", "encadre", "encadree", "gere", "geree", "mene",
        "menee", "pilote", "pilotee", "prise en charge", "pris en charge", "responsable", "responsable de",
        "supervisee",
    }
)  # fmt: skip

LEVEL_CLAIMS: frozenset[str] = frozenset(
    {
        "bilingue", "couramment", "courant", "expert", "fluent", "fluently", "langue maternelle", "mother tongue",
        "native", "natif", "proficient",
    }
)  # fmt: skip

DEGREE_CLAIMS: frozenset[str] = frozenset(
    {
        # English
        "bachelor", "doctorate", "engineer's degree", "master", "master's", "mba", "msc", "phd",
        # French
        "bac+3", "bac+5", "diplome", "doctorat", "ingenieur", "licence", "maitrise", "mastere",
    }
)  # fmt: skip

CLAIM_WORDS: frozenset[str] = SCOPE_CLAIMS | LEVEL_CLAIMS | DEGREE_CLAIMS

# Capitalised words that assert nothing. Stored as written; compared folded.
_NON_CLAIM_DISPLAY: tuple[str, ...] = (
    # English openers and neutral verbs
    "A", "An", "And", "At", "Built", "Created", "Delivered", "Designed", "Designing", "Developed", "Deployed",
    "For", "I", "Implemented", "Improved", "In", "Increased", "Of", "On", "The", "To", "Trained", "Used", "With",
    "Worked", "Reduced", "Participated", "Helped", "Contributed",
    # Letters and emails: greetings and closings
    "Dear", "Hello", "Hi", "Hiring", "Team", "Regards", "Sincerely", "Best", "Thank", "Thanks", "Subject",
    "Application", "Position", "Role", "Opportunity",
    # French openers and neutral verbs
    "Au", "Aux", "Avec", "Ce", "Cette", "Concu", "Dans", "Des", "Du", "Et", "La", "Le", "Les", "Ma", "Mes",
    "Mon", "Pour", "Sur", "Un", "Une", "Ajoute", "Ameliore", "Developpe", "Je", "Nous", "Il", "Elle", "Son", "Sa",
    "Ses", "Leur", "We", "My", "Our", "He", "She",
    "Participe", "Contribue", "Aide", "Realise", "Travaille", "Cree", "Optimise", "Reduit", "Utilise", "Mise",
    # French letters
    "Bonjour", "Madame", "Monsieur", "Cordialement", "Candidature", "Objet",
)  # fmt: skip

NON_CLAIM_FOLDED: frozenset[str] = frozenset(fold(word) for word in _NON_CLAIM_DISPLAY)
