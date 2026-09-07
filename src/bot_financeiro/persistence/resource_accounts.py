from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.orm import Session

from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.resource_account import (
    AccountHistoryEntry,
    AccountHistoryEntryKind,
    AccountStatus,
    AccountType,
    ResourceAccount,
)
from bot_financeiro.persistence.money import (
    money_from_minor_units,
    money_to_minor_units,
)
from bot_financeiro.persistence.schema import metadata

resource_accounts = Table(
    "resource_accounts",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String, nullable=False),
    Column("account_type", String(16), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("balance_minor_units", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("allows_negative_balance", Boolean, nullable=False),
)

account_history_entries = Table(
    "account_history_entries",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "account_id",
        String(36),
        ForeignKey("resource_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("sequence", Integer, nullable=False),
    Column("kind", String(24), nullable=False),
    Column("difference_minor_units", Integer, nullable=False),
    Column("balance_minor_units", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("effective_date", Date, nullable=False),
    Column("recorded_at", String, nullable=False),
    Column("origin", String(16), nullable=False),
    Column("reason", String, nullable=True),
    Column(
        "movement_id",
        String(36),
        ForeignKey("financial_movements.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    UniqueConstraint("account_id", "sequence"),
)


class SqlAlchemyResourceAccountRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, account: ResourceAccount) -> None:
        self._session.execute(insert(resource_accounts).values(self._account_values(account)))
        self._append_history(account, start_at=0)

    def get(self, account_id: UUID) -> ResourceAccount | None:
        account_row = (
            self._session.execute(
                select(resource_accounts).where(resource_accounts.c.id == str(account_id))
            )
            .mappings()
            .one_or_none()
        )
        if account_row is None:
            return None

        history_rows = self._session.execute(
            select(account_history_entries)
            .where(account_history_entries.c.account_id == str(account_id))
            .order_by(account_history_entries.c.sequence)
        ).mappings()
        history = tuple(self._history_entry(row) for row in history_rows)
        return ResourceAccount(
            account_id=UUID(account_row["id"]),
            name=account_row["name"],
            account_type=AccountType(account_row["account_type"]),
            currency=account_row["currency"],
            balance=money_from_minor_units(
                account_row["balance_minor_units"], account_row["currency"]
            ),
            status=AccountStatus(account_row["status"]),
            allows_negative_balance=account_row["allows_negative_balance"],
            history=history,
        )

    def save(self, account: ResourceAccount) -> None:
        persisted = self.get(account.account_id)
        if persisted is None:
            raise LookupError(f"resource account {account.account_id} does not exist")
        persisted_history_size = len(persisted.history)
        if (
            len(account.history) < persisted_history_size
            or account.history[:persisted_history_size] != persisted.history
        ):
            raise ValueError("persisted history is immutable and can only be appended")

        self._session.execute(
            update(resource_accounts)
            .where(resource_accounts.c.id == str(account.account_id))
            .values(self._account_values(account))
        )
        last_persisted_sequence = self._session.scalar(
            select(account_history_entries.c.sequence)
            .where(account_history_entries.c.account_id == str(account.account_id))
            .order_by(account_history_entries.c.sequence.desc())
            .limit(1)
        )
        start_at = 0 if last_persisted_sequence is None else last_persisted_sequence + 1
        self._append_history(account, start_at=start_at)

    @staticmethod
    def _account_values(account: ResourceAccount) -> dict[str, object]:
        return {
            "id": str(account.account_id),
            "name": account.name,
            "account_type": account.account_type.value,
            "currency": account.currency,
            "balance_minor_units": money_to_minor_units(account.balance),
            "status": account.status.value,
            "allows_negative_balance": account.allows_negative_balance,
        }

    def _append_history(self, account: ResourceAccount, *, start_at: int) -> None:
        values = [
            {
                "id": str(entry.metadata.record_id),
                "account_id": str(account.account_id),
                "sequence": sequence,
                "kind": entry.kind.value,
                "difference_minor_units": money_to_minor_units(entry.difference),
                "balance_minor_units": money_to_minor_units(entry.balance),
                "currency": entry.balance.currency,
                "effective_date": entry.metadata.effective_date,
                "recorded_at": entry.metadata.recorded_at.isoformat(),
                "origin": entry.metadata.origin.value,
                "reason": entry.reason,
                "movement_id": None if entry.movement_id is None else str(entry.movement_id),
            }
            for sequence, entry in enumerate(account.history)
            if sequence >= start_at
        ]
        if values:
            self._session.execute(insert(account_history_entries), values)

    @staticmethod
    def _history_entry(row: object) -> AccountHistoryEntry:
        return AccountHistoryEntry(
            kind=AccountHistoryEntryKind(row["kind"]),
            difference=money_from_minor_units(row["difference_minor_units"], row["currency"]),
            balance=money_from_minor_units(row["balance_minor_units"], row["currency"]),
            metadata=RecordMetadata(
                effective_date=row["effective_date"],
                recorded_at=datetime.fromisoformat(row["recorded_at"]),
                origin=RecordOrigin(row["origin"]),
                record_id=UUID(row["id"]),
            ),
            reason=row["reason"],
            movement_id=None if row["movement_id"] is None else UUID(row["movement_id"]),
        )
