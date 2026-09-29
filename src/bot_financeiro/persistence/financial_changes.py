from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Column,
    Date,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    insert,
    select,
)
from sqlalchemy.orm import Session

from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.credit_cards import (
    CardPurchase,
    InstallmentPurchase,
    InvoiceFinancialCharge,
)
from bot_financeiro.domain.financial_changes import (
    FactChange,
    FinancialFactCanceled,
    FinancialFactCorrected,
    FinancialFactHistory,
)
from bot_financeiro.domain.movements import (
    BillPayment,
    CashExpense,
    Income,
    InvoicePayment,
    Transfer,
)
from bot_financeiro.domain.payables import FinancialCharge, PayableOpened
from bot_financeiro.persistence.categories import SqlAlchemyAllocationRepository, allocations
from bot_financeiro.persistence.financial_operations import (
    SqlAlchemyFinancialMovementRepository,
)
from bot_financeiro.persistence.money import money_from_minor_units, money_to_minor_units
from bot_financeiro.persistence.schema import metadata

PersistedChangeableFact = (
    Income
    | CashExpense
    | Transfer
    | BillPayment
    | InvoicePayment
    | PayableOpened
    | FinancialCharge
    | CardPurchase
    | InstallmentPurchase
    | InvoiceFinancialCharge
)

financial_fact_changes = Table(
    "financial_fact_changes",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("target_id", String(36), nullable=False),
    Column("target_kind", String(32), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("kind", String(16), nullable=False),
    Column("previous_version_id", String(36), nullable=False),
    Column("reason", String, nullable=False),
    Column("recorded_effective_date", Date, nullable=False),
    Column("recorded_at", String, nullable=False),
    Column("origin", String(16), nullable=False),
    Column("replacement_kind", String(24), nullable=True),
    Column("replacement_id", String(36), nullable=True),
    Column("replacement_account_id", String(36), nullable=True),
    Column("replacement_source_account_id", String(36), nullable=True),
    Column("replacement_destination_account_id", String(36), nullable=True),
    Column("replacement_allocation_id", String(36), ForeignKey(allocations.c.id), nullable=True),
    Column("replacement_related_transfer_id", String(36), nullable=True),
    Column("replacement_payable_id", String(36), nullable=True),
    Column("replacement_card_id", String(36), nullable=True),
    Column("replacement_invoice_closing_date", Date, nullable=True),
    Column("replacement_due_date", Date, nullable=True),
    Column("replacement_applied_amount_minor_units", Integer, nullable=True),
    Column("replacement_amount_minor_units", Integer, nullable=True),
    Column("replacement_currency", String(3), nullable=True),
    Column("replacement_effective_date", Date, nullable=True),
    Column("replacement_recorded_at", String, nullable=True),
    Column("replacement_origin", String(16), nullable=True),
    Column("replacement_description", String, nullable=True),
    UniqueConstraint("target_id", "sequence"),
)


class SqlAlchemyFinancialChangeRepository:
    def __init__(
        self,
        session: Session,
        original_non_movement_loader: Callable[[UUID], object] | None = None,
    ) -> None:
        self._session = session
        self._original_non_movement_loader = original_non_movement_loader

    def add(self, change: FactChange) -> None:
        sequence = self._session.scalar(
            select(financial_fact_changes.c.sequence)
            .where(financial_fact_changes.c.target_id == str(change.target_id))
            .order_by(financial_fact_changes.c.sequence.desc())
            .limit(1)
        )
        values: dict[str, object] = {
            "id": str(change.change_id),
            "target_id": str(change.target_id),
            "target_kind": self._fact_kind(change.replacement)
            if isinstance(change, FinancialFactCorrected)
            else self._target_kind(change.target_id),
            "sequence": 1 if sequence is None else sequence + 1,
            "kind": ("cancellation" if isinstance(change, FinancialFactCanceled) else "correction"),
            "previous_version_id": str(change.previous_version_id),
            "reason": change.reason,
            "recorded_effective_date": change.metadata.effective_date,
            "recorded_at": change.metadata.recorded_at.isoformat(),
            "origin": change.metadata.origin.value,
            "replacement_kind": None,
            "replacement_id": None,
            "replacement_account_id": None,
            "replacement_source_account_id": None,
            "replacement_destination_account_id": None,
            "replacement_allocation_id": None,
            "replacement_related_transfer_id": None,
            "replacement_payable_id": None,
            "replacement_card_id": None,
            "replacement_invoice_closing_date": None,
            "replacement_due_date": None,
            "replacement_applied_amount_minor_units": None,
            "replacement_amount_minor_units": None,
            "replacement_currency": None,
            "replacement_effective_date": None,
            "replacement_recorded_at": None,
            "replacement_origin": None,
            "replacement_description": None,
        }
        if isinstance(change, FinancialFactCorrected):
            values.update(self._replacement_values(change.replacement))
        self._session.execute(insert(financial_fact_changes).values(values))

    def _target_kind(self, target_id: UUID) -> str:
        existing = self._session.scalar(
            select(financial_fact_changes.c.target_kind)
            .where(financial_fact_changes.c.target_id == str(target_id))
            .limit(1)
        )
        if existing is not None:
            return existing
        movement = SqlAlchemyFinancialMovementRepository(self._session).get_recorded(target_id)
        if movement is not None:
            return self._fact_kind(movement)
        return self._fact_kind(self._original_non_movement(target_id))

    def history(self, movement_id: UUID) -> FinancialFactHistory[PersistedChangeableFact]:
        original = SqlAlchemyFinancialMovementRepository(self._session).get_recorded(movement_id)
        if original is None:
            original = self._original_non_movement(movement_id)
        rows = self._session.execute(
            select(financial_fact_changes)
            .where(financial_fact_changes.c.target_id == str(movement_id))
            .order_by(financial_fact_changes.c.sequence)
        ).mappings()
        events = tuple(self.deserialize_change(row) for row in rows)
        return FinancialFactHistory(original=original, events=events)

    def _original_non_movement(self, event_id: UUID):
        if self._original_non_movement_loader is None:
            raise LookupError(f"financial fact {event_id} does not exist")
        return self._original_non_movement_loader(event_id)

    def deserialize_change(self, row):
        change_metadata = RecordMetadata(
            effective_date=row["recorded_effective_date"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
            origin=RecordOrigin(row["origin"]),
            record_id=UUID(row["id"]),
        )
        common = {
            "target_id": UUID(row["target_id"]),
            "previous_version_id": UUID(row["previous_version_id"]),
            "reason": row["reason"],
            "metadata": change_metadata,
        }
        if row["kind"] == "cancellation":
            return FinancialFactCanceled(**common)
        return FinancialFactCorrected(replacement=self._replacement(row), **common)

    def _replacement(self, row) -> PersistedChangeableFact:
        replacement_metadata = RecordMetadata(
            effective_date=row["replacement_effective_date"],
            recorded_at=datetime.fromisoformat(row["replacement_recorded_at"]),
            origin=RecordOrigin(row["replacement_origin"]),
            record_id=UUID(row["replacement_id"]),
        )
        codec = _CODECS_BY_KIND.get(row["replacement_kind"])
        if codec is None or codec.decode is None:
            raise ValueError(f"unknown corrected financial fact kind: {row['replacement_kind']}")
        return codec.decode(self, row, replacement_metadata)

    @staticmethod
    def _replacement_values(replacement: PersistedChangeableFact) -> dict[str, object]:
        values: dict[str, object] = {
            "replacement_id": str(replacement.metadata.record_id),
            "replacement_amount_minor_units": money_to_minor_units(replacement.amount),
            "replacement_currency": replacement.amount.currency,
            "replacement_effective_date": replacement.metadata.effective_date,
            "replacement_recorded_at": replacement.metadata.recorded_at.isoformat(),
            "replacement_origin": replacement.metadata.origin.value,
            "replacement_description": getattr(replacement, "description", None),
        }
        codec = _CODECS_BY_TYPE.get(type(replacement))
        if codec is None or codec.encode is None:
            raise ValueError("financial fact requires its specific persistence handler")
        values["replacement_kind"] = codec.kind
        values.update(codec.encode(replacement))
        return values

    @staticmethod
    def _fact_kind(fact) -> str:
        codec = _CODECS_BY_TYPE.get(type(fact))
        if codec is None:
            raise ValueError("unsupported financial fact type")
        return codec.kind


@dataclass(frozen=True, slots=True)
class _FinancialFactCodec:
    kind: str
    fact_type: type[object]
    decode: Callable[[SqlAlchemyFinancialChangeRepository, object, RecordMetadata], object] | None
    encode: Callable[[object], dict[str, object]] | None


def _replacement_allocation(
    repository: SqlAlchemyFinancialChangeRepository,
    row,
    replacement_metadata: RecordMetadata,
    *,
    require_matching_metadata: bool,
):
    allocation = SqlAlchemyAllocationRepository(repository._session).get(
        UUID(row["replacement_allocation_id"])
    )
    if allocation is None:
        raise ValueError("corrected financial fact allocation does not exist")
    if require_matching_metadata and allocation.metadata != replacement_metadata:
        raise ValueError("corrected financial fact allocation is inconsistent")
    return allocation


def _decode_transfer(repository, row, replacement_metadata):
    del repository
    return Transfer(
        source_account_id=UUID(row["replacement_source_account_id"]),
        destination_account_id=UUID(row["replacement_destination_account_id"]),
        amount=money_from_minor_units(
            row["replacement_amount_minor_units"], row["replacement_currency"]
        ),
        metadata=replacement_metadata,
        description=row["replacement_description"],
        original_event_id=UUID(row["target_id"]),
    )


def _decode_bill_payment(repository, row, replacement_metadata):
    del repository
    return BillPayment(
        payable_id=UUID(row["replacement_payable_id"]),
        account_id=UUID(row["replacement_account_id"]),
        amount=money_from_minor_units(
            row["replacement_amount_minor_units"], row["replacement_currency"]
        ),
        metadata=replacement_metadata,
        description=row["replacement_description"],
        original_event_id=UUID(row["target_id"]),
    )


def _decode_invoice_payment(repository, row, replacement_metadata):
    del repository
    return InvoicePayment(
        card_id=UUID(row["replacement_card_id"]),
        invoice_closing_date=row["replacement_invoice_closing_date"],
        account_id=UUID(row["replacement_account_id"]),
        amount=money_from_minor_units(
            row["replacement_amount_minor_units"], row["replacement_currency"]
        ),
        applied_amount=money_from_minor_units(
            row["replacement_applied_amount_minor_units"], row["replacement_currency"]
        ),
        metadata=replacement_metadata,
        description=row["replacement_description"],
        original_event_id=UUID(row["target_id"]),
    )


def _decode_income(repository, row, replacement_metadata):
    return Income(
        account_id=UUID(row["replacement_account_id"]),
        allocation=_replacement_allocation(
            repository, row, replacement_metadata, require_matching_metadata=True
        ),
        description=row["replacement_description"],
        original_event_id=UUID(row["target_id"]),
    )


def _decode_cash_expense(repository, row, replacement_metadata):
    related = row["replacement_related_transfer_id"]
    return CashExpense(
        account_id=UUID(row["replacement_account_id"]),
        allocation=_replacement_allocation(
            repository, row, replacement_metadata, require_matching_metadata=True
        ),
        description=row["replacement_description"],
        related_transfer_id=None if related is None else UUID(related),
        original_event_id=UUID(row["target_id"]),
    )


def _decode_payable_opened(repository, row, replacement_metadata):
    return PayableOpened(
        allocation=_replacement_allocation(
            repository, row, replacement_metadata, require_matching_metadata=False
        ),
        due_date=row["replacement_due_date"],
        description=row["replacement_description"],
        original_event_id=UUID(row["target_id"]),
    )


def _decode_payable_charge(repository, row, replacement_metadata):
    return FinancialCharge(
        payable_id=UUID(row["replacement_payable_id"]),
        allocation=_replacement_allocation(
            repository, row, replacement_metadata, require_matching_metadata=False
        ),
        description=row["replacement_description"],
        original_event_id=UUID(row["target_id"]),
    )


def _decode_card_purchase(repository, row, replacement_metadata):
    return CardPurchase(
        card_id=UUID(row["replacement_card_id"]),
        allocation=_replacement_allocation(
            repository, row, replacement_metadata, require_matching_metadata=False
        ),
        invoice_closing_date=row["replacement_invoice_closing_date"],
        description=row["replacement_description"],
        original_event_id=UUID(row["target_id"]),
    )


def _decode_invoice_charge(repository, row, replacement_metadata):
    return InvoiceFinancialCharge(
        card_id=UUID(row["replacement_card_id"]),
        invoice_closing_date=row["replacement_invoice_closing_date"],
        allocation=_replacement_allocation(
            repository, row, replacement_metadata, require_matching_metadata=False
        ),
        description=row["replacement_description"],
        original_event_id=UUID(row["target_id"]),
    )


def _encode_transfer(fact):
    return {
        "replacement_source_account_id": str(fact.source_account_id),
        "replacement_destination_account_id": str(fact.destination_account_id),
    }


def _encode_allocated_account_fact(fact):
    return {
        "replacement_account_id": str(fact.account_id),
        "replacement_allocation_id": str(fact.allocation.allocation_id),
    }


def _encode_cash_expense(fact):
    return {
        **_encode_allocated_account_fact(fact),
        "replacement_related_transfer_id": (
            None if fact.related_transfer_id is None else str(fact.related_transfer_id)
        ),
    }


def _encode_bill_payment(fact):
    return {
        "replacement_account_id": str(fact.account_id),
        "replacement_payable_id": str(fact.payable_id),
    }


def _encode_invoice_payment(fact):
    return {
        "replacement_account_id": str(fact.account_id),
        "replacement_card_id": str(fact.card_id),
        "replacement_invoice_closing_date": fact.invoice_closing_date,
        "replacement_applied_amount_minor_units": money_to_minor_units(fact.applied_amount),
    }


def _encode_payable_opened(fact):
    return {
        "replacement_allocation_id": str(fact.allocation.allocation_id),
        "replacement_due_date": fact.due_date,
    }


def _encode_payable_charge(fact):
    return {
        "replacement_payable_id": str(fact.payable_id),
        "replacement_allocation_id": str(fact.allocation.allocation_id),
    }


def _encode_card_fact(fact):
    return {
        "replacement_card_id": str(fact.card_id),
        "replacement_invoice_closing_date": fact.invoice_closing_date,
        "replacement_allocation_id": str(fact.allocation.allocation_id),
    }


_FACT_CODECS = (
    _FinancialFactCodec("transfer", Transfer, _decode_transfer, _encode_transfer),
    _FinancialFactCodec("income", Income, _decode_income, _encode_allocated_account_fact),
    _FinancialFactCodec("cash_expense", CashExpense, _decode_cash_expense, _encode_cash_expense),
    _FinancialFactCodec("bill_payment", BillPayment, _decode_bill_payment, _encode_bill_payment),
    _FinancialFactCodec(
        "invoice_payment", InvoicePayment, _decode_invoice_payment, _encode_invoice_payment
    ),
    _FinancialFactCodec(
        "payable_opened", PayableOpened, _decode_payable_opened, _encode_payable_opened
    ),
    _FinancialFactCodec(
        "payable_financial_charge",
        FinancialCharge,
        _decode_payable_charge,
        _encode_payable_charge,
    ),
    _FinancialFactCodec("card_purchase", CardPurchase, _decode_card_purchase, _encode_card_fact),
    _FinancialFactCodec("installment_purchase", InstallmentPurchase, None, None),
    _FinancialFactCodec(
        "invoice_financial_charge",
        InvoiceFinancialCharge,
        _decode_invoice_charge,
        _encode_card_fact,
    ),
)
_CODECS_BY_KIND = {codec.kind: codec for codec in _FACT_CODECS}
_CODECS_BY_TYPE = {codec.fact_type: codec for codec in _FACT_CODECS}


def current_financial_fact(session: Session, original):
    rows = session.execute(
        select(financial_fact_changes)
        .where(financial_fact_changes.c.target_id == str(original.event_id))
        .order_by(financial_fact_changes.c.sequence)
    ).mappings()
    current = original
    repository = SqlAlchemyFinancialChangeRepository(session)
    for row in rows:
        change = repository.deserialize_change(row)
        current = None if isinstance(change, FinancialFactCanceled) else change.replacement
    return current
