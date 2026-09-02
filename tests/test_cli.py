from bot_financeiro.__main__ import main


def test_cli_starts_the_local_server_on_loopback_by_default(monkeypatch) -> None:
    started_with: dict[str, object] = {}

    def record_server_start(app: str, **options: object) -> None:
        started_with["app"] = app
        started_with.update(options)

    monkeypatch.delenv("BOT_FINANCEIRO_HOST", raising=False)
    monkeypatch.delenv("BOT_FINANCEIRO_PORT", raising=False)
    monkeypatch.setattr("bot_financeiro.__main__.uvicorn.run", record_server_start)

    main()

    assert started_with == {
        "app": "bot_financeiro.interface.web:app",
        "host": "127.0.0.1",
        "port": 8000,
    }
