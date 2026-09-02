# Controlar Cartões, Faturas e limites

Status: open
Blocked by: 04, 06

## Objetivo

Implementar compras no cartão, ciclos de Fatura, dívida, pagamentos, crédito e limite sem duplicar Despesas.

## Escopo

- Cadastro de cartão com fechamento, vencimento e limite opcional.
- Atribuição de compras às Faturas e Correção explícita da atribuição.
- Estados aberta, fechada, parcialmente paga, paga e atrasada.
- Pagamento total ou parcial por Conta de recursos.
- Crédito no cartão, Reembolsos e Encargos financeiros.
- Cálculo de Limite disponível a partir do principal pendente.

## Critérios de aceite

- Compra cria Despesa e dívida sem reduzir imediatamente o Saldo da conta.
- Pagamento reduz saldo e dívida sem criar outra Despesa principal.
- Limite disponível considera todo principal pendente e nunca compõe patrimônio.
- Pagamento superior à dívida exige Crédito no cartão real.
- Fechamento, vencimento e estados de pagamento possuem testes de fronteira.

## Referências

- `spec.md`: regras 1–2 e 58–66.
- `CONTEXT.md`: Cartão de crédito, Fatura, Crédito no cartão, Limite do cartão e Limite disponível.

## Comments

