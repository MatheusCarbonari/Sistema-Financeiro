# Limites e direção das camadas

Status: accepted

O sistema será dividido em domínio, aplicação, persistência e interface, com dependências apontando para dentro: interfaces e persistência dependem dos contratos da aplicação, e a aplicação depende do domínio. O domínio financeiro será Python puro e não importará FastAPI, Jinja2, Pydantic, SQLAlchemy ou SQLite, permitindo que futuras interfaces reutilizem os mesmos casos de uso e regras.

## Limites

- **Domínio:** Valores monetários, entidades, estados, invariantes e políticas financeiras; não conhece HTTP, formulários, sessões de banco ou modelos ORM.
- **Aplicação:** casos de uso, autorizações locais, Propostas de lançamento, confirmação, coordenação de repositórios e fronteiras transacionais; expõe entradas e resultados próprios, sem objetos HTTP ou ORM.
- **Persistência:** mapeamentos SQLAlchemy, repositórios, unidade de trabalho, conexão SQLite e migrações Alembic; traduz entre armazenamento e domínio sem decidir regras financeiras.
- **Interface:** rotas FastAPI, páginas Jinja2, formulários, JSON e conversa determinística; interpreta entrada, apresenta resultados e chama casos de uso, sem acessar o banco diretamente.
- **Composição:** inicialização e `pydantic-settings` conectam implementações aos contratos; configuração não invade o domínio.

## Consequências

- Telegram, uma futura API ou outra interface chamará a camada de aplicação em vez de duplicar regras.
- Modelos ORM não serão usados como entidades de domínio nem retornados diretamente pela interface.
- Regras financeiras terão testes sem servidor HTTP e sem banco real; adaptadores terão testes próprios de integração.
