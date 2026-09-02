from fastapi.testclient import TestClient

from bot_financeiro.interface.web import create_app

PAGES = {
    "/resumo": "Resumo",
    "/lancamentos": "Lançamentos",
    "/contas": "Contas",
    "/cartoes": "Cartões",
    "/planejamento": "Planejamento",
    "/configuracoes": "Configurações",
}


def test_local_shell_exposes_all_primary_pages_and_navigation() -> None:
    client = TestClient(create_app())

    home = client.get("/", follow_redirects=False)
    assert home.status_code == 307
    assert home.headers["location"] == "/resumo"

    for path, heading in PAGES.items():
        response = client.get(path)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert '<html lang="pt-BR">' in response.text
        assert f"<h1>{heading}</h1>" in response.text
        for navigation_path, navigation_label in PAGES.items():
            assert f'href="{navigation_path}"' in response.text
            assert navigation_label in response.text


def test_shell_exposes_a_minimal_local_pwa_contract() -> None:
    client = TestClient(create_app())

    page = client.get("/resumo")
    manifest_response = client.get("/manifest.webmanifest")
    service_worker = client.get("/service-worker.js")
    registration = client.get("/static/app.js")

    assert '<link rel="manifest" href="/manifest.webmanifest">' in page.text
    assert '<script src="/static/app.js" defer></script>' in page.text

    assert manifest_response.status_code == 200
    assert manifest_response.headers["content-type"].startswith("application/manifest+json")
    manifest = manifest_response.json()
    assert manifest["name"] == "Bot Financeiro"
    assert manifest["short_name"] == "Financeiro"
    assert manifest["lang"] == "pt-BR"
    assert manifest["start_url"] == "/resumo"
    assert manifest["display"] == "standalone"
    assert manifest["icons"]
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200

    assert service_worker.status_code == 200
    assert service_worker.headers["content-type"].startswith("text/javascript")
    assert "/resumo" in service_worker.text
    assert "/static/styles.css" in service_worker.text
    assert registration.status_code == 200
    assert 'navigator.serviceWorker.register("/service-worker.js")' in registration.text


def test_shell_restricts_browser_dependencies_to_its_own_origin() -> None:
    client = TestClient(create_app())

    for path in PAGES:
        response = client.get(path)

        assert response.headers["content-security-policy"] == (
            "default-src 'self'; "
            "connect-src 'self'; "
            "form-action 'self'; "
            "img-src 'self' data:; "
            "object-src 'none'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "worker-src 'self'"
        )
