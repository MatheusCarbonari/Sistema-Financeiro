from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

BRL_CENT = Decimal("0.01")


class CurrencyMismatchError(ValueError):
    """A operação exige Valores monetários na mesma moeda."""


@dataclass(frozen=True, slots=True, init=False)
class Money:
    amount: Decimal
    currency: str

    def __init__(self, amount: Decimal | str, currency: str) -> None:
        if not isinstance(amount, (Decimal, str)):
            raise TypeError("amount must be Decimal or text")
        if (
            not isinstance(currency, str)
            or len(currency) != 3
            or not currency.isascii()
            or not currency.isalpha()
            or not currency.isupper()
        ):
            raise ValueError("currency must be a three-letter uppercase code")

        exact_amount = Decimal(amount)
        if currency == "BRL":
            exact_amount = exact_amount.quantize(BRL_CENT, rounding=ROUND_HALF_UP)

        object.__setattr__(self, "amount", exact_amount)
        object.__setattr__(self, "currency", currency)

    def __add__(self, other: object) -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"cannot add {self.currency} and {other.currency} without conversion"
            )
        return Money(self.amount + other.amount, self.currency)

    def split_equally(self, number_of_parts: int) -> tuple["Money", ...]:
        if number_of_parts < 1:
            raise ValueError("number_of_parts must be positive")
        if self.currency != "BRL":
            raise ValueError("equal splitting is only defined for BRL in the MVP")

        sign = -1 if self.amount < 0 else 1
        absolute_cents = int(abs(self.amount) / BRL_CENT)
        cents_per_part, residual_cents = divmod(absolute_cents, number_of_parts)

        return tuple(
            Money(
                Decimal(sign * (cents_per_part + (index < residual_cents))) * BRL_CENT,
                self.currency,
            )
            for index in range(number_of_parts)
        )
