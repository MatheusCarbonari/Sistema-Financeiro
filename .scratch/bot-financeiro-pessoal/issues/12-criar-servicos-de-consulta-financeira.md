# Criar serviços de consulta financeira

Status: open
Blocked by: 07, 09, 10, 11

## Objetivo

Produzir consultas reconciliáveis que distingam dinheiro, dívida, patrimônio, gasto e planejamento.

## Escopo

- Resumo financeiro decomposto.
- Receitas e Despesas por período e Categoria financeira.
- Extrato por Conta de recursos.
- Faturas, dívida e limite por cartão.
- Compromissos próximos ou atrasados.
- Projeções normal e conservadora.
- Busca e comparação entre previsto e realizado.

## Critérios de aceite

- Uma consulta genérica por saldo nunca retorna número sem qualificação.
- Cada total pode ser explicado pelos registros que o compõem.
- Categorias sem movimento são omitidas por padrão, sem serem arquivadas.
- Compras parceladas aparecem integralmente no período da Despesa e por Parcela no fluxo de caixa.
- Filtros por descrição, valor, categoria, conta e período podem ser combinados.

## Referências

- `spec.md`: regras 6, 20, 23, 56 e 73–76; Consultas essenciais; Resposta padrão de saldo.
- `CONTEXT.md`: Resumo financeiro e Patrimônio líquido.

## Comments

