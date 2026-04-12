"""
Picklists du modèle ERD (types stricts).
"""

from __future__ import annotations

from enum import Enum


class AccountType(str, Enum):
    """Type de compte (Account.Type)."""

    CUSTOMER = "customer"
    BACKOFFICE = "backoffice"


class LoanRequestStatus(str, Enum):
    """Statut d'une demande de prêt (LoanRequest.Status)."""

    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"
    CANCELED = "canceled"
    CLOSED = "closed"


class LoanDocumentType(str, Enum):
    """Type de document rattaché à une demande (Document.Type)."""

    ID_CARD = "id_card"
    FILE = "file"
    INCOME_PROOF = "income_proof"
    BUSINESS_PLAN = "business_plan"
    OTHER = "other"
