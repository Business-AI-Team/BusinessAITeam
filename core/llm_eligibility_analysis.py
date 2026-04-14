"""
Synthèse décisionnelle via LangChain ChatOpenAI : **texte des documents** (politique
interne RAG + contenu analysé des pièces jointes). Pas de champs formulaire détaillés
(montant, type de prêt, langue, profil client, etc.) — seulement le corpus écrit et,
au besoin, un rappel minimal du score automatique pour cohérence.

Activé si ``LOANWISE_LLM_PROVIDER=openai`` et clé OpenAI disponible.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from django.conf import settings

from core.openai_config import get_openai_api_key

logger = logging.getLogger(__name__)

_ENGINE_KEYS = frozenset(
    {"score", "threshold", "decision", "financial_inputs_complete", "identity_address_block"}
)


def serialize_uploaded_document_texts_for_llm(application: Any) -> list[dict[str, Any]]:
    """
    Pour chaque pièce : uniquement le nom de fichier et le résultat d’analyse
    (résumé JSON du texte lu sur le document), pas les métadonnées formulaire.
    """
    out: list[dict[str, Any]] = []
    for doc in application.documents.all().order_by("id"):
        out.append(
            {
                "filename": doc.original_filename,
                "analysis_result": doc.analysis_result or {},
            }
        )
    return out


def _slim_engine_context(detail: dict[str, Any]) -> dict[str, Any]:
    """Signal numérique du moteur déterministe — sans bullets ni montants."""
    return {k: detail[k] for k in _ENGINE_KEYS if k in detail}


def invoke_full_context_eligibility_llm(
    application: Any,
    *,
    policy_full_text: str,
    deterministic_detail: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Appelle ChatOpenAI avec : (1) texte complet des documents de politique, (2) textes
    issus des pièces uploadées (analyse), (3) indicateurs minimaux du moteur (score / seuil).

    Retourne un dict parsé (clés normalisées) ou ``None`` si désactivé / erreur.
    """
    provider = getattr(settings, "LOANWISE_LLM_PROVIDER", "none").lower()
    if provider != "openai":
        return None
    api_key = get_openai_api_key()
    if not api_key:
        logger.info("LLM eligibility synthesis skipped: no OpenAI API key.")
        return None

    max_policy = int(getattr(settings, "LOANWISE_RAG_LLM_MAX_CHARS", 120000))
    policy = (policy_full_text or "")[:max_policy]

    uploads = serialize_uploaded_document_texts_for_llm(application)
    uploads_s = json.dumps(uploads, ensure_ascii=False, default=str)
    if len(uploads_s) > 52000:
        uploads_s = uploads_s[:51900] + "…[truncated]"

    engine = _slim_engine_context(deterministic_detail)
    engine_s = json.dumps(engine, ensure_ascii=False, default=str)

    lang = (application.language or "fr").lower()

    schema = (
        '{"llm_decision":"eligible|not_eligible|manual_review",'
        '"confidence":"high|medium|low",'
        '"summary_fr":"","summary_en":"",'
        '"detail_bullets_fr":[],"detail_bullets_en":[],'
        '"notes_fr":"","notes_en":""}'
    )

    system = (
        "You are a bank policy analyst. Base your reasoning **only on what is written in**: "
        "(A) the INTERNAL POLICY DOCUMENTS text below, and (B) the UPLOADED FILE ANALYSES "
        "(extracted/summarized text from applicant documents). "
        "Do NOT infer eligibility from applicant form fields — those are not provided here on purpose. "
        "You may use the small AUTOMATED ENGINE block only as a numeric cross-check (score vs threshold), "
        "not as a substitute for citing policy wording. "
        "Hard rule on addresses: if AUTOMATED ENGINE contains identity_address_block=true, or if document analyses "
        "show that the PROOF-OF-ADDRESS document address cannot be reconciled with the applicant's declared home, "
        "you MUST set llm_decision to not_eligible (or manual_review if genuinely ambiguous) and explain that "
        "the proof of address must match the declared residence. "
        "IMPORTANT EXCEPTION: a national ID card (CIN) or passport showing a different address from the profile "
        "is NOT a reason to reject — people often have a birth address or old address on their ID. "
        "Only flag mismatches between the proof-of-address document and the declared home address. "
        "Quote or paraphrase the policy documents when explaining. "
        "Reply with JSON only, no markdown. Schema: "
        + schema
        + f" Fill both French and English fields. Language hint for tone: {lang}."
    )

    user = (
        "=== A) INTERNAL POLICY DOCUMENTS (full text; tail may be truncated) ===\n"
        + policy
        + "\n\n=== B) UPLOADED DOCUMENTS — text/summary as read from files ===\n"
        + uploads_s
        + "\n\n=== C) AUTOMATED ENGINE (numeric cross-check only; not form data) ===\n"
        + engine_s
        + f"\n\ndossier_reference: {getattr(application, 'reference', '')!s}"
    )

    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = ChatOpenAI(
            model=getattr(settings, "LOANWISE_OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0.15,
            api_key=api_key,
            model_kwargs={"response_format": {"type": "json_object"}},
        )
        msg = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        raw = getattr(msg, "content", str(msg))
        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?", "", raw)
            raw = re.sub(r"\n```\s*$", "", raw)
        data = json.loads(raw)
    except Exception as e:
        logger.warning("invoke_full_context_eligibility_llm failed: %s", e)
        return {"unavailable": True, "error": str(e)}

    out: dict[str, Any] = {
        "llm_decision": data.get("llm_decision") or data.get("decision") or "manual_review",
        "confidence": data.get("confidence") or "medium",
        "summary_fr": data.get("summary_fr") or "",
        "summary_en": data.get("summary_en") or "",
        "detail_bullets_fr": data.get("detail_bullets_fr") or data.get("rationale_fr") or [],
        "detail_bullets_en": data.get("detail_bullets_en") or data.get("rationale_en") or [],
        "notes_fr": data.get("notes_fr") or "",
        "notes_en": data.get("notes_en") or "",
    }
    if isinstance(out["detail_bullets_fr"], str):
        out["detail_bullets_fr"] = [out["detail_bullets_fr"]]
    if isinstance(out["detail_bullets_en"], str):
        out["detail_bullets_en"] = [out["detail_bullets_en"]]
    return out
