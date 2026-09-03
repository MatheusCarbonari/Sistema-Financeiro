from collections.abc import Callable
from decimal import Decimal
from operator import eq, lt

import pytest

from bot_financeiro.domain.money import CurrencyMismatchError, Money


@pytest.mark.parametrize("currency", ["", None, "R$"])
def test_money_requires_a_three_letter_currency_code(currency: str | None) -> None:
    with pytest.raises(ValueError, match="currency"):
        Money("10.00", currency)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw_amount", "expected"),
    [
        ("10.004", Decimal("10.00")),
        ("10.005", Decimal("10.01")),
    ],
)
def test_brl_money_rounds_half_up_to_cents(raw_amount: str, expected: Decimal) -> None:
    assert Money(raw_amount, "BRL").amount == expected


def test_money_rejects_float_amounts() -> None:
    with pytest.raises(TypeError, match="Decimal or text"):
        Money(10.01, "BRL")


def test_money_does_not_add_different_currencies_implicitly() -> None:
    with pytest.raises(CurrencyMismatchError):
        Money("10.00", "BRL") + Money("2.00", "USD")


@pytest.mark.parametrize("compare", [eq, lt])
def test_money_does_not_compare_different_currencies_implicitly(
    compare: Callable[[Money, Money], bool],
) -> None:
    with pytest.raises(CurrencyMismatchError):
        compare(Money("10.00", "BRL"), Money("10.00", "USD"))


def test_equal_split_preserves_total_and_assigns_residual_cents_first() -> None:
    parts = Money("10.00", "BRL").split_equally(3)

    assert parts == (
        Money("3.34", "BRL"),
        Money("3.33", "BRL"),
        Money("3.33", "BRL"),
    )
    assert parts[0] + parts[1] + parts[2] == Money("10.00", "BRL")
