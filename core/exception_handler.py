"""
DRF wrapper: consistent JSON errors for API clients (API-first UX).
"""

from __future__ import annotations

from typing import Any

from rest_framework.views import exception_handler as drf_exception_handler


def loanwise_exception_handler(exc: Exception, context: dict[str, Any]) -> Any:
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    response.data = {
        "error": True,
        "status_code": response.status_code,
        "messages": response.data,
    }
    return response
