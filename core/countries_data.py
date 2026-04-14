"""
Liste pays ISO 3166-1 alpha-2 pour sélection à l’inscription (recherche côté client).
"""

from __future__ import annotations

# (code, name_fr, name_en)
COUNTRY_CHOICES: list[tuple[str, str, str]] = [
    ("MG", "Madagascar", "Madagascar"),
    ("FR", "France", "France"),
    ("MU", "Maurice", "Mauritius"),
    ("RE", "La Réunion", "Réunion"),
    ("YT", "Mayotte", "Mayotte"),
    ("BE", "Belgique", "Belgium"),
    ("CH", "Suisse", "Switzerland"),
    ("CA", "Canada", "Canada"),
    ("US", "États-Unis", "United States"),
    ("GB", "Royaume-Uni", "United Kingdom"),
    ("DE", "Allemagne", "Germany"),
    ("IT", "Italie", "Italy"),
    ("ES", "Espagne", "Spain"),
    ("PT", "Portugal", "Portugal"),
    ("NL", "Pays-Bas", "Netherlands"),
    ("LU", "Luxembourg", "Luxembourg"),
    ("AT", "Autriche", "Austria"),
    ("SN", "Sénégal", "Senegal"),
    ("CI", "Côte d'Ivoire", "Côte d'Ivoire"),
    ("CM", "Cameroun", "Cameroon"),
    ("MA", "Maroc", "Morocco"),
    ("DZ", "Algérie", "Algeria"),
    ("TN", "Tunisie", "Tunisia"),
    ("GA", "Gabon", "Gabon"),
    ("CD", "RD Congo", "DR Congo"),
    ("CG", "Congo", "Congo"),
    ("KM", "Comores", "Comoros"),
    ("SC", "Seychelles", "Seychelles"),
]


def country_display_name(code: str | None, lang: str = "fr") -> str:
    c = (code or "").strip().upper()[:2]
    for row in COUNTRY_CHOICES:
        if row[0] == c:
            return row[1] if lang.startswith("fr") else row[2]
    return c or "—"
