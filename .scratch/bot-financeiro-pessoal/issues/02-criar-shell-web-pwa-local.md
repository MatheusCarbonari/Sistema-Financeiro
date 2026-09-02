# Criar shell Web/PWA local

Status: open
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

