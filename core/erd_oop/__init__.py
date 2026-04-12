"""
Modèle objet ERD (Customer, Account, BackOffice, LoanRequest, Document, Notification, EligibilityCondition).

Usage:
    from core.erd_oop import Customer, LoanRequest, AccountType
    from core.erd_oop.entities import example_graph
"""

from core.erd_oop.entities import (
    Account,
    BackOffice,
    Customer,
    Document,
    EligibilityCondition,
    LoanRequest,
    Notification,
    create_document,
    create_notification,
    example_graph,
)
from core.erd_oop.enums import AccountType, LoanDocumentType, LoanRequestStatus

__all__ = [
    "Account",
    "AccountType",
    "BackOffice",
    "Customer",
    "Document",
    "EligibilityCondition",
    "LoanDocumentType",
    "LoanRequest",
    "LoanRequestStatus",
    "Notification",
    "create_document",
    "create_notification",
    "example_graph",
]
