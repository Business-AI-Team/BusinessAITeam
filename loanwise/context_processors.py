"""Template globals for LoanWise branding and product metadata."""

from django.conf import settings


def loanwise_globals(request):
    lang = getattr(request, "LANGUAGE_CODE", None) or settings.LANGUAGE_CODE
    # i18n_patterns: default language has no URL prefix when prefix_default_language is False.
    lang_prefix = "" if lang == settings.LANGUAGE_CODE else f"/{lang}"
    return {
        "LOANWISE_NAME": "LoanWise",
        "LOANWISE_COMMERCIAL_NAME": "Smart Loan Eligibility Checker",
        "FRONTEND_BASE_URL": getattr(settings, "FRONTEND_BASE_URL", ""),
        "LANG_PREFIX": lang_prefix,
        "lw_chat_app_id": None,
        "lw_chat_page_context": "home",
    }
