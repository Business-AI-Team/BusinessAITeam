"""
Assistant conversationnel (OpenAI) pour expliquer un dossier ou donner des pistes d'amélioration.

Quatre sources de contexte injectées dans chaque échange :
  SOURCE 0  — Guide d'utilisation (admin) : PDF/texte configuré par l'admin selon le rôle + la page courante
  SOURCE 1a — Politique complète de l'institution : corpus intégral des sources actives (EligibilityKnowledgeSource)
  SOURCE 1b — Extraits pertinents (RAG) : top-k chunks sémantiques depuis Chroma pour la question posée
  SOURCE 2  — Profil utilisateur : données du customer + demande de prêt + analyse d'éligibilité
  SOURCE 3  — Documents fournis : résumés des pièces justificatives uploadées
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
            parts.append(f"Revenu annuel estimé (extrait des documents par l'IA): {cust.annual_income} {cust.income_currency}")
        else:
            parts.append("Revenu annuel: non encore extrait (l'IA le lira automatiquement depuis les bulletins de salaire uploadés)")

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


# ── SOURCE 0 : guide d'utilisation (admin-configurable) ──────────────────────

def _load_guide_text(actor_role: str, page_context: str) -> str:
    """
    Charge le texte du guide depuis AssistantGuideSource selon le rôle et la page.

    Ordre de priorité :
      1. Correspondance exacte (role == actor_role AND page_context == page_context)
      2. Page spécifique + rôle "any"
      3. Rôle spécifique + page "any"
      4. any + any (guide global)

    Plusieurs guides actifs peuvent correspondre ; tous sont concaténés.
    """
    from core.models import AssistantGuideSource
    from django.db.models import Q

    matching = AssistantGuideSource.objects.filter(active=True).filter(
        Q(role=actor_role, page_context=page_context)
        | Q(role="any", page_context=page_context)
        | Q(role=actor_role, page_context="any")
        | Q(role="any", page_context="any")
    ).order_by("-role", "-page_context")

    parts: list[str] = []
    for guide in matching:
        txt = guide.guide_text()
        if txt:
            parts.append(f"[{guide.title}]\n{txt[:3000]}")

    return "\n\n".join(parts) if parts else ""


# ── SOURCE 1 : règles RAG (institution) ───────────────────────────────────────

def _build_policy_context(user_message: str, loan_type: str) -> str:
    """
    Corpus complet de la politique institutionnelle (SOURCE 1a) + top-k chunks sémantiques
    pertinents pour la question posée (SOURCE 1b).

    SOURCE 1a garantit que l'assistant connaît l'intégralité des règles même si la question
    n'est pas bien couverte par la recherche vectorielle.
    SOURCE 1b oriente le LLM sur les passages les plus directement pertinents.
    """
    from core.rag_eligibility import get_all_active_knowledge_text, retrieve_rule_chunks

    parts: list[str] = []

    # SOURCE 1a : corpus intégral des sources actives
    bundle = get_all_active_knowledge_text()
    full_text = (bundle.get("text") or "").strip()
    if full_text:
        trunc_note = " [tronqué — voir LOANWISE_RAG_LLM_MAX_CHARS]" if bundle.get("truncated") else ""
        parts.append(
            f"[POLITIQUE COMPLÈTE DE L'INSTITUTION{trunc_note}]\n{full_text}"
        )

    # SOURCE 1b : extraits sémantiquement pertinents pour la question de l'utilisateur
    chunks = retrieve_rule_chunks(user_message, loan_type, k=5)
    if chunks:
        chunk_block = "\n\n".join(
            f"[extrait pertinent {i}]\n{c[:2000]}" for i, c in enumerate(chunks, 1)
        )
        parts.append(f"[EXTRAITS PERTINENTS POUR LA QUESTION]\n{chunk_block}")

    return "\n\n".join(parts) if parts else "(aucune règle disponible)"


# ── Bloc de rôle ──────────────────────────────────────────────────────────────

def _build_role_instructions(
    actor_role: str,
    application: LoanApplication | None,
    lang: str,
) -> str:
    """Génère le bloc d'identité et de perspective selon le rôle de l'interlocuteur."""
    is_fr = lang.startswith("fr")
    cust = getattr(application, "customer", None) if application else None
    customer_name = f"{cust.first_name} {cust.last_name}".strip() if cust else "le client"

    if actor_role == "admin":
        if is_fr:
            return (
                "Tu parles actuellement avec un ADMINISTRATEUR de notre application LoanWise (superutilisateur). "
                "Adopte un ton technique et direct. "
                "Tu peux répondre à toutes les questions sur notre système, nos règles, nos configurations, "
                "les données d'un dossier, les logs ou l'architecture. "
                "N'omets aucun détail."
            )
        return (
            "You are currently speaking with an ADMINISTRATOR of our LoanWise application (superuser). "
            "Be direct and technically precise. "
            "Answer any questions about our system, our rules, our configurations, application data, logs, or architecture. "
            "Omit nothing."
        )
    if actor_role == "backoffice":
        if is_fr:
            return (
                "Tu parles actuellement avec un agent du BACKOFFICE — un collègue de notre institution bancaire. "
                f"Cet agent consulte le dossier du client « {customer_name} » dont il n'est PAS le propriétaire. "
                "Adopte un ton professionnel et analytique. "
                "Tu peux fournir des détails techniques complets (scores, ratios, incohérences de documents, nos règles internes). "
                "Aide l'agent à comprendre pourquoi notre décision est favorable ou défavorable sur ce dossier, "
                "et quelles actions notre équipe peut entreprendre (demande de pièces complémentaires, validation manuelle, etc.)."
            )
        return (
            "You are currently speaking with a BACKOFFICE agent — a colleague at our banking institution. "
            f"This agent is reviewing the application of customer « {customer_name} », who is NOT the one chatting. "
            "Use a professional and analytical tone. "
            "Provide full technical details (scores, ratios, document inconsistencies, our internal rules). "
            "Help the agent understand why our decision is favourable or unfavourable on this application, "
            "and what actions our team can take (request additional documents, manual validation, etc.)."
        )
    # customer / default
    if is_fr:
        return (
            f"Tu parles actuellement avec « {customer_name} », le CLIENT propriétaire de cette demande de prêt. "
            "Adopte un ton bienveillant, clair et pédagogique. "
            "Explique nos décisions en termes simples, sans jargon technique excessif. "
            "Guide le client sur ce qu'il peut faire pour améliorer son dossier auprès de notre institution. "
            "Ne divulgue pas de détails internes réservés au backoffice (règles de scoring internes, seuils bruts, etc.)."
        )
    return (
        f"You are currently speaking with « {customer_name} », the CUSTOMER who owns this loan application. "
        "Use a warm, clear, and educational tone. "
        "Explain our decisions in simple terms, avoiding excessive technical jargon. "
        "Guide the customer on how to improve their application with our institution. "
        "Do not disclose internal backoffice details (raw scoring rules, internal thresholds, etc.)."
    )


# ── Assemblage du prompt système ──────────────────────────────────────────────

def _build_system_prompt(
    *,
    lang: str,
    actor_role: str,
    application: LoanApplication | None,
    user_email: str,
    user_message: str,
    page_context: str,
    actor_user=None,
) -> str:
    """
    Assemble the full system prompt from the five context sources.

    Keeping this logic separate from build_assistant_reply() makes each
    source independently testable and the main entry-point easy to read.
    """
    is_fr = lang.startswith("fr")
    default_lang = "français" if is_fr else "English"
    loan_type = application.loan_type if application else ""

    address_rule = (
        "RÈGLE ADRESSE (non négociable) : "
        "L'adresse figurant sur une carte d'identité nationale (CIN) ou un passeport "
        "N'EST JAMAIS comparée à l'adresse du profil. Il est tout à fait normal qu'elles diffèrent. "
        "Seul un justificatif de domicile dédié (facture, relevé bancaire…) est vérifié par rapport au profil. "
        "Même si les résumés des documents mentionnent une adresse CIN différente du profil, "
        "tu NE DOIS PAS le signaler comme un problème. Ne mentionne jamais les différences d'adresse CIN/passeport."
    )

    income_rule = (
        "RÈGLE REVENU (non négociable) : "
        "Le revenu annuel du client N'EST JAMAIS saisi manuellement par l'utilisateur dans le formulaire. "
        "Il est UNIQUEMENT extrait automatiquement par notre système depuis les documents fournis "
        "(bulletins de salaire, relevés bancaires, etc.). "
        "Si le client demande comment renseigner son revenu, explique-lui qu'il doit téléverser "
        "ses bulletins de salaire — notre IA extraira le montant automatiquement. "
        "Ne lui demande jamais de saisir un revenu manuellement."
    )

    exhaustivity_rule = (
        "RÈGLE D'EXHAUSTIVITÉ (non négociable) : "
        "Lorsqu'un dossier présente plusieurs problèmes, tu DOIS les mentionner TOUS sans exception. "
        "Ne dis JAMAIS 'le seul problème est…' ou 'uniquement…' si d'autres problèmes existent dans les sources. "
        "Liste chaque problème détecté sous forme de points distincts : "
        "bulletins hors fenêtre des 3 derniers mois, bulletins en doublon (même mois), "
        "nom du salarié différent du profil, nom sur la CIN différent du profil, "
        "adresse incohérente sur le justificatif de domicile, revenu incohérent, montant demandé hors barème, etc. "
        "Si SOURCE 3 (documents) contient des incohérences, liste-les toutes. "
        "Un client doit pouvoir corriger SON DOSSIER EN UNE SEULE FOIS sans découvrir de nouveaux problèmes au fur et à mesure."
    )

    role_instructions = _build_role_instructions(actor_role, application, lang)
    guide_ctx = _load_guide_text(actor_role, page_context)
    policy_ctx = _build_policy_context(user_message, loan_type)
    profile_ctx = _build_profile_context(user_email, application, lang)
    docs_ctx = _build_documents_context(application)

    # ── Profil de l'utilisateur connecté (toutes les infos disponibles en base) ─
    actor_info_lines = [f"Email : {user_email}"]
    if actor_user is not None:
        full_name = f"{actor_user.first_name} {actor_user.last_name}".strip()
        if full_name:
            actor_info_lines.append(f"Nom : {full_name}")
        actor_info_lines.append(f"Rôle : {actor_role}")
        cust = getattr(actor_user, "customer_profile", None)
        if cust:
            if cust.id_card:
                actor_info_lines.append(f"CIN : {cust.id_card}")
            if cust.phone:
                actor_info_lines.append(f"Téléphone : {cust.phone}")
            if cust.address:
                actor_info_lines.append(f"Adresse : {cust.address}")
            if cust.country:
                actor_info_lines.append(f"Pays : {cust.country}")
    actor_info = "\n".join(actor_info_lines)

    guide_block = ""
    if guide_ctx:
        guide_block = f"""
══════════════════════════════════════════════════════
SOURCE 0 — GUIDE D'UTILISATION (configuré par l'administrateur)
══════════════════════════════════════════════════════
{guide_ctx}
"""

    return f"""Tu es LoanWise, l'assistant interne de notre institution bancaire, spécialisé dans l'éligibilité aux prêts.
Tu fais partie de l'équipe : parle toujours à la première personne du pluriel — utilise « notre institution », « notre politique de crédit », « nos règles », « nos clients », « notre barème ».
Tu disposes de CINQ sources d'information que tu dois toutes utiliser pour répondre avec précision.
SOURCE 0 = guide d'utilisation (admin) | SOURCE 1a = notre politique institutionnelle complète | SOURCE 1b = extraits pertinents | SOURCE 2 = profil/demande | SOURCE 3 = documents fournis.

{address_rule}

{income_rule}

{exhaustivity_rule}

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

Page/section actuelle : {page_context}
Adapte ton aide aux actions disponibles sur cette page pour le rôle ci-dessus.
Si SOURCE 0 contient des instructions pour cette page et ce rôle, suis-les en priorité.

INFORMATIONS SUR L'UTILISATEUR CONNECTÉ
{actor_info}
{guide_block}
══════════════════════════════════════════════════════
SOURCE 1 — RÈGLES DE L'INSTITUTION (politique complète + extraits pertinents)
══════════════════════════════════════════════════════
{policy_ctx}

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


# ── Point d'entrée principal ──────────────────────────────────────────────────

def build_assistant_reply(
    *,
    user_message: str,
    language: str,
    user_email: str,
    application: LoanApplication | None,
    actor_role: str = "customer",
    actor_user=None,
    page_context: str = "home",
    history: list[dict] | None = None,
) -> dict[str, Any]:
    """Retourne ``{"reply": str, "model": str}`` ou ``{"error": str}``.

    actor_role: "customer" (default), "backoffice" ou "admin".
    actor_user: objet User Django de l'utilisateur connecté (optionnel, enrichit le prompt).
    page_context: identifiant de la page courante (home, dashboard, application_detail, …).
    """
    api_key = get_openai_api_key()
    if not api_key:
        return {"error": "no_api_key"}

    lang = (language or "fr").lower()
    system_prompt = _build_system_prompt(
        lang=lang,
        actor_role=actor_role,
        application=application,
        user_email=user_email,
        user_message=user_message,
        page_context=page_context,
        actor_user=actor_user,
    )

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        model = getattr(settings, "LOANWISE_OPENAI_MODEL", "gpt-4o-mini")

        safe_history: list[dict] = []
        for turn in (history or [])[-20:]:
            role = turn.get("role", "")
            content = (turn.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                safe_history.append({"role": role, "content": content})

        messages = (
            [{"role": "system", "content": system_prompt}]
            + safe_history
            + [{"role": "user", "content": user_message.strip()}]
        )

        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=1200,
            temperature=0.3,
        )
        text = (resp.choices[0].message.content or "").strip()
        return {"reply": text, "model": model}
    except Exception as e:
        logger.warning("assistant chat failed: %s", e)
        return {"error": str(e)}
