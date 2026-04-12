"""
URL configuration: web UI, API, OpenAPI schema, i18n, media (dev).
"""

from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path, re_path
from django.views.i18n import set_language
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

def _redirect_en_admin(request, rest: str = ""):
    """L’admin Django n’est pas sous /en/ : on redirige vers /admin/…"""
    rest = (rest or "").strip("/")
    if rest:
        return redirect(f"/admin/{rest}/", permanent=False)
    return redirect("/admin/", permanent=False)


urlpatterns = [
    path("i18n/setlang/", set_language, name="set_language"),
    # Si l’utilisateur ouvre /en/admin/… (langue anglaise), renvoyer vers la vraie admin.
    re_path(r"^en/admin(?:/(?P<rest>.*))?$", _redirect_en_admin),
    path("admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/swagger/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/docs/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/", include("core.urls")),
]

urlpatterns += i18n_patterns(
    path("", include("core.web_urls")),
    prefix_default_language=False,
)

if settings.DEBUG:
    from django.conf.urls.static import static

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
