"""
LangGraph-style conversational agent guiding the user step-by-step.

Uses LangGraph when installed; otherwise a small in-process state machine with the same
external API so the UI and REST layer stay stable.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from core.models import ChatMessage, ChatRole, LoanRequest, LoanRequestStatus
from core.document_requirement_service import requirements_for_application
from core.rag_eligibility import build_rag_hint_for_chat

logger = logging.getLogger(__name__)


def _system_prompt(language: str) -> str:
    if language.startswith("fr"):
        return (
            "Tu es l'assistant LoanWise (Smart Loan Eligibility Checker). "
            "Tu guides l'utilisateur avec courtoisie, étapes courtes, et tu restes factuel. "
            "Ne promets jamais un crédit garanti; parle d'éligibilité indicative."
        )
    return (
        "You are the LoanWise assistant (Smart Loan Eligibility Checker). "
        "Guide the user politely in short steps. Never guarantee a loan; speak of indicative eligibility only."
    )


def _try_langgraph():
    try:
        from langgraph.graph import END, StateGraph  # type: ignore

        return StateGraph, END
    except ImportError:
        return None, None


def _llm_reply(
    user_text: str,
    language: str,
    system_extra: str,
    loan_type: str = "personal",
) -> str:
    """Invoke configured LLM backend (OpenAI, Ollama, etc.) or template fallback."""
    rag = build_rag_hint_for_chat(loan_type, language)
    system = _system_prompt(language) + " " + (rag + " " if rag else "") + system_extra
    provider = getattr(settings, "LOANWISE_LLM_PROVIDER", "none").lower()
    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI  # type: ignore

            model = getattr(settings, "LOANWISE_OPENAI_MODEL", "gpt-4o-mini")
            llm = ChatOpenAI(model=model, temperature=0.3)
            from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore

            msg = llm.invoke(
                [
                    SystemMessage(content=system),
                    HumanMessage(content=user_text),
                ]
            )
            return getattr(msg, "content", str(msg))
        except Exception as e:
            logger.warning("OpenAI LLM failed: %s", e)
    if provider == "ollama":
        try:
            from langchain_community.chat_models import ChatOllama  # type: ignore

            base = getattr(settings, "LOANWISE_OLLAMA_BASE_URL", "http://localhost:11434")
            model = getattr(settings, "LOANWISE_OLLAMA_MODEL", "llama3")
            llm = ChatOllama(base_url=base, model=model)
            from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore

            msg = llm.invoke([SystemMessage(content=system), HumanMessage(content=user_text)])
            return getattr(msg, "content", str(msg))
        except Exception as e:
            logger.warning("Ollama LLM failed: %s", e)
    # Template fallback (always works)
    if language.startswith("fr"):
        return f"(Mode démo sans LLM) Merci pour votre message. {system_extra[:200]}…"
    return f"(Demo mode without LLM) Thanks for your message. {system_extra[:200]}…"


def _persist(application: LoanRequest, role: str, content: str, step_hint: str = "", meta: dict | None = None):
    role_value = getattr(role, "value", role)
    ChatMessage.objects.create(
        loan_request=application,
        role=role_value,
        content=content,
        step_hint=step_hint,
        metadata=meta or {},
    )


def advance_simple_state(application: LoanRequest, user_message: str) -> str:
    """
    Deterministic step machine: interprets user input and returns assistant reply.
    Also syncs `application.current_step` and persists messages.
    """
    language = application.language or "fr"
    step = application.current_step or "welcome"
    _persist(application, ChatRole.USER, user_message, step_hint=step)

    msg_lower = user_message.strip().lower()

    if step == "welcome":
        application.current_step = "loan_type"
        application.status = LoanRequestStatus.PENDING
        application.save(update_fields=["current_step", "status", "modification_date"])
        reply = (
            "Pour commencer, quel type de prêt souhaitez-vous ? (personnel, immobilier, professionnel)"
            if language.startswith("fr")
            else "What loan type do you need? (personal, mortgage, business)"
        )
        _persist(application, ChatRole.ASSISTANT, reply, "loan_type")
        return reply

    if step == "loan_type":
        lt = "personal"
        if "immo" in msg_lower or "mortgage" in msg_lower:
            lt = "mortgage"
        elif "pro" in msg_lower or "business" in msg_lower:
            lt = "business"
        application.loan_type = lt
        application.current_step = "amount"
        application.save(update_fields=["loan_type", "current_step", "modification_date"])
        reply = (
            "Quel montant souhaitez-vous emprunter (nombre en euros) ?"
            if language.startswith("fr")
            else "What amount would you like to borrow (number in EUR)?"
        )
        _persist(application, ChatRole.ASSISTANT, reply, "amount")
        return reply

    if step == "amount":
        try:
            from decimal import Decimal

            num = "".join(c for c in user_message if c.isdigit() or c in ".,")
            num = num.replace(",", ".")
            application.amount_requested = Decimal(num or "0")
        except Exception:
            pass
        application.current_step = "income"
        application.save(update_fields=["amount_requested", "current_step", "modification_date"])
        reply = (
            "Quel est votre revenu annuel approximatif (EUR) ?"
            if language.startswith("fr")
            else "What is your approximate annual income (EUR)?"
        )
        _persist(application, ChatRole.ASSISTANT, reply, "income")
        return reply

    if step == "income":
        try:
            from decimal import Decimal

            num = "".join(c for c in user_message if c.isdigit() or c in ".,")
            num = num.replace(",", ".")
            application.annual_income = Decimal(num or "0")
        except Exception:
            pass
        application.current_step = "purpose"
        application.save(update_fields=["annual_income", "current_step", "modification_date"])
        reply = (
            "Décrivez brièvement l'objet du financement."
            if language.startswith("fr")
            else "Briefly describe the purpose of the financing."
        )
        _persist(application, ChatRole.ASSISTANT, reply, "purpose")
        return reply

    if step == "purpose":
        application.purpose = user_message[:2000]
        application.current_step = "documents"
        application.save(update_fields=["purpose", "current_step", "modification_date"])
        reqs = requirements_for_application(application)
        lines = [f"- {r.name}" for r in reqs]
        req_text = "\n".join(lines) if lines else "-"
        reply = (
            f"Merci. Veuillez téléverser les documents suivants dans l'interface :\n{req_text}"
            if language.startswith("fr")
            else f"Thank you. Please upload the following documents in the UI:\n{req_text}"
        )
        _persist(application, ChatRole.ASSISTANT, reply, "documents")
        return reply

    if step == "documents":
        application.current_step = "review"
        application.save(update_fields=["current_step", "modification_date"])
        extra = "Résumé prêt prêt pour analyse." if language.startswith("fr") else "Loan summary ready for analysis."
        reply = _llm_reply(user_message, language, extra, application.loan_type or "personal")
        _persist(application, ChatRole.ASSISTANT, reply, "review")
        return reply

    if step == "review":
        application.current_step = "done"
        application.save(update_fields=["current_step", "modification_date"])
        reply = _llm_reply(
            user_message,
            language,
            "Étape finale: l'orchestrateur peut calculer l'éligibilité."
            if language.startswith("fr")
            else "Final step: orchestrator can compute eligibility.",
            application.loan_type or "personal",
        )
        _persist(application, ChatRole.ASSISTANT, reply, "done")
        return reply

    reply = _llm_reply(
        user_message,
        language,
        "Conversation terminée." if language.startswith("fr") else "Conversation complete.",
        application.loan_type or "personal",
    )
    _persist(application, ChatRole.ASSISTANT, reply, "done")
    return reply


def run_chat_turn(application: LoanRequest, user_message: str) -> str:
    """
    Public entry: optional LangGraph single-node graph; else in-process state machine.
    """
    StateGraph, END = _try_langgraph()
    if StateGraph is not None and getattr(settings, "LOANWISE_USE_LANGGRAPH", False):
        try:
            return _run_langgraph_turn(application, user_message, StateGraph, END)
        except Exception as e:
            logger.exception("LangGraph failed, falling back: %s", e)
    return advance_simple_state(application, user_message)


def _run_langgraph_turn(application, user_message, StateGraph, END) -> str:
    """Single-node LangGraph: delegates to the same step machine for consistency."""
    from typing import TypedDict

    class AgentState(TypedDict, total=False):
        application_id: int
        user_message: str
        assistant_reply: str

    def agent_node(state: AgentState) -> AgentState:
        app = LoanRequest.objects.get(pk=state["application_id"])
        text = advance_simple_state(app, state["user_message"])
        return {**state, "assistant_reply": text}

    workflow = StateGraph(AgentState)
    workflow.add_node("agent", agent_node)
    workflow.set_entry_point("agent")
    workflow.add_edge("agent", END)
    graph = workflow.compile()
    out = graph.invoke(
        {
            "application_id": application.id,
            "user_message": user_message,
            "assistant_reply": "",
        }
    )
    return out.get("assistant_reply") or ""
