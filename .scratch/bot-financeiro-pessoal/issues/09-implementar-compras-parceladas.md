# Implementar Compras parceladas

Status: resolved
Blocked by: 08

## Objetivo

Distribuir o pagamento de uma compra entre Faturas sem distribuir o reconhecimento da Despesa ou da dívida.

## Escopo

- Criação das Parcelas e atribuição aos ciclos de Fatura.
- Reconhecimento integral da Despesa e da dívida na compra.
- Distribuição exata de centavos.
- Correção da compra com recálculo das Parcelas ainda abertas.
- Separação entre relatório de Despesas, Fatura e fluxo de caixa.

## Critérios de aceite

- Uma compra de R$ 1.200 em 12 vezes cria uma Despesa e dívida de R$ 1.200 na Data efetiva.
- Cada Fatura apresenta somente sua Parcela.
- A soma de todas as Parcelas é exatamente igual ao total da compra.
- Patrimônio líquido, Limite disponível e fluxo de caixa refletem suas visões próprias.
- Correções preservam o histórico e não alteram silenciosamente ciclos já afetados.

## Referências

- `spec.md`: regras 3–4, 52, 65 e 73–79.
- `CONTEXT.md`: Compra parcelada e Parcela.

## Comments

- Seams públicos confirmados antes do TDD: `Installment`,
  `InstallmentPurchase`, `InstallmentPurchaseCorrected`, `Invoice.installments`,
  operações de Compra parcelada/Correção em `CreditCard` e
  `CreditCardOperations`, além do repositório e da unidade de trabalho existentes.
- A Compra parcelada mantém um único Rateio integral na Data efetiva; Parcelas
  imutáveis apenas distribuem o principal entre ciclos consecutivos de Fatura.
- `Money.split_equally` distribui resíduos deterministicamente entre as primeiras
  Parcelas. Cada Fatura projeta somente as Parcelas de seu ciclo, enquanto o
  Limite disponível considera todo o principal pendente, inclusive ciclos futuros.
- `InstallmentPurchaseCorrected` acrescenta ao histórico as Parcelas anteriores,
  congeladas e substitutas. A decisão usa o estado da Fatura na Data efetiva da
  Correção, desconsiderando pagamentos e Correções com Data efetiva posterior.
- O snapshot corrigido substitui a visão corrente da Compra parcelada sem apagar o
  evento original nem criar uma segunda Despesa. O novo total não pode ficar abaixo
  das Parcelas congeladas; quando for igual, todas as Parcelas abertas podem ser
  removidas explicitamente.
- `CardCashFlow` separa Parcelas programadas por vencimento de Pagamentos de fatura
  realizados por Data efetiva. Pagamentos continuam neutros para Receita/Despesa.
- A migração `0006` sucede `0005`, preserva `0001`–`0005` e acrescenta a programação
  de Parcelas protegida por validações e gatilhos append-only.
- Code review contra `4585911`: Spec terminou sem achados; Standards terminou sem
  violações documentadas e com um smell apenas prospectivo sobre as cascatas de
  tipos de evento existentes.
- Validação final: 152 testes aprovados, Ruff aprovado e `git diff --check` sem
  erros. Conforme solicitado, nenhuma alteração foi commitada.

## Answer

Compras parceladas agora reconhecem integralmente a Despesa e a dívida na Data
efetiva, distribuem o principal com precisão entre Faturas e comprometem o Limite
do cartão pelo total ainda pendente. A programação de Parcelas, os pagamentos
realizados e o Rateio integral permanecem consultas conceitualmente separadas.

Correções preservam o histórico original, congelam ciclos já afetados conforme a
Data efetiva e recalculam somente Parcelas abertas. As projeções continuam
reconciliáveis após persistência, pagamentos e Correções retroativas.
