from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from bot_financeiro.domain.credit_cards import (
    CardPurchase,
    InstallmentPurchase,
    InvoiceFinancialCharge,
)
from bot_financeiro.domain.payables import FinancialCharge, PayableOpened
from bot_financeiro.persistence.categories import (
    SqlAlchemyAllocationRepository,
    SqlAlchemyFinancialCategoryRepository,
)
from bot_financeiro.persistence.credit_cards import SqlAlchemyCreditCardRepository
from bot_financeiro.persistence.financial_changes import SqlAlchemyFinancialChangeRepository
from bot_financeiro.persistence.financial_operations import SqlAlchemyFinancialMovementRepository
from bot_financeiro.persistence.payables import SqlAlchemyPayableRepository
from bot_financeiro.persistence.resource_accounts import SqlAlchemyResourceAccountRepository
from bot_financeiro.persistence.unit_of_work import SqlAlchemyUnitOfWork


class SqlAlchemyFinancialChangeUnitOfWork(SqlAlchemyUnitOfWork):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__(session_factory)

    def __enter__(self) -> SqlAlchemyFinancialChangeUnitOfWork:
        super().__enter__()
        self.accounts = SqlAlchemyResourceAccountRepository(self.session)
        self.categories = SqlAlchemyFinancialCategoryRepository(self.session)
        self.allocations = SqlAlchemyAllocationRepository(self.session)
        self.movements = SqlAlchemyFinancialMovementRepository(self.session)
        self.credit_cards = SqlAlchemyCreditCardRepository(self.session)
        self.payables = SqlAlchemyPayableRepository(self.session)
        self.changes = SqlAlchemyFinancialChangeRepository(
            self.session,
            original_non_movement_loader=self._original_non_movement,
        )
        return self

    def _original_non_movement(self, event_id: UUID):
        event = self.payables.get_original_event(event_id)
        if isinstance(event, (PayableOpened, FinancialCharge)):
            return event
        event = self.credit_cards.get_original_event(event_id)
        if isinstance(event, (CardPurchase, InstallmentPurchase, InvoiceFinancialCharge)):
            return event
        raise LookupError(f"financial fact {event_id} does not exist")
