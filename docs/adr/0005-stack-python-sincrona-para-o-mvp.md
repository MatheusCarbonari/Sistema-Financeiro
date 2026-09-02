# Stack Python síncrona para o MVP

Status: accepted

O MVP usará Python com FastAPI e Uvicorn para o servidor HTTP local, Jinja2 e `python-multipart` para a interface renderizada no servidor, `pydantic-settings` para configuração, SQLAlchemy síncrono sobre SQLite e Alembic para migrações. Essa stack reduz infraestrutura e ferramentas de frontend, executa sem nuvem e mantém o produto acessível como Web/PWA; `async`, `aiosqlite`, SPA e serviços externos foram rejeitados no MVP porque a carga local de um único Titular não justifica sua complexidade.

## Consequências

- A interface usará HTML renderizado no servidor, recursos nativos do navegador e JavaScript estritamente necessário; manifesto e service worker da PWA não exigirão framework adicional.
- `pytest`, `httpx` e `ruff` formarão a base de desenvolvimento; `pytest-playwright` e seu navegador serão adicionados somente quando começarem os testes reais de interface.
- Versões exatas serão fixadas no manifesto e no arquivo de lock criados durante o scaffold, após verificação de compatibilidade com a versão de Python escolhida.
