"""
Keep `User.preferred_language` aligned with the active UI language (session/cookie).
"""

from __future__ import annotations

from typing import Callable

from django.http import HttpRequest, HttpResponse


class SyncPreferredLanguageMiddleware:
    """After LocaleMiddleware, mirror `request.LANGUAGE_CODE` onto the logged-in user."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            lang = getattr(request, "LANGUAGE_CODE", None)
            if lang and getattr(user, "preferred_language", None) != lang:
                user.preferred_language = lang
                user.save(update_fields=["preferred_language"])
        return response
