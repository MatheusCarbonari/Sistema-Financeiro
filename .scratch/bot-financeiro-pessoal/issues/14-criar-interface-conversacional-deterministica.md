# Criar interface conversacional determinística

Status: open
Blocked by: 02, 04, 05, 07, 09, 10, 11

## Objetivo

Permitir lançamentos e consultas guiados por conversa local sem IA externa ou inferências financeiras silenciosas.

## Escopo

- Comandos e intenções determinísticas para operações e consultas do MVP.
- Coleta incremental de campos ausentes ou ambíguos.
- Proposta estruturada sem efeito antes da confirmação.
- Confirmação, rejeição e reinício do fluxo.
- Mensagens claras quando uma solicitação não for compreendida.

## Critérios de aceite

- O bot nunca inventa valor, data, conta, cartão ou categoria obrigatória.
- Toda mutação apresenta resumo e efeito financeiro antes de salvar.
- Consultas não exigem confirmação.
- Correções, Cancelamentos, restaurações e ações em massa recebem confirmação reforçada.
- Testes demonstram que nenhuma mensagem é enviada a serviço externo.

## Referências

- `spec.md`: Acesso e segurança, Entrada de dados e Confirmação conversacional.
- `CONTEXT.md`: Proposta de lançamento.
- ADR-0004.

## Comments

