def normalize_financial_description(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("financial description is required")
    return value.strip()


def validate_persisted_financial_description(value: str | None) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError("financial description is required")
    if value != value.strip():
        raise ValueError("financial description must be normalized")
