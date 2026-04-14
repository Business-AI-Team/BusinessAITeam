"""
Server-rendered pages (marketing + dashboard). Uses Django sessions; API remains primary.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import login, logout
from django.db import IntegrityError
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from django.conf import settings as django_settings

from core.application_ui import application_pipeline_progress_percent, income_display_for_payslip_estimate
from core.analysis_display import analysis_rows_for_template
from core.document_requirement_service import (
    default_doc_kind_for_requirement_code,
    description_for,
    document_counts_by_requirement_code,
    label_for,
    requirements_for_application,
    seed_default_requirements,
)
from core.models import Currency, Language, LoanApplication, LoanType
from core.portal import can_access_all_applications, get_loan_application_for_portal, is_backoffice_user
from core.countries_data import country_display_name
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
            try:
                user = ser.save()
            except IntegrityError:
                from core.countries_data import COUNTRY_CHOICES

                return render(
                    request,
                    "loanwise/register.html",
                    {
                        "lw_countries": COUNTRY_CHOICES,
                        "lw_email_taken": True,
                    },
                )
            messages.success(request, _("Account created. You can now log in."))
            return redirect("login_page")
        from core.countries_data import COUNTRY_CHOICES

        email_taken = bool(ser.errors.get("email") and any(
            "already exists" in str(e) for e in ser.errors["email"]
        ))
        if email_taken:
            return render(request, "loanwise/register.html", {
                "lw_countries": COUNTRY_CHOICES,
                "lw_email_taken": True,
            })
        messages.error(request, _("Please correct the errors below."))
        return render(
            request,
            "loanwise/register.html",
            {"lw_countries": COUNTRY_CHOICES, "lw_register_errors": ser.errors},
        )
    from core.countries_data import COUNTRY_CHOICES

    return render(
        request,
        "loanwise/register.html",
        {"lw_countries": COUNTRY_CHOICES},
    )


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

    roi = app.roi_summary or {}
    cust = getattr(app, "customer", None)
    cust_country_name = country_display_name(getattr(cust, "country", None), lang) if cust else ""

    # True once the AI pipeline has been run at least once
    has_been_analyzed = bool(app.eligibility_score is not None or roi)
    app_documents = list(app.documents.all().order_by("id"))

    ctx = {
        "application": app,
        "lw_customer": cust,
        "lw_customer_country_name": cust_country_name,
        "lw_currency_choices": Currency.choices,
        "lw_chat_app_id": app.pk,
        "lw_loan_type_choices": LoanType.choices,
        "lw_language_choices": Language.choices,
        "lw_portal_backoffice": app.user_id != request.user.id and can_access_all_applications(request.user),
        "lw_applicant_email": app.user.email if app.user_id else "",
        "lw_initial_progress": application_pipeline_progress_percent(app),
        "lw_rag_eligibility": rag_guidance,
        "lw_rag_summary": rag_summary,
        "lw_eligibility_detail": roi.get("eligibility_detail"),
        "lw_detail_lang": lang,
        "lw_analysis_rows": analysis_rows_for_template(app),
        "matrix_script_data": matrix_script_data,
        "lw_payslip_income_display": income_display_for_payslip_estimate(cust, app),
        "lw_has_been_analyzed": has_been_analyzed,
        "lw_app_documents": app_documents,
    }
    return render(request, "loanwise/application_detail.html", ctx)
