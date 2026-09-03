from decimal import Decimal

from bot_financeiro.domain.money import Money

_CENTS_PER_BRL = Decimal(100)


def money_to_minor_units(value: Money) -> int:
    if value.currency != "BRL":
        raise ValueError("persistence supports BRL minor units in the MVP")
    return int(value.amount * _CENTS_PER_BRL)


def money_from_minor_units(amount: int, currency: str) -> Money:
    if currency != "BRL":
        raise ValueError("persistence supports BRL minor units in the MVP")
    return Money(Decimal(amount) / _CENTS_PER_BRL, currency)
