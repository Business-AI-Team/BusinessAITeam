"""
Assistant conversationnel (OpenAI) pour expliquer un dossier ou donner des pistes d'amélioration.

Trois sources de contexte injectées dans chaque échange :
  SOURCE 1 — Règles de l'institution (RAG) : chunks sémantiques depuis Chroma / PDF sur disque
  SOURCE 2 — Profil utilisateur          : données du customer + demande de prêt + analyse d'éligibilité
  SOURCE 3 — Documents fournis           : résumés des pièces justificatives uploadées
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from core.models import LoanApplication
from core.openai_config import get_openai_api_key

logger = logging.getLogger(__name__)


# ── SOURCE 2 : profil utilisateur + demande ───────────────────────────────────

def _build_profile_context(user_email: str, application: LoanApplication | None, lang: str) -> str:
    """Données du customer et de la demande de prêt (sans les documents)."""
    parts: list[str] = [f"Email: {user_email}"]

    if application is None:
        return "\n".join(parts)

    cust = getattr(application, "customer", None)
    if cust:
        parts.append(f"Nom: {cust.first_name} {cust.last_name}".strip())
        parts.append(f"Adresse dans le profil: {cust.address or '(non renseignée)'}")
        parts.append(f"Pays: {cust.country or '(non renseigné)'}")
        parts.append(f"Téléphone: {cust.phone or '(non renseigné)'}")
        parts.append(f"N° pièce d'identité (CIN): {cust.id_card or '(non renseigné)'}")
        if cust.annual_income and cust.annual_income > 0:
            parts.append(f"Revenu annuel déclaré: {cust.annual_income} {cust.income_currency}")
        else:
            parts.append("Revenu annuel déclaré: non renseigné")

    parts.append(f"Référence demande: {application.reference}")
    parts.append(f"Type de prêt: {application.loan_type}")
    parts.append(f"Statut: {application.status}")
    if application.amount_requested:
        parts.append(f"Montant demandé: {application.amount_requested} {application.amount_currency}")
    else:
        parts.append("Montant demandé: non renseigné")
    parts.append(f"Durée: {application.term_months} mois")
    if application.eligibility_score is not None:
        parts.append(f"Score d'éligibilité: {application.eligibility_score}")
    else:
        parts.append("Score d'éligibilité: non calculé")

    roi = application.roi_summary or {}
    detail = roi.get("eligibility_detail") or {}
    if isinstance(detail, dict) and not detail.get("error"):
        summary = detail.get("summary_fr" if lang.startswith("fr") else "summary_en") or ""
        if summary:
            parts.append(f"Résumé de l'évaluation: {summary[:800]}")
        dc = detail.get("document_consistency") or {}
        addr_src = dc.get("address_mismatch_sources") or {}
        for issue in dc.get("issues") or []:
            msg = issue.get("detail_fr" if lang.startswith("fr") else "detail_en") or ""
            if msg:
                parts.append(f"Problème de cohérence ({issue.get('code', '?')}): {msg}")
        # Only report proof-of-address mismatch; CIN/passport address is irrelevant by policy
        if addr_src.get("proof_of_address"):
            parts.append("Source du problème d'adresse: justificatif de domicile")
        financial_bullets = detail.get("financial_bullets_fr" if lang.startswith("fr") else "financial_bullets_en") or []
        for b in financial_bullets[:4]:
            parts.append(f"Détail financier: {b}")

    return "\n".join(parts)


# ── SOURCE 3 : documents fournis par l'utilisateur ────────────────────────────

def _build_documents_context(application: LoanApplication | None) -> str:
    """Résumés des pièces justificatives analysées."""
    if application is None:
        return "Aucun document fourni."

    docs = application.documents.all().order_by("id")
    lines: list[str] = []
    for d in docs:
        ar = d.analysis_result or {}
        if ar.get("skipped") or (ar.get("engine") or "") == "fallback":
            continue
        caption = (ar.get("caption") or "").strip()
        summary = caption[:400] if caption else "(analyse non disponible)"
        lines.append(f"- [{d.kind}] {d.original_filename}: {summary}")

    return "\n".join(lines) if lines else "Aucun document analysé pour le moment."


# ── SOURCE 1 : règles RAG (institution) ───────────────────────────────────────

def _build_rag_context(user_message: str, loan_type: str) -> str:
    """
    Retrieval sémantique depuis Chroma (LangChain).
    Fallback keyword → fallback lecture fichier sur disque si Chroma vide.
    """
    from core.models import EligibilityKnowledgeSource
    from core.rag_eligibility import extract_text_from_file, retrieve_rule_chunks

    # Niveau 1 : similarity search Chroma
    chunks = retrieve_rule_chunks(user_message, loan_type, k=5)
    if chunks:
        return "\n\n".join(f"[extrait {i}]\n{c[:2000]}" for i, c in enumerate(chunks, 1))

    # Niveau 2 & 3 : searchable_blob() ou lecture directe du fichier sur disque
    rag_sources = EligibilityKnowledgeSource.objects.filter(is_active=True).order_by("id")
    parts: list[str] = []
    for src in rag_sources:
        blob = (src.searchable_blob() or "").strip()
        if not blob and src.file:
            try:
                blob = (extract_text_from_file(src.file.path) or "").strip()
            except Exception:
                blob = ""
        if blob:
            title = (src.title or f"source_{src.pk}")[:200]
            parts.append(f"[{title}]\n{blob[:3000]}")

    return "\n\n".join(parts) if parts else "(aucune règle disponible)"


# ── Point d'entrée principal ──────────────────────────────────────────────────

def _build_role_instructions(
    actor_role: str,
    application: LoanApplication | None,
    lang: str,
) -> str:
    """Génère le bloc d'identité et de perspective selon le rôle de l'interlocuteur."""
    is_fr = lang.startswith("fr")
    cust = getattr(application, "customer", None) if application else None
    customer_name = f"{cust.first_name} {cust.last_name}".strip() if cust else "le client"

    if actor_role == "backoffice":
        if is_fr:
            return (
                "Tu parles actuellement avec un agent du BACKOFFICE (employé de l'institution bancaire). "
                f"Cet agent consulte le dossier du client « {customer_name} » dont il n'est PAS le propriétaire. "
                "Adopte un ton professionnel et analytique. "
                "Tu peux fournir des détails techniques complets (scores, ratios, incohérences de documents, règles internes). "
                "Aide l'agent à comprendre pourquoi la demande a été acceptée ou refusée, "
                "et quelles actions il peut entreprendre (demande de pièces complémentaires, validation manuelle, etc.)."
            )
        return (
            "You are currently speaking with a BACKOFFICE agent (bank staff member). "
            f"This agent is reviewing the application of customer « {customer_name} », who is NOT the one chatting. "
            "Use a professional and analytical tone. "
            "Provide full technical details (scores, ratios, document inconsistencies, internal rules). "
            "Help the agent understand why the application was approved or rejected, "
            "and what actions they can take (request additional documents, manual validation, etc.)."
        )
    else:
        # customer / default
        if is_fr:
            return (
                f"Tu parles actuellement avec « {customer_name} », le CLIENT propriétaire de cette demande de prêt. "
                "Adopte un ton bienveillant, clair et pédagogique. "
                "Explique les décisions en termes simples, sans jargon technique excessif. "
                "Guide le client sur ce qu'il peut faire pour améliorer son dossier. "
                "Ne divulgue pas de détails internes réservés au backoffice (règles de scoring internes, seuils bruts, etc.)."
            )
        return (
            f"You are currently speaking with « {customer_name} », the CUSTOMER who owns this loan application. "
            "Use a warm, clear, and educational tone. "
            "Explain decisions in simple terms, avoiding excessive technical jargon. "
            "Guide the customer on how to improve their application. "
            "Do not disclose internal backoffice details (raw scoring rules, internal thresholds, etc.)."
        )


def build_assistant_reply(
    *,
    user_message: str,
    language: str,
    user_email: str,
    application: LoanApplication | None,
    actor_role: str = "customer",
) -> dict[str, Any]:
    """Retourne ``{"reply": str}`` ou ``{"error": str}``.

    actor_role: "customer" (default) ou "backoffice".
    """
    api_key = get_openai_api_key()
    if not api_key:
        return {"error": "no_api_key"}

    lang = (language or "fr").lower()
    loan_type = application.loan_type if application else ""

    profile_ctx = _build_profile_context(user_email, application, lang)
    docs_ctx = _build_documents_context(application)
    rag_ctx = _build_rag_context(user_message, loan_type)
    role_instructions = _build_role_instructions(actor_role, application, lang)

    address_rule = (
        "RÈGLE ADRESSE (non négociable) : "
        "L'adresse figurant sur une carte d'identité nationale (CIN) ou un passeport "
        "N'EST JAMAIS comparée à l'adresse du profil. Il est tout à fait normal qu'elles diffèrent. "
        "Seul un justificatif de domicile dédié (facture, relevé bancaire…) est vérifié par rapport au profil. "
        "Même si les résumés des documents mentionnent une adresse CIN différente du profil, "
        "tu NE DOIS PAS le signaler comme un problème. Ne mentionne jamais les différences d'adresse CIN/passeport."
    )

    default_lang = "français" if lang.startswith("fr") else "English"

    system_prompt = f"""Tu es LoanWise, un assistant expert en éligibilité aux prêts.
Tu disposes de TROIS sources d'information que tu dois toutes utiliser pour répondre avec précision.

{address_rule}

LANGUE : L'application prend en charge le français et l'anglais.
Par défaut, réponds en {default_lang}.
Si l'utilisateur écrit dans l'autre langue supportée (français ou anglais), adapte-toi à sa langue.
Ne refuse jamais de répondre sous prétexte de langue ; si l'utilisateur écrit dans une autre langue, réponds en {default_lang} poliment.
Utilise des paragraphes clairs et des listes markdown si utile (elles sont rendues).
Ne promets jamais une approbation ; utilise un langage indicatif.

══════════════════════════════════════════════════════
RÔLE DE L'INTERLOCUTEUR
══════════════════════════════════════════════════════
{role_instructions}

══════════════════════════════════════════════════════
SOURCE 1 — RÈGLES DE L'INSTITUTION (base de connaissances RAG)
══════════════════════════════════════════════════════
{rag_ctx}

══════════════════════════════════════════════════════
SOURCE 2 — PROFIL ET DEMANDE DE PRÊT DE L'UTILISATEUR
══════════════════════════════════════════════════════
{profile_ctx}

══════════════════════════════════════════════════════
SOURCE 3 — DOCUMENTS FOURNIS PAR L'UTILISATEUR
══════════════════════════════════════════════════════
{docs_ctx}
══════════════════════════════════════════════════════
"""

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        model = getattr(settings, "LOANWISE_OPENAI_MODEL", "gpt-4o-mini")
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message.strip()},
            ],
            max_tokens=1200,
            temperature=0.3,
        )
        text = (resp.choices[0].message.content or "").strip()
        return {"reply": text, "model": model}
    except Exception as e:
        logger.warning("assistant chat failed: %s", e)
        return {"error": str(e)}
