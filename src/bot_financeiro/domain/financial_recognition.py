from bot_financeiro.domain.categories import FinancialNature


def recognized_financial_nature(fact: object) -> FinancialNature | None:
    """Return when a domain fact recognizes income or expense at its effective date."""
    is_income = getattr(fact, "is_income", False) is True
    is_expense = getattr(fact, "is_expense", False) is True
    if is_income and is_expense:
        raise ValueError("a financial fact cannot recognize income and expense together")
    if is_income:
        return FinancialNature.INCOME
    if is_expense:
        return FinancialNature.EXPENSE
    return None
