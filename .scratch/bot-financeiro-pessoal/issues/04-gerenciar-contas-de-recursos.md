# Gerenciar Contas de recursos

Status: open
Blocked by: 03

## Objetivo

Permitir o ciclo de vida completo das Contas de recursos do Titular.

## Escopo

- Cadastro com nome, tipo, moeda e Saldo inicial.
- Tipos organizacionais: carteira, conta corrente, conta digital, poupança e personalizado.
- Ajuste de saldo explícito e justificado.
- Saldo negativo permitido para contas bancárias e proibido para carteira física.
- Arquivamento apenas com saldo zero e posterior reativação.

## Critérios de aceite

- Saldo inicial e Ajuste de saldo não aparecem como Receita ou Despesa.
- Todo ajuste permanece visível no histórico com seu motivo.
- Uma conta de recursos aceita somente valores de sua moeda.
- Conta arquivada preserva histórico e rejeita novas movimentações.
- As restrições de saldo negativo e arquivamento possuem testes.

## Referências

- `spec.md`: regras 8–11 e 67–72.
- `CONTEXT.md`: Conta de recursos, Conta arquivada, Saldo inicial, Ajuste de saldo, Saldo negativo.

## Comments

