from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "LoanWise Core"

    def ready(self) -> None:
        from core import signals  # noqa: F401 — register signal handlers

        from django.contrib import admin

        admin.site.site_header = "LoanWise"
        admin.site.site_title = _("Administration")
        admin.site.index_title = _("LoanWise — Setup")

        # Seed default document requirements once at startup instead of on every page load.
        # Wrapped in try/except so migrate / test runs (no DB yet) don't crash.
        try:
            from django.db import connection

            if connection.introspection.table_names() and "core_documentrequirement" in connection.introspection.table_names():
                from core.document_requirement_service import seed_default_requirements

                seed_default_requirements()
        except Exception:
            pass
