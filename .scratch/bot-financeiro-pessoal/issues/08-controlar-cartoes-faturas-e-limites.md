# Controlar Cartões, Faturas e limites

Status: resolved
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

- Seams públicos confirmados antes do TDD: `CreditCard`, `InvoiceCycle`,
  `Invoice`, `InvoiceStatus`, eventos imutáveis do cartão,
  `CreditCardOperations`, `CreditCardRepository` e `CreditCardUnitOfWork`.
- O dia de fechamento pertence ao ciclo atual; a Fatura fecha no dia seguinte e
  atrasa somente depois do vencimento. Dias inexistentes são ajustados para o
  último dia do mês.
- Pagamentos e Créditos aplicados reduzem primeiro o principal pendente e depois
  Encargos. Reembolsos reduzem o custo e o principal; excedentes reais viram
  Crédito no cartão. Nenhum desses fatos é Receita.
- O Limite disponível considera somente principal pendente, é opcional e sua
  política é validada também durante a reidratação cronológica do histórico.
- Correções de atribuição preservam a Fatura anterior, o novo ciclo, motivo,
  Data efetiva e Momento do registro sem criar Faturas vazias na consulta atual.
- A migração `0005` sucede `0004`; `0001`–`0004` permaneceram intactas.
- Code review contra `e224ee1`: os achados intermediários de reconciliação,
  integridade estrutural, ciclo e limite foram corrigidos. Standards e Spec
  terminaram sem achados remanescentes.
- Validação final: 140 testes aprovados, Ruff aprovado e `git diff --check` sem
  erros. Conforme solicitado, nenhuma alteração foi commitada.

## Answer

Cartões de crédito agora mantêm compras, dívida, Faturas, pagamentos, Encargos,
Reembolsos e Créditos em um histórico imutável e reconciliável. Compras e
Encargos reconhecem Despesas sem movimentar uma Conta de recursos; Pagamentos de
fatura reduzem atomicamente a Conta e a dívida sem duplicar a Despesa.

As Faturas são projeções do histórico por ciclo e apresentam estados aberta,
fechada, parcialmente paga, paga e atrasada. A atribuição automática respeita o
fechamento e pode ser corrigida apenas por evento explícito e justificado. O
Limite disponível desconta somente o principal ainda devido, não inclui Encargos
e não é tratado como Saldo da conta ou Patrimônio líquido.
