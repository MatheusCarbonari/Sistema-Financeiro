from dataclasses import dataclass

from bot_financeiro.domain.categories import FinancialNature
from bot_financeiro.domain.financial_recognition import recognized_financial_nature


@dataclass(frozen=True)
class FinancialFactStub:
    is_income: bool = False
    is_expense: bool = False


def test_recognition_uses_the_financial_semantics_exposed_by_the_fact() -> None:
    assert recognized_financial_nature(FinancialFactStub(is_income=True)) is (
        FinancialNature.INCOME
    )
    assert recognized_financial_nature(FinancialFactStub(is_expense=True)) is (
        FinancialNature.EXPENSE
    )
    assert recognized_financial_nature(FinancialFactStub()) is None
