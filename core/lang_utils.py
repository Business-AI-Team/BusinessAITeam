"""
Single source of truth for language resolution across LoanWise.

Instead of repeating the same 3-way fallback in every view and service,
call resolve_language() with whatever context is available.
"""

from __future__ import annotations


def resolve_language(
    request=None,
    user=None,
    application=None,
    fallback: str = "fr",
) -> str:
    """
    Resolve the active display language, in priority order:

      1. Django active language on the request  (URL prefix / language cookie)
      2. User's stored language preference       (database field ``preferred_language``)
      3. Language recorded on the loan application
      4. Hard fallback (default: "fr")

    All callers (views, API endpoints, services) should use this function
    rather than reading ``preferred_language`` or ``LANGUAGE_CODE`` directly.
    """
    lang = (
        getattr(request, "LANGUAGE_CODE", None)
        or getattr(user, "preferred_language", None)
        or getattr(application, "language", None)
        or fallback
    )
    return lang.lower()
