# Implementar Compras parceladas

Status: open
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

