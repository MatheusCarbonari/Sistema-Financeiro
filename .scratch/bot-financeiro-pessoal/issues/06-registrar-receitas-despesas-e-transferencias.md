# Registrar Receitas, Despesas e Transferências

Status: resolved
Blocked by: 04, 05

## Objetivo

Implementar as movimentações centrais que alteram Contas de recursos sem misturar consumo, recebimento e movimentação interna.

## Escopo

- Receita reconhecida no recebimento.
- Despesa à vista reconhecida na Data efetiva.
- Transferência vinculada entre contas próprias.
- Tarifa de transferência como Despesa separada.
- Consulta do histórico e recálculo dos Saldos das contas.

## Critérios de aceite

- Receita aumenta exatamente uma Conta de recursos e possui Rateios de receita válidos.
- Despesa à vista reduz exatamente uma Conta de recursos e possui Rateios de despesa válidos.
- Transferência reduz a origem e aumenta o destino pelo mesmo valor, sem afetar Receita, Despesa ou Patrimônio líquido.
- Transferências entre moedas diferentes são rejeitadas no MVP.
- Cada operação é atômica e deixa histórico reconciliável.

## Referências

- `spec.md`: regras 28–30 e 33–35.
- `CONTEXT.md`: Receita, Despesa, Transferência e Movimentação realizada.

## Comments

- Seams públicos confirmados antes do TDD: `Income`, `CashExpense`, `Transfer`,
  `FinancialOperations`, `TransferResult`, `FinancialMovementRepository`,
  `FinancialOperationUnitOfWork` e `AccountStatement`.
- `FinancialOperations` apenas delimita a unidade de trabalho e coordena
  repositórios; moeda, natureza e validade dos Rateios, Conta arquivada, direção
  do lançamento e política de Saldo negativo permanecem no domínio.
- A tarifa é uma `CashExpense` com identidade e Rateio próprios, vinculada a uma
  `Transfer` válida e confirmada atomicamente com ela.
- `AccountStatement` é o único responsável por recalcular e reconciliar o Saldo
  a partir das diferenças do histórico; `ResourceAccount` reutiliza esse cálculo.
- A migração `0003` sucede `0002`, preserva o histórico existente e adiciona
  Movimentações realizadas append-only e referências reconciliáveis. As
  migrações `0001` e `0002` permaneceram intactas.
- Baseline do code review: `096fdc1`. Os achados intermediários sobre divergência
  entre Movimentação e Rateio e vínculos inválidos de tarifa foram corrigidos.
  Os passes finais de Standards e Spec terminaram sem achados materiais.
- Validação final: 98 testes aprovados e Ruff aprovado. Conforme solicitado,
  nenhuma alteração deste ticket foi commitada.

## Answer

Receitas recebidas e Despesas à vista agora produzem Movimentações realizadas
imutáveis, com Rateios exatos da natureza correspondente, Data efetiva e Momento
do registro preservados. Cada uma altera exatamente uma Conta de recursos e
respeita suas regras de moeda, arquivamento e Saldo negativo.

Transferências ligam duas contas próprias da mesma moeda, debitando a origem e
creditando o destino pelo mesmo Valor monetário. Elas permanecem neutras para
Receita, Despesa e Patrimônio líquido. Quando informada, a tarifa é registrada
como uma Despesa à vista separada, categorizada e vinculada à Transferência.

Cada caso de uso é executado em uma única unidade de trabalho SQLAlchemy. Uma
falha reverte conjuntamente Rateios, Movimentações e alterações de Saldo. O
histórico das contas referencia as Movimentações correspondentes, e
`AccountStatement` permite recalcular o Saldo integralmente a partir desse
histórico. A persistência rejeita divergências entre uma Movimentação e seu
Rateio e protege os registros financeiros contra alteração ou exclusão.
