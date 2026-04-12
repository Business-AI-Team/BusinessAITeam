"""
Modèle objet canonique (ERD) : entités, relations, opérations Create / Modify.

Indépendant de Django ORM : référence métier, tests, ou mapping vers la persistance.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from core.erd_oop.enums import AccountType, LoanDocumentType, LoanRequestStatus


def _now() -> datetime:
    return datetime.now()


@dataclass
class EligibilityCondition:
    """
    7. Eligibility Condition — fichier PDF de règles.
    """

    entity_id: uuid.UUID
    file_pdf_path: str

    @classmethod
    def create(cls, file_pdf_path: str, entity_id: uuid.UUID | None = None) -> EligibilityCondition:
        return cls(entity_id=entity_id or uuid.uuid4(), file_pdf_path=file_pdf_path)


@dataclass
class BackOffice:
    """
    3. Back-Office
    """

    entity_id: uuid.UUID
    cin_number: str
    first_name: str
    last_name: str
    email_address: str


@dataclass
class Customer:
    """
    1. Customer (Client) — opérations Create, Modify.

    Téléphone : str (international) malgré le libellé « Number » dans le cahier.
    """

    entity_id: uuid.UUID
    id_card: str
    first_name: str
    last_name: str
    email_address: str
    phone_number: str
    address: str
    created_at: datetime = field(default_factory=_now)
    modified_at: datetime = field(default_factory=_now)
    loan_requests: list[LoanRequest] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        id_card: str,
        first_name: str,
        last_name: str,
        email_address: str,
        phone_number: str,
        address: str,
        *,
        entity_id: uuid.UUID | None = None,
        created_at: datetime | None = None,
    ) -> Customer:
        now = created_at or _now()
        return cls(
            entity_id=entity_id or uuid.uuid4(),
            id_card=id_card,
            first_name=first_name,
            last_name=last_name,
            email_address=email_address,
            phone_number=phone_number,
            address=address,
            created_at=now,
            modified_at=now,
        )

    def modify(
        self,
        *,
        id_card: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        email_address: str | None = None,
        phone_number: str | None = None,
        address: str | None = None,
        modified_at: datetime | None = None,
    ) -> None:
        """Opération Modify."""
        if id_card is not None:
            self.id_card = id_card
        if first_name is not None:
            self.first_name = first_name
        if last_name is not None:
            self.last_name = last_name
        if email_address is not None:
            self.email_address = email_address
        if phone_number is not None:
            self.phone_number = phone_number
        if address is not None:
            self.address = address
        self.modified_at = modified_at or _now()


@dataclass
class LoanRequest:
    """
    4. LoanRequest — opérations Create, Modify.

    Un Customer a plusieurs LoanRequest ; chaque LoanRequest a un seul Customer.
    """

    entity_id: uuid.UUID
    status: LoanRequestStatus
    creation_date: datetime
    due_date: date | None
    modification_date: datetime
    score_percent: Decimal | None
    customer: Customer
    documents: list[Document] = field(default_factory=list)
    notifications: list[Notification] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        customer: Customer,
        *,
        status: LoanRequestStatus = LoanRequestStatus.PENDING,
        due_date: date | None = None,
        score_percent: Decimal | None = None,
        entity_id: uuid.UUID | None = None,
        creation_date: datetime | None = None,
    ) -> LoanRequest:
        cid = entity_id or uuid.uuid4()
        now = creation_date or _now()
        lr = cls(
            entity_id=cid,
            status=status,
            creation_date=now,
            due_date=due_date,
            modification_date=now,
            score_percent=score_percent,
            customer=customer,
        )
        customer.loan_requests.append(lr)
        return lr

    def modify(
        self,
        *,
        status: LoanRequestStatus | None = None,
        due_date: date | None = None,
        score_percent: Decimal | None = None,
        modification_date: datetime | None = None,
    ) -> None:
        """Opération Modify."""
        if status is not None:
            self.status = status
        if due_date is not None:
            self.due_date = due_date
        if score_percent is not None:
            self.score_percent = score_percent
        self.modification_date = modification_date or _now()


@dataclass
class Document:
    """
    5. Document — un seul LoanRequest ; le LoanRequest agrège plusieurs Document.
    """

    entity_id: uuid.UUID
    doc_type: LoanDocumentType
    file_path: str
    validation_rule: str
    loan_request: LoanRequest

    def __post_init__(self) -> None:
        if self not in self.loan_request.documents:
            self.loan_request.documents.append(self)


@dataclass
class Notification:
    """
    6. Notification — plusieurs par LoanRequest.
    """

    entity_id: uuid.UUID
    at: datetime
    read: bool
    loan_request: LoanRequest

    def __post_init__(self) -> None:
        if self not in self.loan_request.notifications:
            self.loan_request.notifications.append(self)


@dataclass
class Account:
    """
    2. Account — opérations Create, Modify.

    CustomerId / BackOfficeId : références optionnelles (typiquement une seule selon Type).
    """

    entity_id: uuid.UUID
    email_address: str
    password: str
    account_type: AccountType
    customer: Customer | None
    back_office: BackOffice | None
    created_at: datetime = field(default_factory=_now)
    modified_at: datetime = field(default_factory=_now)

    @classmethod
    def create(
        cls,
        email_address: str,
        password: str,
        account_type: AccountType,
        *,
        customer: Customer | None = None,
        back_office: BackOffice | None = None,
        entity_id: uuid.UUID | None = None,
        created_at: datetime | None = None,
    ) -> Account:
        now = created_at or _now()
        return cls(
            entity_id=entity_id or uuid.uuid4(),
            email_address=email_address,
            password=password,
            account_type=account_type,
            customer=customer,
            back_office=back_office,
            created_at=now,
            modified_at=now,
        )

    def modify(
        self,
        *,
        email_address: str | None = None,
        password: str | None = None,
        account_type: AccountType | None = None,
        customer: Customer | None = None,
        back_office: BackOffice | None = None,
        modified_at: datetime | None = None,
    ) -> None:
        """Opération Modify (hasher le mot de passe côté application réelle)."""
        if email_address is not None:
            self.email_address = email_address
        if password is not None:
            self.password = password
        if account_type is not None:
            self.account_type = account_type
        if customer is not None:
            self.customer = customer
        if back_office is not None:
            self.back_office = back_office
        self.modified_at = modified_at or _now()


def create_document(
    loan_request: LoanRequest,
    doc_type: LoanDocumentType,
    file_path: str,
    validation_rule: str,
    entity_id: uuid.UUID | None = None,
) -> Document:
    """Crée un Document lié à un LoanRequest (enregistre la relation des deux côtés)."""
    return Document(
        entity_id=entity_id or uuid.uuid4(),
        doc_type=doc_type,
        file_path=file_path,
        validation_rule=validation_rule,
        loan_request=loan_request,
    )


def create_notification(
    loan_request: LoanRequest,
    at: datetime | None = None,
    read: bool = False,
    entity_id: uuid.UUID | None = None,
) -> Notification:
    """Crée une Notification liée à un LoanRequest."""
    return Notification(
        entity_id=entity_id or uuid.uuid4(),
        at=at or _now(),
        read=read,
        loan_request=loan_request,
    )


def example_graph() -> dict[str, Any]:
    """Petit graphe cohérent pour démo ou tests unitaires."""
    cust = Customer.create(
        id_card="AB123456",
        first_name="Ada",
        last_name="Lovelace",
        email_address="ada@example.com",
        phone_number="+33123456789",
        address="Paris",
    )
    bo = BackOffice(
        entity_id=uuid.uuid4(),
        cin_number="BO-001",
        first_name="Grace",
        last_name="Hopper",
        email_address="grace@bank.com",
    )
    acc = Account.create(
        email_address="ada@example.com",
        password="secret",
        account_type=AccountType.CUSTOMER,
        customer=cust,
    )
    lr = LoanRequest.create(customer=cust, due_date=date.today())
    lr.modify(status=LoanRequestStatus.VALIDATED, score_percent=Decimal("82.5"))
    doc = create_document(lr, LoanDocumentType.ID_CARD, "/tmp/id.pdf", validation_rule="min_resolution_300dpi")
    notif = create_notification(lr, read=False)
    elig = EligibilityCondition.create("/rules/policy.pdf")
    return {
        "customer": cust,
        "back_office": bo,
        "account": acc,
        "loan_request": lr,
        "document": doc,
        "notification": notif,
        "eligibility_condition": elig,
    }
