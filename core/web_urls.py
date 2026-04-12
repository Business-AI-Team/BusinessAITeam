"""Web routes (non-API), language-prefixed via i18n_patterns in project urls."""

from django.urls import path

from core import web_views

urlpatterns = [
    path("", web_views.home, name="home"),
    path("register/", web_views.register_page, name="register_page"),
    path("verify-email/", web_views.verify_email_page, name="verify_email_page"),
    path("login/", web_views.login_page, name="login_page"),
    path("logout/", web_views.logout_view, name="logout"),
    path("dashboard/", web_views.dashboard, name="dashboard"),
    path("applications/<int:pk>/", web_views.application_detail, name="application_detail"),
]
