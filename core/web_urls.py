"""Web routes — Customer and Back-Office workspaces."""

from django.urls import path

from core import web_views

urlpatterns = [
    # Public
    path("", web_views.home, name="home"),
    path("register/", web_views.register_page, name="register_page"),
    path("verify-email/", web_views.verify_email_page, name="verify_email_page"),
    path("login/", web_views.login_page, name="login_page"),
    path("logout/", web_views.logout_view, name="logout"),
    path("profile/edit/", web_views.edit_profile, name="edit_profile"),

    # Generic redirect (backward compat)
    path("dashboard/", web_views.dashboard, name="dashboard"),
    path("applications/<int:pk>/", web_views.application_detail, name="application_detail"),

    # ── Customer workspace ──────────────────────────────────────────────────
    path("my/", web_views.customer_dashboard, name="customer_dashboard"),
    path("my/new-request/", web_views.loan_request_new, name="loan_request_new"),
    path("my/requests/<int:pk>/", web_views.loan_request_detail, name="loan_request_detail"),
    path("my/notifications/", web_views.notifications_page, name="notifications_page"),
    path("my/notifications/json/", web_views.notifications_json, name="notifications_json"),

    # ── Back-Office workspace ───────────────────────────────────────────────
    path("bo/", web_views.backoffice_dashboard, name="backoffice_dashboard"),
    path("bo/requests/", web_views.backoffice_loan_requests, name="backoffice_loan_requests"),
    path("bo/requests/<int:pk>/", web_views.backoffice_loan_request_detail, name="backoffice_loan_request_detail"),
    path("conditions/", web_views.backoffice_eligibility_conditions, name="backoffice_eligibility_conditions"),
    path("bo/requirements/", web_views.backoffice_document_requirements, name="backoffice_document_requirements"),
    path("bo/agents/", web_views.backoffice_agents, name="backoffice_agents"),
]
