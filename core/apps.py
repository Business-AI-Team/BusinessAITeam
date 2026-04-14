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
