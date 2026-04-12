"""
RAG over admin-uploaded PDFs / images: eligibility rules → user-facing ranges (amounts, terms).

Optional stack (requirements-ai.txt): Chroma + sentence-transformers + pypdf (+ OCR for images).
Falls back to keyword retrieval over stored text if Chroma is unavailable.
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

_CHUNK_SIZE = 900
_CHUNK_OVERLAP = 120


def _chunk_text(text: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    chunks: list[str] = []
    i = 0
    while i < len(text):
        chunks.append(text[i : i + size])
        i += size - overlap
    return chunks


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
            logger.warning(
                "No PDF text extracted from %s. Install: pip install pypdf "
                "(or PyPDF2). For scanned PDFs, paste policy text in « Manual text » in admin.",
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


def _chroma_collection():
    try:
        import chromadb
        from chromadb.utils import embedding_functions

        base = Path(settings.BASE_DIR) / "data" / "chroma_rag"
        base.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(base))
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
        return client.get_or_create_collection(
            name="eligibility_rules",
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
    except Exception as e:
        logger.warning("Chroma / embeddings unavailable: %s", e)
        return None


def index_eligibility_source(source_id: int, title: str, full_text: str) -> None:
    """Chunk text and upsert into Chroma (or no-op if unavailable)."""
    col = _chroma_collection()
    chunks = _chunk_text(full_text)
    if not chunks:
        if col:
            try:
                col.delete(where={"source_id": str(source_id)})
            except Exception:
                pass
        return
    if col is None:
        return
    try:
        col.delete(where={"source_id": str(source_id)})
    except Exception:
        pass
    ids = [f"{source_id}_{i}" for i in range(len(chunks))]
    metadatas = [{"source_id": str(source_id), "title": title or ""} for _ in chunks]
    col.add(ids=ids, documents=chunks, metadatas=metadatas)


def delete_eligibility_source_index(source_id: int) -> None:
    col = _chroma_collection()
    if col is None:
        return
    try:
        col.delete(where={"source_id": str(source_id)})
    except Exception as e:
        logger.warning("Chroma delete failed: %s", e)


def retrieve_rule_chunks(query: str, loan_type: str, k: int = 6) -> list[str]:
    """Semantic search if Chroma available; else keyword fallback over active sources."""
    from core.models import EligibilityKnowledgeSource

    q = f"{query} {loan_type}".strip()
    col = _chroma_collection()
    if col is not None:
        try:
            res = col.query(query_texts=[q], n_results=min(k, 20))
            docs = (res.get("documents") or [[]])[0]
            return [d for d in docs if d]
        except Exception as e:
            logger.warning("Chroma query failed: %s", e)

    # Fallback: score paragraphs by keyword overlap
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


def _llm_extract_rules(chunks: list[str], language: str) -> dict[str, Any] | None:
    provider = getattr(settings, "LOANWISE_LLM_PROVIDER", "none").lower()
    if provider == "none" or not chunks:
        return None
    blob = "\n---\n".join(chunks)[:12000]
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
) -> dict[str, Any]:
    """
    Returns keys: available, currency, min_amount, max_amount, term_months_options,
    summary_fr, summary_en, chunks_used (int).
    """
    loan_label = loan_type or "personal"
    q = (
        "règles d'éligibilité montant minimum maximum durée de remboursement mois prêt"
        if language.startswith("fr")
        else "eligibility rules loan amount min max repayment months term"
    )
    chunks = retrieve_rule_chunks(q, loan_label, k=8)
    if not chunks:
        return {"available": False, "reason": "no_knowledge_sources"}

    structured = _llm_extract_rules(chunks, language)
    if not structured:
        structured = _fallback_parse_amounts_and_terms("\n".join(chunks))

    structured["available"] = True
    structured["chunks_used"] = len(chunks)
    return structured


def build_rag_hint_for_chat(loan_type: str, language: str) -> str:
    """Short system add-on for the loan assistant."""
    g = get_eligibility_guidance_from_rag(loan_type, language)
    if not g.get("available"):
        return ""
    if language.startswith("fr"):
        return (
            f"[Règles internes indicatives — montants {g.get('min_amount')}–{g.get('max_amount')} "
            f"{g.get('currency')}, durées possibles: {g.get('term_months_options')}. "
            "Citer ces fourchettes si l'utilisateur demande ce qu'il peut emprunter.]"
        )
    return (
        f"[Internal policy hints — amounts {g.get('min_amount')}–{g.get('max_amount')} "
        f"{g.get('currency')}, terms (months): {g.get('term_months_options')}.]"
    )
