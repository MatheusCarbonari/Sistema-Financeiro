# Controlar Contas a pagar

Status: resolved
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

- Seams públicos confirmados antes do TDD: `Payable`, `PayableOpened`,
  `BillPayment`, `FinancialCharge`, `PayableStatus`, `PayableOperations`,
  `PayableRepository` e `PayableUnitOfWork`.
- A abertura reconhece a Despesa categorizada e cria a obrigação sem movimentar
  Conta de recursos. `PayableOpened` não realiza nem representa saída de conta.
- Pagamentos são movimentações realizadas neutras para Receita e Despesa; Conta,
  obrigação e movimento são persistidos atomicamente.
- Encargos financeiros possuem Rateio próprio de natureza Despesa e permanecem
  vinculados à obrigação no histórico imutável.
- A migração `0004` sucede `0003`; as migrações `0001`–`0003` permaneceram
  intactas.
- Code review contra `eed1f6a`: nenhum achado material de especificação ou
  padrões. A duplicação de baixo impacto encontrada no recálculo da dívida foi
  removida.
- Validação final: 119 testes aprovados, Ruff aprovado e `git diff --check` sem
  erros. Conforme solicitado, nenhuma alteração deste ticket foi commitada.

## Answer

Contas a pagar agora registram a Despesa no nascimento da obrigação sem alterar
o Saldo da Conta de recursos. O valor devido é reconstruído exatamente como valor
original mais Encargos financeiros menos pagamentos, e os estados pendente,
parcialmente paga, paga e atrasada são calculados sem gerar eventos fictícios.

Pagamentos totais e parciais reduzem simultaneamente a obrigação e a Conta de
recursos, sem reconhecer novamente a Despesa principal. Encargos são Despesas
separadas, categorizadas e vinculadas. Todo o fluxo usa uma única unidade de
trabalho, mantém histórico append-only e permite reconciliar o pagamento com a
movimentação financeira e com o extrato da conta.
