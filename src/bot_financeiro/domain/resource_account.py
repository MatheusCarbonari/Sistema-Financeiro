from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID, uuid4

from bot_financeiro.domain.audit import RecordMetadata
from bot_financeiro.domain.money import CurrencyMismatchError, Money


class AccountType(StrEnum):
    WALLET = "wallet"
    CHECKING = "checking"
    DIGITAL = "digital"
    SAVINGS = "savings"
    CUSTOM = "custom"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class AccountHistoryEntryKind(StrEnum):
    INITIAL_BALANCE = "initial_balance"
    BALANCE_ADJUSTMENT = "balance_adjustment"
    INCOME = "income"
    CASH_EXPENSE = "cash_expense"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"


@dataclass(frozen=True, slots=True)
class AccountHistoryEntry:
    kind: AccountHistoryEntryKind
    difference: Money
    balance: Money
    metadata: RecordMetadata
    reason: str | None = None
    movement_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.difference.currency != self.balance.currency:
            raise CurrencyMismatchError("history values must use the same currency")
        if (
            self.kind is AccountHistoryEntryKind.BALANCE_ADJUSTMENT
            and not (self.reason or "").strip()
        ):
            raise ValueError("balance adjustment history requires a reason")
        realized_kinds = {
            AccountHistoryEntryKind.INCOME,
            AccountHistoryEntryKind.CASH_EXPENSE,
            AccountHistoryEntryKind.TRANSFER_IN,
            AccountHistoryEntryKind.TRANSFER_OUT,
        }
        if self.kind in realized_kinds and self.movement_id is None:
            raise ValueError("realized account history requires a movement reference")
        if self.kind not in realized_kinds and self.movement_id is not None:
            raise ValueError("neutral account history cannot reference a realized movement")
        incoming_kinds = {
            AccountHistoryEntryKind.INCOME,
            AccountHistoryEntryKind.TRANSFER_IN,
        }
        outgoing_kinds = {
            AccountHistoryEntryKind.CASH_EXPENSE,
            AccountHistoryEntryKind.TRANSFER_OUT,
        }
        if self.kind in incoming_kinds and self.difference.amount <= 0:
            raise ValueError("account history difference conflicts with movement direction")
        if self.kind in outgoing_kinds and self.difference.amount >= 0:
            raise ValueError("account history difference conflicts with movement direction")

    @property
    def is_income(self) -> bool:
        return self.kind is AccountHistoryEntryKind.INCOME

    @property
    def is_expense(self) -> bool:
        return self.kind is AccountHistoryEntryKind.CASH_EXPENSE


@dataclass(frozen=True, slots=True)
class AccountStatement:
    account_id: UUID
    currency: str
    entries: tuple[AccountHistoryEntry, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.entries, tuple) or not self.entries:
            raise ValueError("account history must be a non-empty immutable tuple")
        if self.entries[0].kind is not AccountHistoryEntryKind.INITIAL_BALANCE:
            raise ValueError("account history must begin with the initial balance")
        if self.account_id != self.entries[0].metadata.record_id:
            raise ValueError("account identity must match its initial history entry")

        seen_record_ids: set[UUID] = set()
        for index, entry in enumerate(self.entries):
            if entry.metadata.record_id in seen_record_ids:
                raise ValueError("account history record identities must be unique")
            seen_record_ids.add(entry.metadata.record_id)
            if entry.balance.currency != self.currency:
                raise CurrencyMismatchError("account history must use the account currency")
            if index > 0 and entry.kind is AccountHistoryEntryKind.INITIAL_BALANCE:
                raise ValueError("initial balance can appear only once in account history")

        self._recalculate_balance()

    @property
    def balance(self) -> Money:
        return self._recalculate_balance()

    def _recalculate_balance(self) -> Money:
        balance = Money("0", self.currency)
        for entry in self.entries:
            if entry.difference.currency != self.currency:
                raise CurrencyMismatchError("account statement must use the account currency")
            balance += entry.difference
            if balance != entry.balance:
                raise ValueError("account history contains an inconsistent balance")
        return balance


@dataclass(frozen=True, slots=True)
class ResourceAccount:
    account_id: UUID
    name: str
    account_type: AccountType
    currency: str
    balance: Money
    status: AccountStatus
    allows_negative_balance: bool
    history: tuple[AccountHistoryEntry, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("account name is required")
        if not isinstance(self.account_type, AccountType):
            raise ValueError("account type is invalid")
        if not isinstance(self.status, AccountStatus):
            raise ValueError("account status is invalid")
        if not isinstance(self.allows_negative_balance, bool):
            raise ValueError("negative balance policy must be boolean")
        if self.account_type is not AccountType.CUSTOM:
            expected_policy = self.account_type in {
                AccountType.CHECKING,
                AccountType.DIGITAL,
                AccountType.SAVINGS,
            }
            if self.allows_negative_balance is not expected_policy:
                raise ValueError("negative balance policy does not match account type")
        if self.balance.currency != self.currency:
            raise CurrencyMismatchError("account balance must use the account currency")
        if self.balance.amount < 0 and not self.allows_negative_balance:
            raise ValueError("negative balance is not allowed for this account")
        if self.status is AccountStatus.ARCHIVED and self.balance.amount != 0:
            raise ValueError("archived account must have zero balance")
        if self.statement().balance != self.balance:
            raise ValueError("account balance must match the latest history entry")

    def adjust_balance(
        self,
        *,
        actual_balance: Money,
        reason: str,
        metadata: RecordMetadata,
    ) -> "ResourceAccount":
        if self.status is AccountStatus.ARCHIVED:
            raise ValueError("archived account rejects new adjustments")
        self._require_own_currency(actual_balance)
        if actual_balance.amount < 0 and not self.allows_negative_balance:
            raise ValueError("negative balance is not allowed for this account")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("balance adjustment reason is required")
        difference = Money(
            actual_balance.amount - self.balance.amount,
            self.currency,
        )
        entry = AccountHistoryEntry(
            kind=AccountHistoryEntryKind.BALANCE_ADJUSTMENT,
            difference=difference,
            balance=actual_balance,
            reason=normalized_reason,
            metadata=metadata,
        )
        return replace(
            self,
            balance=actual_balance,
            history=(*self.history, entry),
        )

    def record_movement(
        self,
        *,
        kind: AccountHistoryEntryKind,
        difference: Money,
        movement_metadata: RecordMetadata,
    ) -> "ResourceAccount":
        if kind not in {
            AccountHistoryEntryKind.INCOME,
            AccountHistoryEntryKind.CASH_EXPENSE,
            AccountHistoryEntryKind.TRANSFER_IN,
            AccountHistoryEntryKind.TRANSFER_OUT,
        }:
            raise ValueError("kind is not a realized account movement")
        if self.status is AccountStatus.ARCHIVED:
            raise ValueError("archived account rejects new movements")
        self._require_own_currency(difference)
        new_balance = self.balance + difference
        if new_balance.amount < 0 and not self.allows_negative_balance:
            raise ValueError("negative balance is not allowed for this account")
        entry_metadata = replace(movement_metadata, record_id=uuid4())
        entry = AccountHistoryEntry(
            kind=kind,
            difference=difference,
            balance=new_balance,
            metadata=entry_metadata,
            movement_id=movement_metadata.record_id,
        )
        return replace(self, balance=new_balance, history=(*self.history, entry))

    def statement(self) -> AccountStatement:
        return AccountStatement(
            account_id=self.account_id,
            currency=self.currency,
            entries=self.history,
        )

    def archive(self) -> "ResourceAccount":
        if self.balance.amount != 0:
            raise ValueError("account must have zero balance before archiving")
        return replace(self, status=AccountStatus.ARCHIVED)

    def reactivate(self) -> "ResourceAccount":
        return replace(self, status=AccountStatus.ACTIVE)

    @classmethod
    def open(
        cls,
        *,
        name: str,
        account_type: AccountType,
        currency: str,
        initial_balance: Money,
        metadata: RecordMetadata,
        custom_allows_negative_balance: bool | None = None,
    ) -> "ResourceAccount":
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("account name is required")
        if initial_balance.currency != currency:
            raise CurrencyMismatchError(
                f"account uses {currency}, but the supplied value uses {initial_balance.currency}"
            )
        if account_type is AccountType.CUSTOM:
            if custom_allows_negative_balance is None:
                raise ValueError("CUSTOM requires an explicit negative balance policy")
            allows_negative_balance = custom_allows_negative_balance
        else:
            allows_negative_balance = account_type in {
                AccountType.CHECKING,
                AccountType.DIGITAL,
                AccountType.SAVINGS,
            }
        if initial_balance.amount < 0 and not allows_negative_balance:
            raise ValueError("negative balance is not allowed for this account")
        initial_entry = AccountHistoryEntry(
            kind=AccountHistoryEntryKind.INITIAL_BALANCE,
            difference=initial_balance,
            balance=initial_balance,
            metadata=metadata,
        )
        return cls(
            account_id=metadata.record_id,
            name=normalized_name,
            account_type=account_type,
            currency=currency,
            balance=initial_balance,
            status=AccountStatus.ACTIVE,
            allows_negative_balance=allows_negative_balance,
            history=(initial_entry,),
        )

    def _require_own_currency(self, value: Money) -> None:
        if value.currency != self.currency:
            raise CurrencyMismatchError(
                f"account uses {self.currency}, but the supplied value uses {value.currency}"
            )
