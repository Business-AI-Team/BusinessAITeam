"""
Resolved OpenAI API key: environment variable OPENAI_API_KEY wins, else Admin (IntegrationSettings).
Provides a singleton OpenAI client (one TCP/TLS connection reused across all calls within a process).
"""

from __future__ import annotations

import os

_openai_client = None


def get_openai_client():
    """Return a cached OpenAI client instance (singleton per process).

    Reuses the underlying HTTP connection pool so successive API calls avoid
    the TCP + TLS handshake overhead on every request.
    Returns None if no API key is configured.
    """
    global _openai_client
    if _openai_client is None:
        key = get_openai_api_key()
        if key:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=key)
    return _openai_client


def reset_openai_client() -> None:
    """Force the singleton to be recreated on next call (useful after key rotation)."""
    global _openai_client
    _openai_client = None


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
