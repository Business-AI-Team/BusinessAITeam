"""
Resolved OpenAI API key: environment variable OPENAI_API_KEY wins, else Admin (IntegrationSettings).
"""

from __future__ import annotations

import os


def get_openai_api_key() -> str:
    """
    Return the active OpenAI API key.

    Precedence:
    1. ``OPENAI_API_KEY`` environment variable (e.g. .env, Docker secrets).
    2. Value stored in Django Admin → Integration settings (singleton).
    """
    env = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if env:
        return env
    try:
        from core.models import IntegrationSettings

        row = IntegrationSettings.objects.filter(pk=1).first()
        if row and (row.openai_api_key or "").strip():
            return row.openai_api_key.strip()
    except Exception:
        pass
    return ""
