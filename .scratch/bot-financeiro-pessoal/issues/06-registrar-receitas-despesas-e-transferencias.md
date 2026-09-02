# Registrar Receitas, Despesas e Transferências

Status: open
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

