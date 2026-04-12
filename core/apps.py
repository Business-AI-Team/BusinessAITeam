from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "LoanWise Core"

    def ready(self) -> None:
        # Titres de l’interface /admin/ (évite la confusion avec le site public).
        from django.contrib import admin

        admin.site.site_header = "LoanWise"
        admin.site.site_title = _("Administration")
        admin.site.index_title = _("LoanWise — users, applications, tokens")
