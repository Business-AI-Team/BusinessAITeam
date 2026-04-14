"""REST API URL routes."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core import api_views

router = DefaultRouter()
router.register(r"applications", api_views.LoanApplicationViewSet, basename="application")

urlpatterns = [
    path("auth/register/", api_views.RegisterView.as_view(), name="api-register"),
    path("auth/login/", api_views.LoginView.as_view(), name="api-login"),
    path("auth/me/", api_views.MeView.as_view(), name="api-me"),
    path("documents/upload/<int:application_id>/", api_views.DocumentUploadView.as_view(), name="api-document-upload"),
    path("documents/<int:document_id>/download/", api_views.DocumentDownloadView.as_view(), name="api-document-download"),
    path("documents/<int:document_id>/", api_views.DocumentDeleteView.as_view(), name="api-document-delete"),
    path("requirements/", api_views.DocumentRequirementListView.as_view(), name="api-requirements"),
    path("applications/<int:pk>/rag_guidance/", api_views.ApplicationRagGuidanceView.as_view(), name="api-application-rag-guidance"),
    path("applications/<int:pk>/documents/", api_views.ApplicationDocumentsView.as_view(), name="api-application-documents"),
    path("assistant/chat/", api_views.AssistantChatView.as_view(), name="api-assistant-chat"),
    path("health/", api_views.HealthView.as_view(), name="api-health"),
    path("seed/", api_views.SeedDemoView.as_view(), name="api-seed"),
    path("", include(router.urls)),
]
