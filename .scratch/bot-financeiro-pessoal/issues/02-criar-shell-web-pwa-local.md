# Criar shell Web/PWA local

Status: resolved
Blocked by: 01

## Objetivo

Criar a estrutura executável da aplicação Web/PWA local que receberá os fluxos financeiros.

## Escopo

- Inicialização e encerramento local da aplicação.
- Navegação base para resumo, lançamentos, contas, cartões, planejamento e configurações.
- Configuração inicial em português do Brasil e fuso `America/Sao_Paulo`.
- Garantia de que o serviço não seja exposto à rede por padrão.

## Critérios de aceite

- A aplicação abre localmente e apresenta sua estrutura principal.
- Não há dependência obrigatória de internet para iniciar ou navegar.
- Nenhum dado é enviado a serviço externo.
- O comportamento instalável da PWA é verificável no ambiente suportado.
- A configuração de rede padrão aceita conexões somente do próprio computador.

## Referências

- `spec.md`: Canal do produto, Acesso e segurança, Datas e localidade.
- ADR-0003 e ADR-0004.

## Comments

- Os seams públicos de TDD foram confirmados antes da implementação: HTTP com
  `TestClient`, configuração pública, recursos PWA servidos por HTTP e início pelo
  comando público em loopback.
- A instalação real da PWA no navegador foi explicitamente adiada para um ticket
  posterior com `pytest-playwright`; neste ticket foi verificado o contrato público
  formado por manifesto, service worker, registro e recursos locais.

## Answer

O shell Web/PWA local foi criado com FastAPI, Uvicorn e Jinja2. Ele oferece as seis
páginas e a navegação previstas, usa `pt-BR` e `America/Sao_Paulo`, funciona com
recursos locais e recusa configuração de bind diferente de `127.0.0.1`.

O manifesto, o service worker e seu registro fornecem o contrato mínimo de PWA sem
adicionar `pytest-playwright`. Nenhuma regra financeira ou persistência de domínio
foi implementada.
