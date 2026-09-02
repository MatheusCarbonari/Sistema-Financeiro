# Decidir stack e persistência local

Status: resolved

## Objetivo

Escolher a base técnica da aplicação Web/PWA e da persistência local sem acoplar o domínio financeiro ao canal de interação.

## Escopo

- Comparar opções de frontend, backend local e armazenamento persistente.
- Garantir suporte a transações atômicas, valores monetários exatos, histórico auditável e backups consistentes.
- Definir limites entre domínio, aplicação, persistência e interfaces.
- Registrar em ADR as escolhas que criarem dependência de longo prazo.

## Critérios de aceite

- [x] A stack escolhida executa integralmente no computador do Titular.
- [x] O desenho não exige nuvem, serviço externo ou modelo de IA.
- [x] O núcleo financeiro pode ser utilizado por interfaces futuras sem depender da Web/PWA.
- [x] Migrações, transações, backup e restauração possuem uma estratégia definida.
- [x] As decisões difíceis de reverter estão registradas em ADR.

## Answer

A stack aprovada é FastAPI/Uvicorn com Jinja2 e `python-multipart`, SQLAlchemy síncrono sobre SQLite, Alembic, `Decimal` e `pydantic-settings`. O desenvolvimento usará `pytest`, `httpx` e `ruff`; `pytest-playwright` será adicionado quando os testes reais da interface começarem, e `async`/`aiosqlite` ficam explicitamente fora do MVP.

O domínio permanece Python puro; a aplicação coordena casos de uso e transações; a persistência implementa repositórios, unidade de trabalho, ORM, SQLite e migrações; a interface traduz HTTP, formulários e conversa determinística sem acessar o banco diretamente. Cada comando confirmado usa uma transação curta e atômica, migrações Alembic recebem backup prévio, backups usam snapshots consistentes com validação, e dinheiro usa `Decimal` no domínio e centavos inteiros mais moeda na persistência.

## Referências

- `spec.md`: Canal do produto, Acesso e segurança, Backup e portabilidade.
- ADR-0003 a ADR-0009.

## Comments

- 2026-09-02: stack e estratégias aprovadas; decisões registradas nos ADRs 0005–0009 e critérios de aceite verificados.
