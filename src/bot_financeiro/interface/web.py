from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bot_financeiro.config import Settings

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
TEMPLATES_DIR = PACKAGE_DIR / "templates"


@dataclass(frozen=True, slots=True)
class Page:
    slug: str
    title: str
    description: str


PAGES = (
    Page("resumo", "Resumo", "Sua visão financeira em um só lugar."),
    Page("lancamentos", "Lançamentos", "Registre e consulte suas movimentações."),
    Page("contas", "Contas", "Organize suas contas de recursos."),
    Page("cartoes", "Cartões", "Acompanhe seus cartões e faturas."),
    Page("planejamento", "Planejamento", "Veja previsões e compromissos futuros."),
    Page("configuracoes", "Configurações", "Ajuste as preferências locais do aplicativo."),
)
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "img-src 'self' data:; "
    "object-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "worker-src 'self'"
)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()
    app = FastAPI(title="Bot Financeiro", docs_url=None, redoc_url=None)
    app.state.settings = app_settings
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    templates = Jinja2Templates(directory=TEMPLATES_DIR)

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse(url="/resumo")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "manifest.webmanifest",
            media_type="application/manifest+json",
        )

    @app.get("/service-worker.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "service-worker.js",
            media_type="text/javascript",
            headers={
                "Cache-Control": "no-cache",
                "Service-Worker-Allowed": "/",
            },
        )

    def page_handler(page: Page):
        def render_page(request: Request):
            response = templates.TemplateResponse(
                request=request,
                name="page.html",
                context={
                    "current_page": page.slug,
                    "description": page.description,
                    "locale": app_settings.locale,
                    "navigation": PAGES,
                    "title": page.title,
                },
            )
            response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
            return response

        return render_page

    for page in PAGES:
        app.add_api_route(
            f"/{page.slug}",
            page_handler(page),
            methods=["GET"],
            name=page.slug,
        )

    return app


app = create_app()
