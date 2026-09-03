from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID

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


@dataclass(frozen=True, slots=True)
class AccountHistoryEntry:
    kind: AccountHistoryEntryKind
    difference: Money
    balance: Money
    metadata: RecordMetadata
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.difference.currency != self.balance.currency:
            raise CurrencyMismatchError("history values must use the same currency")
        if (
            self.kind is AccountHistoryEntryKind.BALANCE_ADJUSTMENT
            and not (self.reason or "").strip()
        ):
            raise ValueError("balance adjustment history requires a reason")

    @property
    def is_income(self) -> bool:
        return False

    @property
    def is_expense(self) -> bool:
        return False


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
        if self.balance.currency != self.currency:
            raise CurrencyMismatchError("account balance must use the account currency")
        if self.balance.amount < 0 and not self.allows_negative_balance:
            raise ValueError("negative balance is not allowed for this account")
        if self.status is AccountStatus.ARCHIVED and self.balance.amount != 0:
            raise ValueError("archived account must have zero balance")
        if not isinstance(self.history, tuple) or not self.history:
            raise ValueError("account history must be a non-empty immutable tuple")
        if self.history[0].kind is not AccountHistoryEntryKind.INITIAL_BALANCE:
            raise ValueError("account history must begin with the initial balance")

        previous_balance = None
        seen_record_ids: set[UUID] = set()
        for index, entry in enumerate(self.history):
            if entry.metadata.record_id in seen_record_ids:
                raise ValueError("account history record identities must be unique")
            seen_record_ids.add(entry.metadata.record_id)
            if entry.balance.currency != self.currency:
                raise CurrencyMismatchError("account history must use the account currency")
            if index == 0:
                if entry.difference.amount != entry.balance.amount:
                    raise ValueError("initial history difference must equal its balance")
            else:
                if entry.kind is AccountHistoryEntryKind.INITIAL_BALANCE:
                    raise ValueError("initial balance can appear only once in account history")
                expected_difference = entry.balance.amount - previous_balance.amount
                if entry.difference.amount != expected_difference:
                    raise ValueError("account history contains an inconsistent difference")
            previous_balance = entry.balance

        if self.history[-1].balance.amount != self.balance.amount:
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
