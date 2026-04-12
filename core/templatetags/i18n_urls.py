"""
Build localized URLs for the language switcher (works with i18n_patterns).
"""

from __future__ import annotations

from django import template
from django.urls import translate_url

register = template.Library()


@register.simple_tag(takes_context=True)
def localized_path(context, lang_code: str) -> str:
    """
    Return the current path translated to `lang_code` (preserves query string).

    Uses django.urls.translate_url so /dashboard/ <-> /en/dashboard/ when needed.
    """
    request = context["request"]
    full = request.get_full_path()
    if "?" in full:
        path, query = full.split("?", 1)
        new_path = translate_url(path, lang_code)
        return f"{new_path}?{query}"
    return translate_url(full, lang_code)
