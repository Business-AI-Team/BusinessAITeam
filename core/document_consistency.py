"""
Cohérence dossier : compare les données déclarées (formulaire / profil) avec ce qui est
lisible sur les pièces (JSON d’analyse OpenAI). Détecte en particulier un écart de
revenu (fiche de paie vs revenu annuel déclaré) et des adresses différentes.
"""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal
from typing import Any

from datetime import date

from django.conf import settings

from core.currency_fx import convert_amount, format_money
from core.models import LoanApplication

logger = logging.getLogger(__name__)


def _parse_caption_json(caption: str | None) -> dict[str, Any]:
    if not caption or not str(caption).strip():
        return {}
    s = str(caption).strip()
    if s.startswith("```"):
        s = re.sub(r"^```\w*\n?", "", s)
        s = re.sub(r"\n```\s*$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return {}


def _get_extracted_fields(parsed: dict[str, Any]) -> dict[str, Any]:
    """Supporte ``extracted_fields`` imbriqué ou anciennes réponses plates."""
    ef = parsed.get("extracted_fields")
    if isinstance(ef, dict):
        return ef
    return {}


def _to_decimal(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        s = str(v).strip().replace(" ", "").replace("\u00a0", "")
        s = re.sub(r"[^\d,\.\-]", "", s)
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            parts = s.split(",")
            if len(parts) == 2 and len(parts[1]) <= 2:
                s = parts[0].replace(".", "") + "." + parts[1]
            else:
                s = s.replace(",", ".")
        return Decimal(s)
    except Exception:
        return None


def _document_currency(parsed: dict[str, Any], ef: dict[str, Any]) -> str:
    """Devise des montants extraits : le modèle place souvent ``document_currency`` à la racine du JSON, pas dans ``extracted_fields``."""
    c = (parsed.get("document_currency") or ef.get("document_currency") or "").upper().strip()
    if c in ("MGA", "EUR", "MUR"):
        return c
    return "EUR"


def _pay_period_year_month(ef: dict[str, Any]) -> str | None:
    """Retourne ``YYYY-MM`` si présent sur la pièce."""
    raw = ef.get("pay_period_year_month") or ef.get("payslip_month")
    if raw is None:
        return None
    s = str(raw).strip()
    m = re.match(r"^(\d{4})\D?(\d{1,2})", s)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    if 1 <= mo <= 12 and 2000 <= y <= 2100:
        return f"{y:04d}-{mo:02d}"
    return None


def _rolling_month_keys(count: int, reference_date=None) -> set[str]:
    """Les ``count`` derniers mois calendaires avant ``reference_date`` (ou aujourd'hui si None)."""
    from datetime import date as _date
    ref = reference_date or _date.today()
    if hasattr(ref, "date"):
        ref = ref.date()
    y, m = ref.year, ref.month
    out: set[str] = set()
    for _ in range(max(1, count)):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        out.add(f"{y:04d}-{m:02d}")
    return out


def _annual_equivalent_from_fields(ef: dict[str, Any]) -> Decimal | None:
    """Revenu annuel explicite ou mensuel net × 12."""
    annual = _to_decimal(ef.get("annual_income_amount"))
    if annual is not None and annual > 0:
        return annual
    monthly = _to_decimal(ef.get("monthly_net_amount"))
    if monthly is not None and monthly > 0:
        return (monthly * Decimal("12")).quantize(Decimal("0.01"))
    return None


def _norm_tokens(s: str) -> set[str]:
    s = re.sub(r"\s+", " ", (s or "").lower())
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    return {w for w in s.split() if len(w) > 2}


def _address_overlap(declared: str, on_doc: str) -> float:
    a, b = _norm_tokens(declared), _norm_tokens(on_doc)
    if not a or not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _doc_is_national_id_cin(document_type: str, doc_kind: str) -> bool:
    """Pièce d’identité (CIN / CNI / carte d’identité)."""
    dk = (doc_kind or "").strip().lower()
    if dk == "identity":
        return True
    dt = (document_type or "").lower()
    return any(
        x in dt
        for x in (
            "piece_identite",
            "piece d'identite",
            "cin",
            "cni",
            "carte_identite",
            "carte nationale",
            "id_card",
            "national_id",
            "identite nationale",
        )
    )


def _doc_is_proof_of_address(document_type: str, doc_kind: str) -> bool:
    """Justificatif de domicile."""
    dk = (doc_kind or "").strip().lower()
    if dk == "address":
        return True
    dt = (document_type or "").lower()
    return any(
        x in dt
        for x in (
            "justificatif",
            "domicile",
            "proof_of_address",
            "attestation",
            "hebergement",
            "facture",
            "utility",
        )
    )


def _is_income_document(document_type: str, summary: str) -> bool:
    t = f"{document_type} {summary}".lower()
    keys = (
        "payslip",
        "fiche de paie",
        "fiche_de_paie",
        "bulletin",
        "salaire",
        "pay slip",
        "payroll",
        "stub",
        "releve",
    )
    return any(k in t for k in keys)


def _fallback_income_from_summary(summary: str) -> Decimal | None:
    """Heuristique si le JSON n’a pas de champs structurés (anciennes analyses)."""
    if not summary or len(summary) < 20:
        return None
    # Net mensuel typique FR: "xxx,xx €" ou "xxx EUR"
    best: Decimal | None = None
    for m in re.finditer(
        r"(?:net|mensuel|mois)[^\d]{0,24}(\d[\d\s\.,]{3,})\s*(?:€|eur|mga|ar)",
        summary,
        re.I,
    ):
        d = _to_decimal(m.group(1))
        if d and d > 100:
            ann = d * Decimal("12")
            if best is None or ann > best:
                best = ann
    return best


def _check_payslip_month_rules(application: LoanApplication) -> list[dict[str, Any]]:
    """
    Vérifie :
    1. Tous les bulletins sont dans la fenêtre des 3 derniers mois avant la création de la demande.
    2. Pas de mois dupliqués (deux bulletins du même mois = redondance).

    La fenêtre est calculée à partir de ``application.created_at`` (date de soumission du dossier),
    pas de ``date.today()``, pour éviter que les mois valides ne glissent pendant l'instruction.
    """
    from core.document_requirement_service import requirements_for_application

    DEFAULT_WINDOW = 3
    reference_date = getattr(application, "created_at", None)
    out: list[dict[str, Any]] = []

    # ── Collecte des bulletins par requirement configuré ──────────────────────
    req_handled: set[int] = set()
    for req in requirements_for_application(application):
        w = getattr(req, "payslip_distinct_months_window", None)
        if not w or w < 1:
            continue
        req_handled.add(req.pk)
        min_f = max(1, getattr(req, "min_files", 1) or 1)
        docs = [
            d
            for d in application.documents.all().order_by("id")
            if d.requirement_id == req.pk and d.deleted_at is None
        ]
        if len(docs) < min_f:
            continue
        out.extend(_validate_payslip_docs(docs, w, min_f, label=req.code, reference_date=reference_date))

    # ── Fallback : bulletins sans requirement configuré (fenêtre 3 mois) ──────
    all_payslip_docs = [
        d
        for d in application.documents.all().order_by("id")
        if d.deleted_at is None
        and (d.requirement_id is None or d.requirement_id not in req_handled)
        and _is_income_document(
            _get_doc_type_from_analysis(d),
            _get_summary_from_analysis(d),
        )
    ]
    if all_payslip_docs:
        out.extend(_validate_payslip_docs(all_payslip_docs, DEFAULT_WINDOW, 1, label="payslip", reference_date=reference_date))

    return out


def _get_doc_type_from_analysis(doc: Any) -> str:
    ar = doc.analysis_result or {}
    cap = ar.get("caption") or ""
    parsed = _parse_caption_json(cap)
    return (parsed.get("document_type") or "").strip()


def _get_summary_from_analysis(doc: Any) -> str:
    ar = doc.analysis_result or {}
    cap = ar.get("caption") or ""
    parsed = _parse_caption_json(cap)
    return (parsed.get("visible_text_summary") or "").strip()


def _validate_payslip_docs(
    docs: list[Any],
    window: int,
    min_distinct: int,
    label: str,
    reference_date=None,
) -> list[dict[str, Any]]:
    """Vérifie la fenêtre temporelle et l'unicité des mois pour une liste de bulletins.

    ``reference_date`` : date de création de la demande (datetime ou date).
    Les mois autorisés sont les ``window`` mois PRÉCÉDANT cette date.
    """
    out: list[dict[str, Any]] = []
    allowed = _rolling_month_keys(window, reference_date)
    yms: list[str] = []
    missing_date = 0

    for d in docs:
        ar = d.analysis_result or {}
        if ar.get("skipped") or (ar.get("engine") or "") == "fallback":
            continue
        cap = ar.get("caption") or ""
        parsed = _parse_caption_json(cap)
        ef = _get_extracted_fields(parsed)
        ym = _pay_period_year_month(ef)
        if ym:
            yms.append(ym)
        else:
            missing_date += 1

    if not yms and missing_date == 0:
        return out

    if missing_date > 0:
        out.append({
            "code": "payslip_dates_incomplete",
            "severity": "medium",
            "detail_fr": (
                f"Les dates de période de certains bulletins ({label}) sont illisibles ou absentes. "
                f"La période doit être dans les {window} derniers mois calendaires."
            ),
            "detail_en": (
                f"Payslip period dates are missing or unreadable for some uploads ({label}). "
                f"The period must fall within the last {window} calendar months."
            ),
        })

    if not yms:
        return out

    distinct = set(yms)

    # Doublons (même mois, plusieurs fichiers)
    if len(yms) != len(distinct):
        duplicates = sorted(ym for ym in distinct if yms.count(ym) > 1)
        out.append({
            "code": "payslip_months_not_distinct",
            "severity": "high",
            "detail_fr": (
                f"Plusieurs bulletins ({label}) couvrent le même mois calendaire. "
                f"Chaque mois doit être représenté une seule fois. "
                f"Mois en doublon : {', '.join(duplicates)}."
            ),
            "detail_en": (
                f"Multiple payslips ({label}) cover the same calendar month. "
                f"Each month must appear only once. "
                f"Duplicate months: {', '.join(duplicates)}."
            ),
        })

    # Mois hors fenêtre glissante
    outside = sorted(distinct - allowed)
    if outside:
        out.append({
            "code": "payslip_months_outside_window",
            "severity": "high",
            "detail_fr": (
                f"Un ou plusieurs bulletins ({label}) sont hors des {window} derniers mois autorisés "
                f"({', '.join(sorted(allowed))}). "
                f"Mois hors fenêtre : {', '.join(outside)}."
            ),
            "detail_en": (
                f"One or more payslips ({label}) fall outside the last {window} authorized months "
                f"({', '.join(sorted(allowed))}). "
                f"Out-of-window months: {', '.join(outside)}."
            ),
        })

    return out


def _name_tokens(s: str) -> set[str]:
    """Tokenise un nom en minuscules, mots >= 2 caractères."""
    s = re.sub(r"\s+", " ", (s or "").lower().strip())
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    return {w for w in s.split() if len(w) >= 2}


def _check_identity_name_match(application: LoanApplication) -> list[dict[str, Any]]:
    """
    Compare le nom extrait sur les CIN/passeports et les bulletins de salaire
    avec le nom déclaré dans le profil client. Génère une issue si trop différent.
    """
    cust = getattr(application, "customer", None)
    if cust is None:
        return []
    declared = f"{getattr(cust, 'first_name', '') or ''} {getattr(cust, 'last_name', '') or ''}".strip()
    if not declared:
        return []

    declared_tokens = _name_tokens(declared)
    if not declared_tokens:
        return []

    out: list[dict[str, Any]] = []
    checked_id = False
    checked_payslip = False

    for doc in application.documents.all().order_by("id"):
        ar = doc.analysis_result or {}
        if ar.get("skipped") or (ar.get("engine") or "") == "fallback":
            continue
        cap = ar.get("caption") or ""
        parsed = _parse_caption_json(cap)
        if not parsed:
            continue
        ef = _get_extracted_fields(parsed)
        doc_type = (parsed.get("document_type") or "").strip()
        dk = getattr(doc, "kind", None) or ""

        is_id = _doc_is_national_id_cin(doc_type, dk)
        is_payslip = _is_income_document(doc_type, (parsed.get("visible_text_summary") or ""))

        # Préférer les champs spécialisés, puis le générique
        name_on_doc: str | None = None
        if is_id:
            name_on_doc = (ef.get("id_holder_name") or ef.get("person_full_name") or "").strip() or None
        elif is_payslip:
            name_on_doc = (ef.get("payslip_employee_name") or ef.get("person_full_name") or "").strip() or None

        if not name_on_doc or len(name_on_doc) < 3:
            continue

        doc_tokens = _name_tokens(name_on_doc)
        if not doc_tokens:
            continue

        inter = len(declared_tokens & doc_tokens)
        union = len(declared_tokens | doc_tokens)
        overlap = inter / union if union else 0.0

        if overlap < 0.4:
            fname = doc.original_filename or str(doc.pk)
            if is_id and not checked_id:
                checked_id = True
                out.append({
                    "code": "identity_name_mismatch",
                    "severity": "high",
                    "document_filename": fname,
                    "declared_name": declared,
                    "detected_name": name_on_doc,
                    "detail_fr": (
                        f"Le nom sur la pièce d'identité ({name_on_doc!r}, fichier : {fname}) "
                        f"ne correspond pas au nom déclaré dans le profil ({declared!r}). "
                        "Vérifiez que la pièce d'identité appartient bien au demandeur."
                    ),
                    "detail_en": (
                        f"The name on the identity document ({name_on_doc!r}, file: {fname}) "
                        f"does not match the declared profile name ({declared!r}). "
                        "Please ensure the ID belongs to the loan applicant."
                    ),
                })
            elif is_payslip and not checked_payslip:
                checked_payslip = True
                out.append({
                    "code": "payslip_name_mismatch",
                    "severity": "high",
                    "document_filename": fname,
                    "declared_name": declared,
                    "detected_name": name_on_doc,
                    "detail_fr": (
                        f"Le nom du salarié sur le bulletin de salaire ({name_on_doc!r}, fichier : {fname}) "
                        f"ne correspond pas au nom déclaré dans le profil ({declared!r}). "
                        "Vérifiez que le bulletin appartient bien au demandeur."
                    ),
                    "detail_en": (
                        f"The employee name on the payslip ({name_on_doc!r}, file: {fname}) "
                        f"does not match the declared profile name ({declared!r}). "
                        "Please ensure the payslip belongs to the loan applicant."
                    ),
                })

    return out


def profile_declared_annual_income(application: LoanApplication) -> Decimal:
    """Revenu saisi au profil / dossier uniquement (pas l’estimation issue des pièces)."""
    c = getattr(application, "customer", None)
    if c is not None and c.annual_income is not None and c.annual_income > 0:
        return c.annual_income
    if application.annual_income is not None and application.annual_income > 0:
        return application.annual_income
    return Decimal("0")


def best_annual_income_from_payslips(application: LoanApplication) -> Decimal:
    """
    Meilleure équivalence annuelle dérivée des pièces revenu (net mensuel × 12 ou net annuel),
    convertie dans la devise du profil pour comparaison avec le reste du dossier.
    """
    cust = getattr(application, "customer", None)
    target_ccy = (getattr(cust, "income_currency", None) or "EUR") if cust else "EUR"
    best: Decimal | None = None
    for doc in application.documents.all().order_by("id"):
        ar = doc.analysis_result or {}
        if ar.get("skipped"):
            continue
        if (ar.get("engine") or "") == "fallback":
            continue
        cap = ar.get("caption") or ""
        parsed = _parse_caption_json(cap)
        if not parsed and cap and len(cap) > 30:
            parsed = {"visible_text_summary": cap, "document_type": ""}
        elif not parsed:
            continue
        ef = _get_extracted_fields(parsed)
        doc_type = (parsed.get("document_type") or "").strip()
        summary = (parsed.get("visible_text_summary") or "").strip()
        dcurr = _document_currency(parsed, ef)
        detected_annual = _annual_equivalent_from_fields(ef)
        if detected_annual is None and _is_income_document(doc_type, summary):
            detected_annual = _fallback_income_from_summary(summary)
            dcurr = "EUR"
        if detected_annual is not None and detected_annual > 0:
            conv = convert_amount(detected_annual, dcurr, target_ccy)
            if best is None or conv > best:
                best = conv
    return best.quantize(Decimal("0.01")) if best is not None else Decimal("0")


def check_application_document_consistency(application: LoanApplication) -> dict[str, Any]:
    """
    Retourne ``issues`` (liste), ``score_penalty_points`` (0–max setting), et métadonnées.
    """
    tol_pct = Decimal(str(getattr(settings, "LOANWISE_DOC_INCOME_TOLERANCE_PERCENT", 15)))
    max_pen = Decimal(str(getattr(settings, "LOANWISE_CONSISTENCY_MAX_SCORE_PENALTY", 35)))

    cust = getattr(application, "customer", None)
    profile_income = profile_declared_annual_income(application)
    declared_currency = (getattr(cust, "income_currency", None) or "EUR") if cust else "EUR"
    declared_address = (getattr(cust, "address", None) or "").strip() if cust else ""

    issues: list[dict[str, Any]] = []
    issues.extend(_check_payslip_month_rules(application))
    issues.extend(_check_identity_name_match(application))
    penalty = Decimal("0")
    penalty += Decimal("12") * sum(1 for i in issues if i.get("code") == "payslip_months_not_distinct")
    penalty += Decimal("10") * sum(1 for i in issues if i.get("code") == "payslip_months_outside_window")
    penalty += Decimal("6") * sum(1 for i in issues if i.get("code") == "payslip_dates_incomplete")
    penalty += Decimal("20") * sum(1 for i in issues if i.get("code") == "identity_name_mismatch")
    penalty += Decimal("15") * sum(1 for i in issues if i.get("code") == "payslip_name_mismatch")

    best_detected_annual: Decimal | None = None
    best_doc_name = ""
    worst_income_diff: tuple[Decimal, str, Decimal, str] | None = None  # diff_pct, filename, detected, severity_weight
    addr_mm_national_id = False
    addr_mm_proof_of_address = False
    addr_mm_other = False

    for doc in application.documents.all().order_by("id"):
        ar = doc.analysis_result or {}
        if ar.get("skipped"):
            continue
        if (ar.get("engine") or "") == "fallback":
            continue
        cap = ar.get("caption") or ""
        parsed = _parse_caption_json(cap)
        if not parsed and cap and len(cap) > 30:
            parsed = {"visible_text_summary": cap, "document_type": ""}
        elif not parsed:
            continue
        ef = _get_extracted_fields(parsed)
        doc_type = (parsed.get("document_type") or "").strip()
        summary = (parsed.get("visible_text_summary") or "").strip()

        dcurr = _document_currency(parsed, ef)
        detected_annual = _annual_equivalent_from_fields(ef)
        if detected_annual is None and _is_income_document(doc_type, summary):
            detected_annual = _fallback_income_from_summary(summary)
            dcurr = "EUR"

        income_fields = bool(ef.get("monthly_net_amount") is not None or ef.get("annual_income_amount") is not None)
        treat_as_income = income_fields or _is_income_document(doc_type, summary)

        detected_for_compare: Decimal | None = None
        if detected_annual is not None and detected_annual > 0:
            detected_for_compare = convert_amount(detected_annual, dcurr, declared_currency)
            if best_detected_annual is None or detected_for_compare > best_detected_annual:
                best_detected_annual = detected_for_compare
                best_doc_name = doc.original_filename or str(doc.pk)

        # Adresse : règle métier — seul le justificatif de domicile (proof of address) doit
        # correspondre à l'adresse du profil. La CIN / passeport peut avoir une adresse
        # différente (adresse de naissance, ancienne adresse) sans que cela bloque le dossier.
        addr_doc = (ef.get("address_on_document") or ef.get("address_lines") or "").strip()
        if isinstance(addr_doc, list):
            addr_doc = ", ".join(str(x) for x in addr_doc)
        if declared_address and addr_doc and len(addr_doc) > 8:
            dk = getattr(doc, "kind", None) or ""
            is_id_doc = _doc_is_national_id_cin(doc_type, dk)
            if not is_id_doc:
                # CIN et passeports exclus de la vérification d'adresse
                overlap = _address_overlap(declared_address, addr_doc)
                if overlap < float(getattr(settings, "LOANWISE_ADDRESS_OVERLAP_MIN", 0.22)):
                    if _doc_is_proof_of_address(doc_type, dk):
                        addr_mm_proof_of_address = True
                    elif not _is_income_document(doc_type, summary):
                        # document non classifié (ni revenu) → traité comme justificatif
                        addr_mm_proof_of_address = True
                    else:
                        addr_mm_other = True

        # Revenu : un seul cas « pire » retenu pour éviter les doublons
        if (
            treat_as_income
            and detected_for_compare is not None
            and detected_for_compare > 0
            and profile_income
            and profile_income > 0
        ):
            base = max(detected_for_compare, profile_income)
            diff_pct = abs(detected_for_compare - profile_income) / base * Decimal("100")
            if diff_pct > tol_pct and (worst_income_diff is None or diff_pct > worst_income_diff[0]):
                worst_income_diff = (
                    diff_pct,
                    doc.original_filename or str(doc.pk),
                    detected_for_compare,
                    doc_type,
                )

    # CIN/passeport : jamais un motif de blocage d'adresse.
    # Regle metier : seul le justificatif de domicile (proof of address) est compare
    # a l'adresse du profil. La CIN ou le passeport peut avoir une adresse differente
    # (naissance, ancienne adresse) sans que le dossier soit bloque.
    if addr_mm_proof_of_address or addr_mm_other:
        if addr_mm_proof_of_address and not addr_mm_other:
            detail_fr = (
                "L'adresse de votre profil ne correspond pas a celle figurant sur votre "
                "justificatif de domicile. Note : l'adresse sur la CIN ou le passeport "
                "n'est pas verifiee ici — seul le justificatif de domicile est compare "
                "a l'adresse declaree."
            )
            detail_en = (
                "Your profile address does not match the address on your proof-of-address "
                "document. Note: the address on a national ID (CIN) or passport is not "
                "checked here — only the proof of address is compared to your declared address."
            )
        elif addr_mm_other and not addr_mm_proof_of_address:
            detail_fr = (
                "L'adresse de votre profil ne correspond pas a une adresse lisible sur une "
                "piece fournie (par ex. adresse employeur sur un bulletin de paie). "
                "Fournissez un justificatif de domicile recent a votre nom reprenant "
                "l'adresse declaree dans votre profil."
            )
            detail_en = (
                "Your profile address does not match an address on an uploaded document "
                "(e.g. employer address on a payslip). "
                "Provide a recent proof of address in your name matching your declared home address."
            )
        else:
            detail_fr = (
                "L'adresse de votre profil ne correspond pas a celle lisible sur le "
                "justificatif de domicile ou sur une autre piece. "
                "Rappel : l'adresse sur la CIN ou le passeport n'est pas verifiee ici."
            )
            detail_en = (
                "Your profile address does not match the address on your proof of address "
                "or another document. "
                "Reminder: the address on a CIN or passport is not checked against your declared home."
            )
        issues.append(
            {
                "code": "address_mismatch",
                "severity": "medium",
                "document_filename": None,
                "declared_address": declared_address[:500] if declared_address else "",
                "detected_address": "",
                "overlap_ratio": None,
                "detail_fr": detail_fr,
                "detail_en": detail_en,
            }
        )
        penalty += Decimal("8")

    if worst_income_diff is not None:
        diff_pct, fname, detected_conv, _dt = worst_income_diff
        sev = "high" if diff_pct > tol_pct * Decimal("2") else "medium"
        decl_s = format_money(profile_income, declared_currency)
        det_s = format_money(detected_conv, declared_currency)
        issues.append(
            {
                "code": "income_mismatch",
                "severity": sev,
                "document_filename": fname,
                "declared_annual_income": decl_s,
                "detected_annual_equivalent": det_s,
                "difference_percent": str(diff_pct.quantize(Decimal("0.1"))),
                "detail_fr": (
                    f"Écart entre le revenu annuel déclaré ({decl_s}) et les pièces, après conversion de devise "
                    f"vers {declared_currency} ({det_s} — fichier : {fname})."
                ),
                "detail_en": (
                    f"Gap between declared annual income ({decl_s}) and documents after FX conversion to "
                    f"{declared_currency} ({det_s}; file: {fname})."
                ),
            }
        )
        penalty += Decimal("28") if sev == "high" else Decimal("15")

    penalty = min(penalty, max_pen)
    return {
        "issues": issues,
        "score_penalty_points": float(penalty),
        "best_detected_annual_income": str(best_detected_annual) if best_detected_annual else None,
        "best_income_source_filename": best_doc_name or None,
        "income_tolerance_percent": float(tol_pct),
        "address_mismatch_sources": {
            # national_id_card is intentionally excluded from the check:
            # CIN/passport address is not required to match the profile address.
            "proof_of_address": bool(addr_mm_proof_of_address),
            "other_document": bool(addr_mm_other),
        },
    }


def apply_score_penalty_for_consistency(base: Decimal, consistency: dict[str, Any]) -> Decimal:
    try:
        pts = Decimal(str(consistency.get("score_penalty_points") or 0))
    except Exception:
        pts = Decimal("0")
    out = base - pts
    if out < 0:
        out = Decimal("0")
    return out.quantize(Decimal("0.01"))
