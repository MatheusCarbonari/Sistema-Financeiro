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

### Parte 1 — concluída, Ticket permanece aberto

- Criada a fachada `FinancialQueries` para busca combinável de Movimentações e Extrato por Conta de recursos.
- O Extrato reconstrói o Saldo usando Saldo inicial, Ajustes e a versão financeira corrente de cada fato; compensações históricas permanecem apenas para auditoria.
- Períodos são inclusivos e usam Data efetiva; Momento do registro inicial e da versão corrente são expostos separadamente.
- Introduzida Descrição financeira pesquisável, distinta de motivo e origem, com herança pela Despesa/Compra original em Reembolsos e Parcelas.
- A migração `0009` mantém descrições legadas como ausentes, sem inventar informação, e permite sua reconstrução e Correção posterior.
- As Partes 2–4 e a validação integrada continuam pendentes.

### Parte 2 — concluída, Ticket permanece aberto

- `FinancialQueries.income_and_expense_report(...)` apresenta Receitas, Despesa bruta, Reembolsos e Despesa líquida por período e moeda, sempre com componentes reconciliáveis.
- O reconhecimento usa a Data efetiva de cada fato; Reembolsos pertencem ao próprio período e podem produzir Despesa líquida negativa legitimamente.
- Compras parceladas são reconhecidas integralmente como Despesa; Parcelas, pagamentos de contas e pagamentos de Faturas não criam nova Despesa.
- Reembolsos em conta e cartão reduzem o gasto líquido sem virar Receita e herdam Descrição e Rateios vigentes da Despesa original.
- A distribuição proporcional de Reembolsos preserva o total exato e distribui resíduos por ordem estável de Categoria financeira.
- Categorias apresentam valores diretos, descendentes e inclusivos sem duplicação; categorias sem movimento são omitidas e arquivadas com movimento permanecem visíveis.
- Correções usam a versão financeira vigente, Cancelamentos retiram os efeitos atuais e moedas diferentes permanecem separadas.
- As Partes 3–4 e a validação integrada continuam pendentes.

### Parte 3 — concluída, Ticket permanece aberto

- `FinancialQueries` delega posição de Cartões, Contas a pagar, Compromissos e fluxo de caixa realizado a quatro colaboradores de consulta.
- A posição do Cartão separa principal pendente, Encargos, Crédito disponível, dívida líquida e Limite disponível, com Faturas e componentes reconciliáveis calculados pelo domínio.
- Faturas exibem apenas suas Parcelas e explicam o saldo pendente após pagamentos, Reembolsos e Créditos aplicados, sem duplicar a dívida da Compra parcelada.
- Compromissos mantêm dívidas reconhecidas separadas de Previsões, incluem pendências atrasadas e limitam ocorrências futuras ao horizonte solicitado.
- O fluxo de caixa usa Data efetiva e expõe Momento do registro; Transferências internas permanecem no detalhe, mas não afetam totais globais.
- Consultas individuais por Conta incluem a respectiva perna da Transferência nos totais da Conta.
- Moedas permanecem separadas e nenhum cálculo de dívida ou Fatura concorrente foi introduzido.
- A Parte 4 e a validação integrada continuam pendentes.

