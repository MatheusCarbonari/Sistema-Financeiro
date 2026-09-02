# Controlar Contas a pagar

Status: open
Blocked by: 06

## Objetivo

Separar obrigações confirmadas de meras previsões e controlar sua quitação sem duplicar Despesas.

## Escopo

- Criação de Despesa com Conta a pagar.
- Vencimento, valor devido e estados pendente, parcialmente paga, paga e atrasada.
- Pagamento total ou parcial por Conta de recursos.
- Encargos financeiros como Despesas vinculadas e separadas.

## Critérios de aceite

- Criar obrigação reduz o Patrimônio líquido sem alterar imediatamente o Saldo da conta.
- Pagar reduz simultaneamente saldo e dívida, sem criar nova Despesa principal.
- Pagamento parcial mantém o valor restante devido.
- A passagem do vencimento altera o estado, mas não inventa pagamento.
- Histórico permite explicar o valor original, pagamentos e Encargos financeiros.

## Referências

- `spec.md`: regras 30–32 e política de Alertas.
- `CONTEXT.md`: Conta a pagar, Pagamento de conta e Encargo financeiro.

## Comments

