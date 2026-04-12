"""
Server-rendered pages (marketing + dashboard). Uses Django sessions; API remains primary.
"""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from django.conf import settings as django_settings

from core.application_ui import application_pipeline_progress_percent
from core.analysis_display import analysis_rows_for_template
from core.document_requirement_service import (
    default_doc_kind_for_requirement_code,
    description_for,
    document_counts_by_requirement_code,
    label_for,
    requirements_for_application,
    seed_default_requirements,
)
from core.models import LoanApplication
from core.portal import can_access_all_applications, get_loan_application_for_portal, is_backoffice_user
from core.rag_eligibility import get_eligibility_guidance_from_rag


def home(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "loanwise/home.html",
        {
            "commercial_name": "Smart Loan Eligibility Checker",
            "team": "Business AI Team (Tantely, Hasina, Hardi, Frederic)",
        },
    )


@require_http_methods(["GET", "POST"])
def register_page(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("backoffice_dashboard" if is_backoffice_user(request.user) else "dashboard")
    if request.method == "POST":
        from core.serializers import RegisterSerializer

        ser = RegisterSerializer(data=request.POST)
        if ser.is_valid():
            user = ser.save()
            from django.conf import settings

            from core.email_verification_service import send_registration_verification_email
            from core.models import EmailVerificationToken

            if getattr(settings, "LOANWISE_AUTO_VERIFY_EMAIL_IN_DEBUG", False) and settings.DEBUG:
                user.email_verified = True
                user.save(update_fields=["email_verified"])

            evt = EmailVerificationToken.create_for_user(user)
            verify_url = f"{settings.FRONTEND_BASE_URL.rstrip('/')}/api/auth/verify/?token={evt.token}"
            try:
                send_registration_verification_email(user, evt)
            except Exception:
                messages.warning(
                    request,
                    _("We could not send the email. Enter the code from the server log or ask an administrator."),
                )
            if user.email_verified and getattr(settings, "LOANWISE_AUTO_VERIFY_EMAIL_IN_DEBUG", False):
                messages.success(request, _("Account created. You can log in — your email is marked verified in development."))
            else:
                messages.success(request, _("Account created. Check your email for your verification code."))
            if not user.email_verified and (getattr(settings, "DEBUG", False) or "console" in settings.EMAIL_BACKEND.lower()):
                messages.info(
                    request,
                    _(
                        "Development: the email is printed in the runserver terminal. Your verification code is %(code)s. "
                        "You can also open: %(url)s"
                    )
                    % {"code": evt.code, "url": verify_url},
                )
            return redirect("login_page")
        messages.error(request, _("Please correct the errors below."))
    return render(request, "loanwise/register.html")


@require_http_methods(["GET", "POST"])
def login_page(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("backoffice_dashboard" if is_backoffice_user(request.user) else "dashboard")
    if request.method == "POST":
        from django.contrib.auth import authenticate

        email = request.POST.get("email", "").lower().strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=email, password=password)
        if user:
            login(request, user)
            if is_backoffice_user(user):
                return redirect("backoffice_dashboard")
            return redirect("dashboard")
        messages.error(request, _("Invalid email or password."))
    return render(request, "loanwise/login.html")


@require_http_methods(["GET", "POST"])
def verify_email_page(request: HttpRequest) -> HttpResponse:
    """Enter email + numeric code from the registration email."""
    from django.contrib.auth import get_user_model

    from core.email_verification_service import verify_email_with_code

    if request.user.is_authenticated and request.user.email_verified:
        return redirect("backoffice_dashboard" if is_backoffice_user(request.user) else "dashboard")

    if request.method == "POST":
        email = (request.POST.get("email") or "").strip()
        code = (request.POST.get("code") or "").strip()
        ok, detail = verify_email_with_code(email, code)
        if ok:
            User = get_user_model()
            user = User.objects.filter(email__iexact=email.lower()).first()
            if user:
                from django.contrib.auth import login

                login(request, user)
            if detail == "already_verified":
                messages.info(request, _("This email was already verified. You are signed in."))
            else:
                messages.success(request, _("Your email is verified. Welcome!"))
            return redirect("backoffice_dashboard" if is_backoffice_user(request.user) else "dashboard")
        if detail == "email_and_code_required":
            messages.error(request, _("Please enter your email and the verification code."))
        elif detail == "invalid_code":
            messages.error(request, _("The code must contain exactly six digits."))
        else:
            messages.error(request, _("Invalid email or verification code. Check the code in your email and try again."))
        return render(
            request,
            "loanwise/verify_email.html",
            {"prefill_email": email},
        )

    prefill = ""
    if request.user.is_authenticated:
        prefill = request.user.email
    return render(request, "loanwise/verify_email.html", {"prefill_email": prefill})


@login_required
def logout_view(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("home")


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    seed_default_requirements()
    if is_backoffice_user(request.user):
        return redirect("backoffice_dashboard")
    apps = LoanApplication.objects.filter(user=request.user)[:50]
    return render(
        request,
        "loanwise/dashboard.html",
        {"applications": apps},
    )


@login_required
def backoffice_dashboard(request: HttpRequest) -> HttpResponse:
    """Bank staff: all customers' applications (not Django Admin)."""
    if not is_backoffice_user(request.user) and not request.user.is_superuser:
        messages.error(request, _("You do not have access to the backoffice."))
        return redirect("dashboard")
    seed_default_requirements()
    apps = LoanApplication.objects.select_related("user").order_by("-created_at")[:200]
    return render(
        request,
        "loanwise/backoffice/dashboard.html",
        {"applications": apps},
    )


@login_required
def application_detail(request: HttpRequest, pk: int) -> HttpResponse:
    seed_default_requirements()
    app = get_loan_application_for_portal(request.user, pk)
    if not app:
        messages.error(request, _("Application not found."))
        return redirect("backoffice_dashboard" if can_access_all_applications(request.user) else "dashboard")
    lang = getattr(request.user, "preferred_language", None) or "fr"
    rag_guidance = get_eligibility_guidance_from_rag(app.loan_type or "personal", lang)
    if lang.startswith("fr"):
        rag_summary = (rag_guidance.get("summary_fr") or rag_guidance.get("summary_en") or "").strip()
    else:
        rag_summary = (rag_guidance.get("summary_en") or rag_guidance.get("summary_fr") or "").strip()

    counts = document_counts_by_requirement_code(app)
    matrix_slots: list[dict] = []
    for req in requirements_for_application(app):
        if not req.is_required:
            continue
        have = counts.get(req.code, 0)
        matrix_slots.append(
            {
                "requirement_id": req.pk,
                "code": req.code,
                "label": label_for(req, lang),
                "description": description_for(req, lang),
                "files_needed": max(1, getattr(req, "min_files", 1) or 1),
                "doc_kind": default_doc_kind_for_requirement_code(req.code),
                "uploaded": have,
            }
        )
    matrix_script_data = {"slots": matrix_slots}

    ctx = {
        "application": app,
        "lw_portal_backoffice": app.user_id != request.user.id and can_access_all_applications(request.user),
        "lw_applicant_email": app.user.email if app.user_id else "",
        "lw_initial_progress": application_pipeline_progress_percent(app),
        "lw_rag_eligibility": rag_guidance,
        "lw_rag_summary": rag_summary,
        "lw_email_verified": request.user.email_verified,
        "lw_require_email": django_settings.LOANWISE_REQUIRE_EMAIL_VERIFICATION,
        "lw_analysis_rows": analysis_rows_for_template(app),
        "matrix_script_data": matrix_script_data,
    }
    if django_settings.DEBUG:
        docs = list(app.documents.all().order_by("-created_at"))
        ctx["lw_ai_debug_json"] = json.dumps(
            {
                "orchestration_log": app.orchestration_log,
                "documents": [
                    {
                        "id": d.id,
                        "kind": d.kind,
                        "original_filename": d.original_filename,
                        "analysis_result": d.analysis_result,
                    }
                    for d in docs
                ],
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    else:
        ctx["lw_ai_debug_json"] = None
    return render(request, "loanwise/application_detail.html", ctx)
