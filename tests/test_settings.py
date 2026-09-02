import pytest
from pydantic import ValidationError

from bot_financeiro.config import Settings


def test_default_settings_keep_the_app_local_and_use_brazilian_locale(
    monkeypatch,
) -> None:
    for variable in (
        "BOT_FINANCEIRO_HOST",
        "BOT_FINANCEIRO_PORT",
        "BOT_FINANCEIRO_LOCALE",
        "BOT_FINANCEIRO_TIMEZONE",
    ):
        monkeypatch.delenv(variable, raising=False)

    settings = Settings(_env_file=None)

    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.locale == "pt-BR"
    assert settings.timezone == "America/Sao_Paulo"


def test_settings_reject_a_network_exposed_host(monkeypatch) -> None:
    monkeypatch.setenv("BOT_FINANCEIRO_HOST", "0.0.0.0")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
