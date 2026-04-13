"""
Server-rendered pages — Customer and Back-Office workspaces.

Routing logic:
  - Unauthenticated → login / register (Customer only)
  - account_type = customer → customer_dashboard, loan_request_new, loan_request_detail, notifications
  - account_type = backoffice → backoffice_dashboard, backoffice_loan_requests, backoffice_eligibility_conditions, backoffice_document_requirements
"""

from __future__ import annotations

import json
import os

from django.conf import settings as django_settings
from django.contrib import messages
from django.db import models
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from core.document_requirement_service import all_requirements
from core.models import (
    Account,
    AccountType,
    BackOffice,
    Customer,
    Document,
    DocumentRequirement,
    DocumentType,

    EligibilityCondition,
    LoanRequest,
    LoanRequestStatus,
    LoanType,
    Notification,
)
from core.rag_eligibility import (
    delete_eligibility_source_index,
    extract_text_from_file,
    index_eligibility_source,
)

Account = get_user_model()

# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_backoffice(user) -> bool:
    return getattr(user, "account_type", None) == AccountType.BACKOFFICE


def _require_customer(view_fn):
    """Decorator: must be logged in AND be a Customer."""
    @login_required
    def wrapped(request, *args, **kwargs):
        if _is_backoffice(request.user):
            return redirect("backoffice_dashboard")
        return view_fn(request, *args, **kwargs)
    wrapped.__name__ = view_fn.__name__
    return wrapped


def _require_backoffice(view_fn):
    """Decorator: must be logged in AND be a Back-Office agent."""
    @login_required
    def wrapped(request, *args, **kwargs):
        if not _is_backoffice(request.user):
            return redirect("customer_dashboard")
        return view_fn(request, *args, **kwargs)
    wrapped.__name__ = view_fn.__name__
    return wrapped


# ── Public pages ──────────────────────────────────────────────────────────────

def home(request: HttpRequest) -> HttpResponse:
    return render(request, "loanwise/home.html", {
        "commercial_name": "Smart Loan Eligibility Checker",
        "team": "Business AI Team (Tantely, Hasina, Hardi, Frederic)",
    })


@require_http_methods(["GET", "POST"])
def register_page(request: HttpRequest) -> HttpResponse:
    """Customer self-registration."""
    if request.user.is_authenticated:
        return redirect("backoffice_dashboard" if _is_backoffice(request.user) else "customer_dashboard")

    if request.method == "POST":
        email = request.POST.get("email", "").lower().strip()
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        phone = request.POST.get("phone", "").strip()
        address = request.POST.get("address", "").strip()
        password = request.POST.get("password", "")
        password2 = request.POST.get("password2", "")
        lang = request.POST.get("preferred_language", "fr")

        errors = []
        if not email:
            errors.append(_("Email is required."))
        if Account.objects.filter(email__iexact=email).exists():
            errors.append(_("An account with this email already exists."))
        if len(password) < 8:
            errors.append(_("Password must be at least 8 characters."))
        if password != password2:
            errors.append(_("Passwords do not match."))

        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, "loanwise/register.html", {
                "form_data": request.POST,
            })

        username = email.split("@")[0]
        base = username
        i = 1
        while Account.objects.filter(username=username).exists():
            username = f"{base}{i}"
            i += 1

        user = Account(
            email=email,
            username=username,
            first_name=first_name,
            last_name=last_name,
            preferred_language=lang,
            account_type=AccountType.CUSTOMER,
            email_verified=True,  # simplified: no email verification for now
        )
        user.set_password(password)
        user.save()

        # Create linked Customer profile
        customer = Customer.objects.create(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            address=address,
        )
        user.customer = customer
        user.save(update_fields=["customer"])

        messages.success(request, _("Account created. You can now log in."))
        return redirect("login_page")

    return render(request, "loanwise/register.html")


@require_http_methods(["GET", "POST"])
def login_page(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("backoffice_dashboard" if _is_backoffice(request.user) else "customer_dashboard")

    if request.method == "POST":
        email = request.POST.get("email", "").lower().strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=email, password=password)
        if user:
            login(request, user)
            if _is_backoffice(user):
                return redirect("backoffice_dashboard")
            return redirect("customer_dashboard")
        messages.error(request, _("Invalid email or password."))
    return render(request, "loanwise/login.html")


@login_required
def logout_view(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("home")


# Kept for backward compat
def dashboard(request):
    if not request.user.is_authenticated:
        return redirect("login_page")
    if _is_backoffice(request.user):
        return redirect("backoffice_dashboard")
    return redirect("customer_dashboard")


# ── Customer workspace ────────────────────────────────────────────────────────

@_require_customer
def customer_dashboard(request: HttpRequest) -> HttpResponse:
    loan_requests = LoanRequest.objects.filter(account=request.user).order_by("-creation_date")
    # Unread notifications count
    unread_count = 0
    for lr in loan_requests:
        try:
            if not lr.notification.read:
                unread_count += 1
        except Exception:
            pass
    return render(request, "loanwise/customer/dashboard.html", {
        "loan_requests": loan_requests,
        "unread_count": unread_count,
    })


@_require_customer
@require_http_methods(["GET", "POST"])
def loan_request_new(request: HttpRequest) -> HttpResponse:
    """Create a new LoanRequest with initial documents."""
    requirements = all_requirements()

    if request.method == "POST":
        loan_type = request.POST.get("loan_type", LoanType.PERSONAL)
        purpose = request.POST.get("purpose", "").strip()
        amount = request.POST.get("amount_requested", "0").replace(",", ".").strip() or "0"
        term = request.POST.get("term_months", "12").strip() or "12"
        income = request.POST.get("annual_income", "0").replace(",", ".").strip() or "0"

        try:
            from decimal import Decimal
            amount_d = Decimal(amount)
            income_d = Decimal(income)
            term_i = int(term)
        except Exception:
            messages.error(request, _("Please enter valid numbers."))
            return render(request, "loanwise/customer/loan_request_new.html", {
                "requirements": requirements,
                "loan_types": LoanType.choices,
                "form_data": request.POST,
            })

        # Get or create Customer profile
        customer = getattr(request.user, "customer", None)
        if customer is None:
            customer = Customer.objects.create(
                email=request.user.email,
                first_name=request.user.first_name,
                last_name=request.user.last_name,
            )
            request.user.customer = customer
            request.user.save(update_fields=["customer"])

        lr = LoanRequest.objects.create(
            account=request.user,
            customer=customer,
            loan_type=loan_type,
            purpose=purpose,
            amount_requested=amount_d,
            term_months=term_i,
            annual_income=income_d,
            status=LoanRequestStatus.PENDING,
            language=getattr(request.user, "preferred_language", "fr"),
        )

        # Handle document uploads
        files = request.FILES.getlist("documents")
        doc_types = request.POST.getlist("doc_types")
        req_ids = request.POST.getlist("req_ids")

        for i, f in enumerate(files):
            dtype = doc_types[i] if i < len(doc_types) else DocumentType.OTHER
            req_id = req_ids[i] if i < len(req_ids) else None
            req = None
            if req_id:
                req = DocumentRequirement.objects.filter(pk=req_id).first()

            from django.core.files.storage import default_storage
            from core.security_utils import sha256_file
            rel_path = f"loanwise/{lr.id}/{f.name}"
            path = default_storage.save(rel_path, f)
            full = default_storage.path(path)
            digest = sha256_file(full)

            Document.objects.create(
                loan_request=lr,
                requirement=req,
                document_type=dtype,
                original_filename=f.name,
                content_type=f.content_type or "",
                sha256_hex=digest,
                file_size=f.size,
                storage_path=path,
                file=path,
            )

        # Create notification
        Notification.objects.get_or_create(loan_request=lr, defaults={"read": False})

        messages.success(request, _("Loan request submitted successfully."))
        return redirect("loan_request_detail", pk=lr.pk)

    return render(request, "loanwise/customer/loan_request_new.html", {
        "requirements": requirements,
        "loan_types": LoanType.choices,
        "document_types": DocumentType.choices,
    })


@_require_customer
def loan_request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    lr = get_object_or_404(LoanRequest, pk=pk, account=request.user)
    documents = lr.documents.all().order_by("-created_at")
    requirements = all_requirements()

    # Handle additional document upload
    if request.method == "POST":
        files = request.FILES.getlist("documents")
        doc_types = request.POST.getlist("doc_types")
        req_ids = request.POST.getlist("req_ids")
        for i, f in enumerate(files):
            dtype = doc_types[i] if i < len(doc_types) else DocumentType.OTHER
            req_id = req_ids[i] if i < len(req_ids) else None
            req = DocumentRequirement.objects.filter(pk=req_id).first() if req_id else None
            from django.core.files.storage import default_storage
            from core.security_utils import sha256_file
            rel_path = f"loanwise/{lr.id}/{f.name}"
            path = default_storage.save(rel_path, f)
            full = default_storage.path(path)
            digest = sha256_file(full)
            Document.objects.create(
                loan_request=lr,
                requirement=req,
                document_type=dtype,
                original_filename=f.name,
                content_type=f.content_type or "",
                sha256_hex=digest,
                file_size=f.size,
                storage_path=path,
            )
        messages.success(request, _("Document(s) added."))
        return redirect("loan_request_detail", pk=pk)

    return render(request, "loanwise/customer/loan_request_detail.html", {
        "loan_request": lr,
        "documents": documents,
        "requirements": requirements,
        "document_types": DocumentType.choices,
        "status_choices": LoanRequestStatus.choices,
    })


@_require_customer
def notifications_page(request: HttpRequest) -> HttpResponse:
    loan_requests = LoanRequest.objects.filter(account=request.user).order_by("-creation_date")
    notifications = []
    for lr in loan_requests:
        try:
            n = lr.notification
            notifications.append({"notification": n, "loan_request": lr})
            if not n.read:
                n.read = True
                n.save(update_fields=["read"])
        except Exception:
            pass
    return render(request, "loanwise/customer/notifications.html", {
        "notifications": notifications,
    })


@_require_customer
def notifications_json(request: HttpRequest) -> JsonResponse:
    """Return notifications as JSON for the header bell modal."""
    from django.urls import reverse
    loan_requests = LoanRequest.objects.filter(account=request.user).order_by("-creation_date")
    data = []
    for lr in loan_requests:
        try:
            n = lr.notification
            data.append({
                "id": n.pk,
                "read": n.read,
                "loan_reference": lr.reference,
                "loan_request_id": lr.pk,
                "loan_request_url": reverse("loan_request_detail", args=[lr.pk]),
                "loan_status": lr.status,
                "loan_status_display": lr.get_status_display(),
                "date": n.date.strftime("%d/%m/%Y %H:%M"),
            })
        except Exception:
            pass
    unread_count = sum(1 for d in data if not d["read"])
    return JsonResponse({"notifications": data, "unread_count": unread_count})


# ── Back-Office workspace ─────────────────────────────────────────────────────

@_require_backoffice
def backoffice_dashboard(request: HttpRequest) -> HttpResponse:
    total = LoanRequest.objects.count()
    pending = LoanRequest.objects.filter(status=LoanRequestStatus.PENDING).count()
    validated = LoanRequest.objects.filter(status=LoanRequestStatus.VALIDATED).count()
    rejected = LoanRequest.objects.filter(status=LoanRequestStatus.REJECTED).count()

    qs = LoanRequest.objects.select_related("customer", "account").order_by("-creation_date")

    f_status = request.GET.get("status", "").strip()
    f_customer = request.GET.get("customer", "").strip()
    f_type = request.GET.get("loan_type", "").strip()
    f_date_from = request.GET.get("date_from", "").strip()
    f_date_to = request.GET.get("date_to", "").strip()

    if f_status:
        qs = qs.filter(status=f_status)
    if f_customer:
        qs = qs.filter(
            models.Q(customer__first_name__icontains=f_customer)
            | models.Q(customer__last_name__icontains=f_customer)
            | models.Q(account__email__icontains=f_customer)
        )
    if f_type:
        qs = qs.filter(loan_type=f_type)
    if f_date_from:
        qs = qs.filter(creation_date__date__gte=f_date_from)
    if f_date_to:
        qs = qs.filter(creation_date__date__lte=f_date_to)

    return render(request, "loanwise/backoffice/dashboard.html", {
        "total": total,
        "pending": pending,
        "validated": validated,
        "rejected": rejected,
        "recent_requests": qs[:50],
        "status_choices": LoanRequestStatus.choices,
        "type_choices": LoanType.choices,
        "f_status": f_status,
        "f_customer": f_customer,
        "f_type": f_type,
        "f_date_from": f_date_from,
        "f_date_to": f_date_to,
        "filtered_count": qs.count(),
    })


@_require_backoffice
def backoffice_loan_requests(request: HttpRequest) -> HttpResponse:
    status_filter = request.GET.get("status", "")
    qs = LoanRequest.objects.select_related("customer", "account").order_by("-creation_date")
    if status_filter:
        qs = qs.filter(status=status_filter)
    return render(request, "loanwise/backoffice/loan_requests.html", {
        "loan_requests": qs,
        "status_filter": status_filter,
        "status_choices": LoanRequestStatus.choices,
    })


@_require_backoffice
def backoffice_loan_request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    lr = get_object_or_404(LoanRequest, pk=pk)
    documents = lr.documents.all().order_by("-created_at")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update_status":
            new_status = request.POST.get("status")
            if new_status in dict(LoanRequestStatus.choices):
                lr.status = new_status
                lr.save(update_fields=["status", "modification_date"])
                # Create/update notification
                Notification.objects.update_or_create(
                    loan_request=lr,
                    defaults={"read": False},
                )
                messages.success(request, _("Status updated."))
        return redirect("backoffice_loan_request_detail", pk=pk)

    return render(request, "loanwise/backoffice/loan_request_detail.html", {
        "loan_request": lr,
        "documents": documents,
        "status_choices": LoanRequestStatus.choices,
    })


def _sync_eligibility_condition_index(condition: EligibilityCondition) -> None:
    """Extract text, refresh Chroma / keyword index (same behaviour as Django admin)."""
    if condition.file:
        extracted = extract_text_from_file(condition.file.path)
        EligibilityCondition.objects.filter(pk=condition.pk).update(extracted_text=extracted)
        condition.refresh_from_db()
    full = condition.searchable_blob()
    if condition.is_active and full.strip():
        index_eligibility_source(condition.pk, condition.title or "", full)
        EligibilityCondition.objects.filter(pk=condition.pk).update(last_indexed_at=timezone.now())
    else:
        delete_eligibility_source_index(condition.pk)


def _uploaded_file_is_pdf(f) -> bool:
    name = (getattr(f, "name", "") or "").lower()
    if not name.endswith(".pdf"):
        return False
    ct = (getattr(f, "content_type", "") or "").lower()
    if not ct:
        return True
    return "pdf" in ct or ct == "application/octet-stream"


@_require_backoffice
@require_http_methods(["GET", "POST"])
def backoffice_eligibility_conditions(request: HttpRequest) -> HttpResponse:
    """Upload PDF policy documents that drive eligibility guidance (RAG)."""
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "upload":
            f = request.FILES.get("file")
            if not f:
                messages.error(request, _("Please choose a PDF file to upload."))
            elif not _uploaded_file_is_pdf(f):
                messages.error(request, _("Only PDF files are accepted."))
            else:
                cond = EligibilityCondition.objects.create(
                    file=f,
                    is_active=True,
                )
                _sync_eligibility_condition_index(cond)
                messages.success(request, _("Criteria document added and indexed."))

        elif action == "delete":
            cid = request.POST.get("condition_id")
            cond = EligibilityCondition.objects.filter(pk=cid).first()
            if cond:
                delete_eligibility_source_index(cond.pk)
                if cond.file:
                    cond.file.delete(save=False)
                cond.delete()
                messages.success(request, _("Criteria document deleted."))

        return redirect("backoffice_eligibility_conditions")

    conditions = EligibilityCondition.objects.all().order_by("-created_at")
    return render(request, "loanwise/backoffice/eligibility_conditions.html", {
        "conditions": conditions,
    })


@_require_backoffice
@require_http_methods(["GET", "POST"])
def backoffice_document_requirements(request: HttpRequest) -> HttpResponse:
    """Manage DocumentRequirement records: create and delete only."""
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create":
            name = request.POST.get("name", "").strip()
            is_mandatory = request.POST.get("is_mandatory") == "on"
            if name:
                DocumentRequirement.objects.create(name=name, is_mandatory=is_mandatory)
                messages.success(request, _("Requirement added."))
            else:
                messages.error(request, _("Name is required."))

        elif action == "delete":
            req_id = request.POST.get("req_id")
            DocumentRequirement.objects.filter(pk=req_id).delete()
            messages.success(request, _("Requirement deleted."))

        return redirect("backoffice_document_requirements")

    requirements = DocumentRequirement.objects.order_by("name")
    return render(request, "loanwise/backoffice/document_requirements.html", {
        "requirements": requirements,
    })


@_require_backoffice
@require_http_methods(["GET", "POST"])
def backoffice_agents(request: HttpRequest) -> HttpResponse:
    """
    Manage Back-Office agent accounts from a JSON file.
    File: BACKOFFICE_AGENTS_FILE setting (default: backoffice_agents.json in BASE_DIR).
    """
    agents_file = getattr(
        django_settings,
        "BACKOFFICE_AGENTS_FILE",
        os.path.join(django_settings.BASE_DIR, "backoffice_agents.json"),
    )

    def _load():
        if os.path.exists(agents_file):
            with open(agents_file, encoding="utf-8") as f:
                return json.load(f)
        return []

    def _save(data):
        with open(agents_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create":
            email = request.POST.get("email", "").lower().strip()
            first_name = request.POST.get("first_name", "").strip()
            last_name = request.POST.get("last_name", "").strip()
            cin = request.POST.get("cin_number", "").strip()
            password = request.POST.get("password", "")

            if not email or not password:
                messages.error(request, _("Email and password are required."))
            elif Account.objects.filter(email__iexact=email).exists():
                messages.error(request, _("An account with this email already exists."))
            else:
                username = email.split("@")[0]
                base = username
                i = 1
                while Account.objects.filter(username=username).exists():
                    username = f"{base}{i}"
                    i += 1

                bo = BackOffice.objects.create(
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    cin_number=cin,
                )
                user = Account(
                    email=email,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    account_type=AccountType.BACKOFFICE,
                    email_verified=True,
                    back_office=bo,
                )
                user.set_password(password)
                user.save()

                # Also record in JSON file
                agents = _load()
                agents.append({
                    "id": user.pk,
                    "email": email,
                    "first_name": first_name,
                    "last_name": last_name,
                    "cin_number": cin,
                })
                _save(agents)
                messages.success(request, _("Back-Office agent created."))

        elif action == "update":
            agent_id = request.POST.get("agent_id")
            first_name = request.POST.get("first_name", "").strip()
            last_name = request.POST.get("last_name", "").strip()
            new_password = request.POST.get("new_password", "").strip()
            user = Account.objects.filter(pk=agent_id, account_type=AccountType.BACKOFFICE).first()
            if user:
                user.first_name = first_name
                user.last_name = last_name
                if new_password:
                    user.set_password(new_password)
                user.save()
                if user.back_office:
                    user.back_office.first_name = first_name
                    user.back_office.last_name = last_name
                    user.back_office.save(update_fields=["first_name", "last_name"])
                # Sync JSON
                agents = _load()
                for a in agents:
                    if a.get("id") == int(agent_id):
                        a["first_name"] = first_name
                        a["last_name"] = last_name
                        break
                _save(agents)
                messages.success(request, _("Agent updated."))

        elif action == "delete":
            agent_id = request.POST.get("agent_id")
            user = Account.objects.filter(pk=agent_id, account_type=AccountType.BACKOFFICE).first()
            if user and user.pk != request.user.pk:
                if user.back_office:
                    user.back_office.delete()
                user.delete()
                agents = _load()
                agents = [a for a in agents if a.get("id") != int(agent_id)]
                _save(agents)
                messages.success(request, _("Agent deleted."))
            else:
                messages.error(request, _("Cannot delete your own account."))

        return redirect("backoffice_agents")

    agents = Account.objects.filter(account_type=AccountType.BACKOFFICE).select_related("back_office").order_by("email")
    return render(request, "loanwise/backoffice/agents.html", {
        "agents": agents,
        "agents_file": agents_file,
    })


# ── Legacy / compat ───────────────────────────────────────────────────────────

@login_required
def application_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Legacy redirect to new customer detail view."""
    if _is_backoffice(request.user):
        return redirect("backoffice_loan_request_detail", pk=pk)
    return redirect("loan_request_detail", pk=pk)


@require_http_methods(["GET", "POST"])
def verify_email_page(request: HttpRequest) -> HttpResponse:
    from core.email_verification_service import verify_email_with_code
    if request.user.is_authenticated and request.user.email_verified:
        return redirect("customer_dashboard")
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip()
        code = (request.POST.get("code") or "").strip()
        ok, detail = verify_email_with_code(email, code)
        if ok:
            UserModel = get_user_model()
            user = UserModel.objects.filter(email__iexact=email.lower()).first()
            if user:
                login(request, user)
            messages.success(request, _("Email verified. Welcome!"))
            return redirect("customer_dashboard")
        messages.error(request, _("Invalid email or verification code."))
        return render(request, "loanwise/verify_email.html", {"prefill_email": email})
    prefill = request.user.email if request.user.is_authenticated else ""
    return render(request, "loanwise/verify_email.html", {"prefill_email": prefill})
