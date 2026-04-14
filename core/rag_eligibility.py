"""
RAG sur les PDF / textes admin : règles d'éligibilité → fourchettes (montants, durées).

Flux LangChain (https://docs.langchain.com/oss/python/langchain/rag) :

  1. Document → RecursiveCharacterTextSplitter
  2. Embeddings **OpenAI uniquement** (langchain_openai.OpenAIEmbeddings)
  3. VectorStore Chroma (langchain_chroma) → similarity_search

Sans clé OpenAI (OPENAI_API_KEY / Admin), pas d’index vectoriel — repli par mots-clés sur le texte en base.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from django.conf import settings

from core.openai_config import get_openai_api_key

logger = logging.getLogger(__name__)

_CHROMA_ROOT = "chroma_langchain"
_COLLECTION_NAME = "eligibility_rules"

# Paramètres recommandés dans la doc LangChain RAG (knowledge base / RAG)
_TEXT_SPLIT_CHUNK = 1000
_TEXT_SPLIT_OVERLAP = 200

try:
    import importlib.util as _ilu
    _LANGCHAIN_RAG_AVAILABLE = (
        _ilu.find_spec("langchain_chroma") is not None
        and _ilu.find_spec("langchain_core") is not None
        and _ilu.find_spec("langchain_text_splitters") is not None
    )
except Exception:
    _LANGCHAIN_RAG_AVAILABLE = False

if not _LANGCHAIN_RAG_AVAILABLE:
    logger.warning(
        "LangChain RAG stack not available. "
        "Install: pip install langchain-chroma langchain-text-splitters langchain-core langchain-openai"
    )

Chroma = Document = RecursiveCharacterTextSplitter = None  # type: ignore

_embeddings: Any = None
_embedding_meta: str | None = None  # identifiant pour sous-dossier Chroma (change si modèle / backend change)
_vector_store: Any = None


def _pdf_text_libraries_available() -> bool:
    """True if at least one PDF text library can be imported."""
    for module_name in ("pypdf", "PyPDF2"):
        try:
            __import__(module_name)
            return True
        except ImportError:
            continue
    return False


def _extract_pdf_text(path: Path) -> str:
    """Try pypdf first, then PyPDF2 (same API). Returns empty string on failure."""
    for module_name, reader_name in (("pypdf", "PdfReader"), ("PyPDF2", "PdfReader")):
        try:
            mod = __import__(module_name, fromlist=[reader_name])
            Reader = getattr(mod, reader_name)
            reader = Reader(str(path))
            parts: list[str] = []
            for page in reader.pages:
                parts.append(page.extract_text() or "")
            text = "\n".join(parts).strip()
            if text:
                return text
        except ImportError:
            continue
        except Exception as e:
            logger.warning("PDF extract failed (%s): %s", module_name, e)
    return ""


def extract_text_from_file(file_path: str | Path) -> str:
    """PDF via pypdf or PyPDF2; images via Pillow + pytesseract if available."""
    path = Path(file_path)
    if not path.is_file():
        return ""
    suf = path.suffix.lower()
    if suf == ".pdf":
        text = _extract_pdf_text(path)
        if not text:
            if not _pdf_text_libraries_available():
                logger.warning(
                    "No PDF text library found. Install: pip install pypdf (or PyPDF2). File: %s",
                    path.name,
                )
            else:
                logger.info(
                    "No selectable text in PDF %s (often a scanned image). "
                    "Use JPG/PNG or a text-based PDF; for policy docs use « Manual text » in admin.",
                    path.name,
                )
        return text
    if suf in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        try:
            from PIL import Image  # noqa: F401

            import pytesseract

            img = Image.open(path)
            return (pytesseract.image_to_string(img, lang="fra+eng") or "").strip()
        except Exception as e:
            logger.warning("Image OCR failed: %s", e)
            return ""
    return ""


def _sanitize_dir_name(s: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", s)[:180]


def _openai_vector_rag_ready() -> bool:
    """Index + retrieval sémantique nécessitent LangChain, une clé OpenAI, et OpenAIEmbeddings."""
    return _LANGCHAIN_RAG_AVAILABLE and bool(get_openai_api_key())


def _get_embeddings() -> Any:
    """Embeddings RAG : uniquement ``langchain_openai.OpenAIEmbeddings`` (modèle configurable, défaut text-embedding-3-small)."""
    global _embeddings, _embedding_meta
    if not _LANGCHAIN_RAG_AVAILABLE:
        raise RuntimeError("LangChain RAG not available")
    api_key = get_openai_api_key()
    if not api_key:
        raise RuntimeError("OpenAI API key required for RAG embeddings (OPENAI_API_KEY or Admin integration).")
    if _embeddings is None:
        from langchain_openai import OpenAIEmbeddings

        oai_model = getattr(settings, "LOANWISE_RAG_OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        _embedding_meta = f"openai_{_sanitize_dir_name(oai_model)}"
        _embeddings = OpenAIEmbeddings(model=oai_model, api_key=api_key)
        logger.info("RAG embeddings: OpenAI %s (LangChain OpenAIEmbeddings).", oai_model)
    return _embeddings


def _get_vector_store() -> Any:
    """Chroma via langchain-chroma (persist_directory + embedding_function), cf. doc LangChain."""
    global _vector_store
    if not _LANGCHAIN_RAG_AVAILABLE:
        raise RuntimeError("LangChain RAG not available")
    if _vector_store is None:
        from langchain_chroma import Chroma as _Chroma
        _ = _get_embeddings()
        sub = _embedding_meta or "default"
        persist = Path(settings.BASE_DIR) / "data" / _CHROMA_ROOT / sub
        persist.mkdir(parents=True, exist_ok=True)
        _vector_store = _Chroma(
            collection_name=_COLLECTION_NAME,
            embedding_function=_embeddings,
            persist_directory=str(persist),
        )
    return _vector_store


def _text_splitter() -> Any:
    from langchain_text_splitters import RecursiveCharacterTextSplitter as _RCS
    return _RCS(
        chunk_size=_TEXT_SPLIT_CHUNK,
        chunk_overlap=_TEXT_SPLIT_OVERLAP,
        add_start_index=True,
    )


def _delete_source_chunks(vector_store: Any, source_id: int) -> None:
    """Supprime tous les chunks dont metadata source_id correspond (ré-indexation)."""
    sid = str(source_id)
    try:
        vector_store.delete(where={"source_id": sid})
    except Exception as e:
        logger.warning("Chroma delete(where=) failed for source %s: %s", source_id, e)


def index_eligibility_source(source_id: int, title: str, full_text: str) -> None:
    """
    Indexe un texte politique : 1 Document LangChain → découpe → add_documents dans Chroma.
    (Équivalent à load → split → vector_store.add_documents dans le tutoriel RAG.)
    """
    if not _openai_vector_rag_ready():
        logger.warning(
            "RAG vector index skipped: set OPENAI_API_KEY (or Admin → Integration) for OpenAI embeddings."
        )
        return

    text = (full_text or "").strip()
    vs = _get_vector_store()
    _delete_source_chunks(vs, source_id)

    if not text:
        return

    from langchain_core.documents import Document as _Document
    doc = _Document(
        page_content=text,
        metadata={"source_id": str(source_id), "title": (title or "")[:500]},
    )
    splits = _text_splitter().split_documents([doc])
    if not splits:
        return
    try:
        vs.add_documents(splits)
    except Exception as e:
        logger.warning("vector_store.add_documents failed: %s", e)


def delete_eligibility_source_index(source_id: int) -> None:
    if not _openai_vector_rag_ready():
        return
    try:
        _delete_source_chunks(_get_vector_store(), source_id)
    except Exception as e:
        logger.warning("delete_eligibility_source_index failed: %s", e)


def get_all_active_knowledge_text(max_chars: int | None = None) -> dict[str, Any]:
    """
    Concatène **tout** le texte indexable des sources actives (manuel + extrait PDF/OCR),
    pour envoi au LLM ou heuristiques sur la politique complète.

    Respecte ``LOANWISE_RAG_LLM_MAX_CHARS`` (troncature en fin de corpus si besoin).
    """
    from core.models import EligibilityKnowledgeSource

    max_c = max_chars if max_chars is not None else int(getattr(settings, "LOANWISE_RAG_LLM_MAX_CHARS", 120000))
    sources = list(EligibilityKnowledgeSource.objects.filter(is_active=True).order_by("id"))
    parts: list[str] = []
    total = 0
    truncated = False
    used_sources = 0

    for src in sources:
        blob = src.searchable_blob()
        if not (blob or "").strip():
            continue
        title = (src.title or f"source_{src.pk}")[:200]
        block = f"\n\n===== [{src.pk}] {title} =====\n{blob.strip()}"
        if total >= max_c:
            truncated = True
            break
        space = max_c - total
        if len(block) <= space:
            parts.append(block)
            total += len(block)
            used_sources += 1
        else:
            if space > 400:
                parts.append(block[:space] + "\n[… contenu tronqué (limite LOANWISE_RAG_LLM_MAX_CHARS) …]")
                truncated = True
                used_sources += 1
            else:
                truncated = True
            break

    text = "".join(parts).strip()
    return {
        "text": text,
        "truncated": truncated,
        "source_rows": len(sources),
        "sources_with_text": used_sources,
        "max_chars": max_c,
    }


def retrieve_rule_chunks(query: str, loan_type: str, k: int = 6) -> list[str]:
    """
    Retrieval sémantique : similarity_search sur le vector store (cf. doc LangChain RAG).
    Repli : score par mots-clés sur les sources actives en base.
    """
    from core.models import EligibilityKnowledgeSource

    q = f"{query} {loan_type}".strip()

    if _openai_vector_rag_ready():
        try:
            vs = _get_vector_store()
            retrieved_docs = vs.similarity_search(q, k=min(k, 20))
            out = [d.page_content for d in retrieved_docs if (d.page_content or "").strip()]
            if out:
                return out
        except Exception as e:
            logger.warning("LangChain similarity_search failed: %s", e)

    # Fallback hors vector store (pas d’index ou erreur)
    active = EligibilityKnowledgeSource.objects.filter(is_active=True)
    scored: list[tuple[float, str]] = []
    terms = set(re.split(r"\W+", q.lower())) - {"", "le", "la", "les", "de", "et", "ou"}
    for src in active:
        blob = src.searchable_blob()
        for para in re.split(r"\n\s*\n+", blob):
            p = para.strip()
            if len(p) < 40:
                continue
            words = set(re.split(r"\W+", p.lower()))
            score = len(terms & words) / max(1, len(terms))
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:k]]


def _fallback_parse_amounts_and_terms(text: str) -> dict[str, Any]:
    """Heuristic extraction when no LLM (Ar, MGA, EUR, months)."""
    t = text.replace("\u00a0", " ").replace(" ", "")
    amounts: list[int] = []
    for m in re.finditer(r"(\d{1,3}(?:\.\d{3})+|\d{6,})", t):
        raw = m.group(1).replace(".", "")
        try:
            amounts.append(int(raw))
        except ValueError:
            pass
    for m in re.finditer(r"(\d[\d\s]{4,})\s*(?:Ar|MGA|MGAR|Ariary)", text, re.I):
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) >= 5:
            try:
                amounts.append(int(digits))
            except ValueError:
                pass
    months: list[int] = []
    for m in re.finditer(r"(\d+)\s*(?:mois|months?|m\b)", text, re.I):
        mo = int(m.group(1))
        if mo in (3, 6, 12, 18, 24, 36, 48, 60):
            months.append(mo)
    for m in re.finditer(r"(?:durée|duration|terme)[^\d]{0,12}(\d+)", text, re.I):
        mo = int(m.group(1))
        if 1 <= mo <= 120:
            months.append(mo)
    months = sorted(set(months))
    cur = "MGA" if re.search(r"\b(Ar|MGA|MGAR|Ariary)\b", text, re.I) else getattr(settings, "LOANWISE_CURRENCY", "EUR")
    min_a = min(amounts) if amounts else None
    max_a = max(amounts) if amounts else None
    if min_a is not None and max_a is not None and min_a > max_a:
        min_a, max_a = max_a, min_a
    return {
        "currency": cur,
        "min_amount": min_a,
        "max_amount": max_a,
        "term_months_options": months[:12],
        "summary_fr": text[:800] + ("…" if len(text) > 800 else ""),
        "summary_en": text[:800] + ("…" if len(text) > 800 else ""),
    }


def _llm_extract_rules(
    chunks: list[str],
    language: str,
    *,
    policy_text: str | None = None,
) -> dict[str, Any] | None:
    provider = getattr(settings, "LOANWISE_LLM_PROVIDER", "none").lower()
    if provider == "none":
        return None
    max_c = int(getattr(settings, "LOANWISE_RAG_LLM_MAX_CHARS", 120000))
    if policy_text and policy_text.strip():
        blob = policy_text.strip()[:max_c]
    elif chunks:
        blob = "\n---\n".join(chunks)[:max_c]
    else:
        return None
    schema_hint = (
        '{"currency":"MGA or EUR","min_amount": number or null,"max_amount": number or null,'
        '"term_months_options":[6,12,24],"summary_fr":"...","summary_en":"..."}'
    )
    system = (
        "You extract loan eligibility RULES from internal bank policy excerpts. "
        "Reply with JSON only, no markdown. Schema: " + schema_hint
    )
    user = f"Language hint: {language}\n\nExcerpts:\n{blob}"
    try:
        if provider == "openai":
            api_key = get_openai_api_key()
            if not api_key:
                logger.warning(
                    "LOANWISE_LLM_PROVIDER=openai but no API key (set OPENAI_API_KEY or Admin → Integration settings)."
                )
                return None
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import HumanMessage, SystemMessage

            llm = ChatOpenAI(
                model=getattr(settings, "LOANWISE_OPENAI_MODEL", "gpt-4o-mini"),
                temperature=0,
                api_key=api_key,
            )
            msg = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
            raw = getattr(msg, "content", str(msg))
        else:
            return None
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?", "", raw)
            raw = re.sub(r"\n```\s*$", "", raw)
        return json.loads(raw)
    except Exception as e:
        logger.warning("LLM rule extraction failed: %s", e)
        return None


def get_eligibility_guidance_from_rag(
    loan_type: str,
    language: str = "fr",
    *,
    preloaded_policy_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Returns keys: available, currency, min_amount, max_amount, term_months_options,
    summary_fr, summary_en, chunks_used (int), policy_truncated (bool when full corpus used).

    Priorité : **texte complet** des sources actives pour le LLM ; repli ``similarity_search`` + mots-clés.
    """
    loan_label = loan_type or "personal"
    q = (
        "règles d'éligibilité montant minimum maximum durée de remboursement mois prêt"
        if language.startswith("fr")
        else "eligibility rules loan amount min max repayment months term"
    )

    bundle = preloaded_policy_bundle if preloaded_policy_bundle is not None else get_all_active_knowledge_text()
    full_text = (bundle.get("text") or "").strip()
    chunks: list[str] = []

    if full_text:
        structured = _llm_extract_rules([], language, policy_text=full_text)
        if not structured:
            structured = _fallback_parse_amounts_and_terms(full_text)
        structured["available"] = True
        structured["chunks_used"] = int(bundle.get("sources_with_text") or 0) or 1
        structured["policy_truncated"] = bool(bundle.get("truncated"))
        structured["policy_chars"] = len(full_text)
        return structured

    chunks = retrieve_rule_chunks(q, loan_label, k=24)
    if not chunks:
        return {"available": False, "reason": "no_knowledge_sources"}

    structured = _llm_extract_rules(chunks, language, policy_text=None)
    if not structured:
        structured = _fallback_parse_amounts_and_terms("\n".join(chunks))

    structured["available"] = True
    structured["chunks_used"] = len(chunks)
    structured["policy_truncated"] = False
    return structured
