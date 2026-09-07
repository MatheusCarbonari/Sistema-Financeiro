from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.money import CurrencyMismatchError, Money
from bot_financeiro.domain.resource_account import (
    AccountHistoryEntry,
    AccountHistoryEntryKind,
    AccountStatus,
    AccountType,
    ResourceAccount,
)


def metadata(record_id: str, *, day: int = 1) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID(record_id),
    )


def test_open_account_preserves_registration_data_and_neutral_initial_balance() -> None:
    opened = metadata("f25bdac3-48a7-40a8-bc93-bf4d12245c9c")

    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1250.30", "BRL"),
        metadata=opened,
    )

    assert account.account_id == opened.record_id
    assert account.name == "Conta principal"
    assert account.account_type is AccountType.CHECKING
    assert account.currency == "BRL"
    assert account.balance == Money("1250.30", "BRL")
    assert account.status is AccountStatus.ACTIVE
    assert len(account.history) == 1
    initial_entry = account.history[0]
    assert initial_entry.kind is AccountHistoryEntryKind.INITIAL_BALANCE
    assert initial_entry.balance == Money("1250.30", "BRL")
    assert initial_entry.metadata == opened
    assert initial_entry.is_income is False
    assert initial_entry.is_expense is False


def test_account_requires_a_meaningful_name() -> None:
    with pytest.raises(ValueError, match="name"):
        ResourceAccount.open(
            name="   ",
            account_type=AccountType.CHECKING,
            currency="BRL",
            initial_balance=Money("0.00", "BRL"),
            metadata=metadata("e75f56e7-5dc1-4852-9007-31fc601b8aae"),
        )


def test_custom_account_requires_an_explicit_negative_balance_policy() -> None:
    opened = metadata("b9477f14-964e-4a37-aea0-8c49d9ce6077")

    with pytest.raises(ValueError, match="CUSTOM.*negative balance policy"):
        ResourceAccount.open(
            name="Conta personalizada",
            account_type=AccountType.CUSTOM,
            currency="BRL",
            initial_balance=Money("0.00", "BRL"),
            metadata=opened,
        )

    account = ResourceAccount.open(
        name="Conta personalizada",
        account_type=AccountType.CUSTOM,
        currency="BRL",
        initial_balance=Money("-10.00", "BRL"),
        metadata=opened,
        custom_allows_negative_balance=True,
    )

    assert account.allows_negative_balance is True


@pytest.mark.parametrize(
    ("account_type", "custom_policy", "allows_negative"),
    [
        (AccountType.CHECKING, None, True),
        (AccountType.DIGITAL, None, True),
        (AccountType.SAVINGS, None, True),
        (AccountType.WALLET, None, False),
        (AccountType.CUSTOM, False, False),
    ],
)
def test_negative_initial_balance_follows_the_account_type_policy(
    account_type: AccountType,
    custom_policy: bool | None,
    allows_negative: bool,
) -> None:
    def opening() -> ResourceAccount:
        return ResourceAccount.open(
            name="Conta",
            account_type=account_type,
            currency="BRL",
            initial_balance=Money("-0.01", "BRL"),
            metadata=metadata("5ebf0c0e-4e53-45ba-84ba-dc0d7a3bb46f"),
            custom_allows_negative_balance=custom_policy,
        )

    if allows_negative:
        assert opening().balance == Money("-0.01", "BRL")
    else:
        with pytest.raises(ValueError, match="negative balance"):
            opening()


def test_adjustment_sets_the_real_balance_and_records_the_exact_difference() -> None:
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("125.30", "BRL"),
        metadata=metadata("db0e19ec-b35a-40b0-978b-eb83e756dc7f"),
    )
    adjustment = metadata("8ec09503-3ec5-4309-b809-45fd14adf7a2", day=2)

    adjusted = account.adjust_balance(
        actual_balance=Money("100.00", "BRL"),
        reason="Conciliação com o extrato",
        metadata=adjustment,
    )

    assert account.balance == Money("125.30", "BRL")
    assert len(account.history) == 1
    assert adjusted.balance == Money("100.00", "BRL")
    assert len(adjusted.history) == 2
    entry = adjusted.history[-1]
    assert entry.kind is AccountHistoryEntryKind.BALANCE_ADJUSTMENT
    assert entry.difference == Money("-25.30", "BRL")
    assert entry.balance == Money("100.00", "BRL")
    assert entry.reason == "Conciliação com o extrato"
    assert entry.metadata == adjustment
    assert entry.is_income is False
    assert entry.is_expense is False


def test_adjustment_requires_a_meaningful_reason() -> None:
    account = ResourceAccount.open(
        name="Carteira",
        account_type=AccountType.WALLET,
        currency="BRL",
        initial_balance=Money("10.00", "BRL"),
        metadata=metadata("a706d717-da95-4afd-a6dc-c79ee7a298bc"),
    )

    with pytest.raises(ValueError, match="reason"):
        account.adjust_balance(
            actual_balance=Money("9.00", "BRL"),
            reason="   ",
            metadata=metadata("0fa302a4-2f32-47d5-8540-4d27382497f5", day=2),
        )


def test_account_accepts_only_values_in_its_own_currency() -> None:
    opened = metadata("9f649fc9-c0e0-4f0b-a69f-5a73fe79d4d5")

    with pytest.raises(CurrencyMismatchError, match="BRL.*USD"):
        ResourceAccount.open(
            name="Conta",
            account_type=AccountType.CHECKING,
            currency="BRL",
            initial_balance=Money("10.00", "USD"),
            metadata=opened,
        )

    account = ResourceAccount.open(
        name="Conta",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("10.00", "BRL"),
        metadata=opened,
    )
    with pytest.raises(CurrencyMismatchError, match="BRL.*USD"):
        account.adjust_balance(
            actual_balance=Money("9.00", "USD"),
            reason="Conciliação",
            metadata=metadata("c385fc73-f083-429f-b4bc-3f8958f603d6", day=2),
        )


@pytest.mark.parametrize(
    ("account_type", "custom_policy", "allows_negative"),
    [
        (AccountType.CHECKING, None, True),
        (AccountType.DIGITAL, None, True),
        (AccountType.SAVINGS, None, True),
        (AccountType.WALLET, None, False),
        (AccountType.CUSTOM, True, True),
        (AccountType.CUSTOM, False, False),
    ],
)
def test_negative_adjustment_follows_the_account_policy(
    account_type: AccountType,
    custom_policy: bool | None,
    allows_negative: bool,
) -> None:
    account = ResourceAccount.open(
        name="Conta",
        account_type=account_type,
        currency="BRL",
        initial_balance=Money("0.00", "BRL"),
        metadata=metadata("e807ee79-7871-4a31-9c96-8e6d70bc0fba"),
        custom_allows_negative_balance=custom_policy,
    )

    def adjustment() -> ResourceAccount:
        return account.adjust_balance(
            actual_balance=Money("-1.00", "BRL"),
            reason="Conciliação",
            metadata=metadata("2fb18c96-43e3-42f4-80f9-c3d5eda65944", day=2),
        )

    if allows_negative:
        assert adjustment().balance == Money("-1.00", "BRL")
    else:
        with pytest.raises(ValueError, match="negative balance"):
            adjustment()


def test_account_can_be_archived_only_with_zero_balance() -> None:
    nonzero_account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("0.01", "BRL"),
        metadata=metadata("4c2acdd8-e112-486d-9348-4212215355fd"),
    )

    with pytest.raises(ValueError, match="zero balance"):
        nonzero_account.archive()

    zero_account = nonzero_account.adjust_balance(
        actual_balance=Money("0.00", "BRL"),
        reason="Zerar antes do arquivamento",
        metadata=metadata("b9cb0bfc-6600-4e6e-bc77-f5b47bf06358", day=2),
    )
    archived = zero_account.archive()

    assert archived.status is AccountStatus.ARCHIVED
    assert archived.history == zero_account.history


def test_archived_account_rejects_adjustments_and_can_be_reactivated() -> None:
    account = ResourceAccount.open(
        name="Conta reserva",
        account_type=AccountType.SAVINGS,
        currency="BRL",
        initial_balance=Money("0.00", "BRL"),
        metadata=metadata("e7326491-5d33-4e92-b44a-9e4db4bc3a4d"),
    )
    archived = account.archive()

    with pytest.raises(ValueError, match="archived"):
        archived.adjust_balance(
            actual_balance=Money("1.00", "BRL"),
            reason="Não permitido",
            metadata=metadata("b0f194c4-033b-4482-8124-2648b4d27c09", day=2),
        )

    reactivated = archived.reactivate()
    adjusted = reactivated.adjust_balance(
        actual_balance=Money("1.00", "BRL"),
        reason="Conta em uso novamente",
        metadata=metadata("d3c5950e-e19e-4b2d-8a90-101b3771bc1c", day=3),
    )

    assert reactivated.status is AccountStatus.ACTIVE
    assert reactivated.history == archived.history
    assert adjusted.balance == Money("1.00", "BRL")


def test_account_snapshot_cannot_diverge_from_its_history() -> None:
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("10.00", "BRL"),
        metadata=metadata("3af8c31a-b616-4192-b33f-1f6ab42517c1"),
    )

    with pytest.raises(ValueError, match="history"):
        replace(account, balance=Money("11.00", "BRL"))


@pytest.mark.parametrize(
    ("account_type", "invalid_policy"),
    [
        (AccountType.WALLET, True),
        (AccountType.CHECKING, False),
        (AccountType.DIGITAL, False),
        (AccountType.SAVINGS, False),
    ],
)
def test_non_custom_account_policy_cannot_be_overridden(
    account_type: AccountType,
    invalid_policy: bool,
) -> None:
    account = ResourceAccount.open(
        name="Conta",
        account_type=account_type,
        currency="BRL",
        initial_balance=Money("0.00", "BRL"),
        metadata=metadata("fd8928c6-7dc6-4fbc-904e-df6946ca6313"),
    )

    with pytest.raises(ValueError, match="negative balance policy"):
        replace(account, allows_negative_balance=invalid_policy)


def test_account_identity_is_bound_to_its_initial_history_entry() -> None:
    account = ResourceAccount.open(
        name="Conta",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("0.00", "BRL"),
        metadata=metadata("434ca18e-c438-4d75-b431-430354cf7ca1"),
    )

    with pytest.raises(ValueError, match="identity"):
        replace(account, account_id=UUID("ba7a44d0-6c9f-4c8e-bfa8-680df367d040"))


@pytest.mark.parametrize(
    ("kind", "difference"),
    [
        (AccountHistoryEntryKind.INCOME, Money("-1.00", "BRL")),
        (AccountHistoryEntryKind.TRANSFER_IN, Money("-1.00", "BRL")),
        (AccountHistoryEntryKind.CASH_EXPENSE, Money("1.00", "BRL")),
        (AccountHistoryEntryKind.TRANSFER_OUT, Money("1.00", "BRL")),
    ],
)
def test_realized_account_history_requires_a_difference_in_its_direction(
    kind: AccountHistoryEntryKind,
    difference: Money,
) -> None:
    with pytest.raises(ValueError, match="direction"):
        AccountHistoryEntry(
            kind=kind,
            difference=difference,
            balance=Money("10.00", "BRL"),
            metadata=metadata("40cf5c38-05aa-4368-9cdd-a98fa6420286"),
            movement_id=UUID("a2a3a073-85f3-45ba-bf29-49399113dfe0"),
        )
