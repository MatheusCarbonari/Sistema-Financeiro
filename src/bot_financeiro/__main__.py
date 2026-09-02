import uvicorn

from bot_financeiro.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        "bot_financeiro.interface.web:app",
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
