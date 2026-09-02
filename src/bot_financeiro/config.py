from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração da aplicação carregada apenas do ambiente local."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="BOT_FINANCEIRO_",
        extra="ignore",
    )

    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = 8000
    locale: str = "pt-BR"
    timezone: str = "America/Sao_Paulo"
