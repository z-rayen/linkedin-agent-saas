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

# Typographic characters that look like ASCII ones but are not: they are equal for the grounding check and for the
# LaTeX rendering. Spaces: no-break, narrow no-break and thin. Hyphen: non-breaking. Quotes: curly.
TYPOGRAPHIC_EQUIVALENTS: dict[str, str] = {
    " ": " ", " ": " ", " ": " ",
    "‑": "-", "‐": "-", "‒": "-", "–": "-", "—": "-",
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
}  # fmt: skip
_TYPOGRAPHY = str.maketrans(TYPOGRAPHIC_EQUIVALENTS)


def fold(text: str) -> str:
    """Lowercase, strip accents, and use the ASCII equivalent of typographic characters. Used on both sides of every
    comparison, so "2 000", "well‑known" and "’" compare as their plain forms."""
    normalised = unicodedata.normalize("NFKD", text.translate(_TYPOGRAPHY))
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
        "native", "natif", "proficient", "advanced", "advance", "avance", "avancee", "intermediate", "intermediaire",
        "beginner", "debutant", "confirme", "confirmee", "senior",
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

# Scope words that models add to a sentence without any fact behind them (seen in real drafts). They are claims: a
# sentence may use one only when a cited fact uses it too.
UNSUPPORTED_SCOPE_CLAIMS: frozenset[str] = frozenset(
    {
        "deploying", "designing", "engineered", "hands-on", "hands on", "improving user experience",
        "improve user experience", "optimization", "optimisation", "optimizing", "optimising",
    }
)  # fmt: skip

CLAIM_WORDS: frozenset[str] = SCOPE_CLAIMS | LEVEL_CLAIMS | DEGREE_CLAIMS | UNSUPPORTED_SCOPE_CLAIMS

# Words that carry no fact on their own: function words, greetings, and words every application uses. A sentence that
# shares only these with two facts does not mix the facts. Folded, four letters or more (shorter words are never facts).
GENERIC_WORDS: frozenset[str] = frozenset(
    fold(word)
    for word in (
        "about", "also", "applying", "application", "apply", "around", "candidate", "candidature", "company",
        "dear", "entreprise", "experience", "experiences", "from", "have", "hello", "interest", "interested",
        "internship", "into", "job", "looking", "more", "opportunity", "over", "poste", "position", "project",
        "projet", "role", "stage", "team", "that", "their", "them", "there", "these", "this", "those", "thank",
        "thanks", "using", "very", "which", "with", "within", "work", "working", "writing", "avec", "dans", "pour",
        "sans", "dont", "cette", "ceci", "cela", "alors", "aussi", "ainsi", "entre", "comme", "plus", "tres",
        "tout", "toute", "tous", "sont", "sera", "etre", "avoir", "fait", "faire", "mais", "donc", "votre", "notre",
        "vous", "nous", "leur", "leurs", "bonjour", "madame", "monsieur", "regards", "cordialement", "sincerely",
        "discuss", "conversation", "availability", "disponible", "merci", "builds", "build", "built", "created",
        "developed", "implemented", "improved", "helped",
    )
)  # fmt: skip

# Capitalised words that assert nothing. Stored as written; compared folded.
_NON_CLAIM_DISPLAY: tuple[str, ...] = (
    # English openers and neutral verbs
    "A", "An", "And", "At", "Built", "Created", "Delivered", "Designed", "Designing", "Developed", "Deployed",
    "For", "I", "Implemented", "Improved", "In", "Increased", "Of", "On", "The", "To", "Trained", "Used", "With",
    "Worked", "Reduced", "Participated", "Helped", "Contributed",
    # Letter politeness: neutral words, asserting nothing about the candidate
    "Your", "Yours", "Kind", "Respectfully", "Warm", "Warmly",
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
    "Merci", "Bien", "Salutations", "Veuillez", "Recevez", "Sinceres", "Distinguees", "Respectueusement",
)  # fmt: skip

NON_CLAIM_FOLDED: frozenset[str] = frozenset(fold(word) for word in _NON_CLAIM_DISPLAY)
