# Planejar Previsões, Recorrências e Margens

Status: resolved
Blocked by: 06, 07, 09

## Objetivo

Permitir planejamento futuro sem alterar a posição financeira atual ou realizar eventos automaticamente.

## Escopo

- Previsões de entrada e saída.
- Recorrências e Ocorrências previstas.
- Edição de uma ocorrência ou dela em diante.
- Confirmação vinculada à Movimentação realizada correspondente.
- Margem de segurança fixa ou percentual.
- Saldo projetado normal e conservador.

## Critérios de aceite

- Previsões e margens não alteram Saldo da conta nem Patrimônio líquido atual.
- A chegada da data apenas torna uma ocorrência atrasada; não a realiza.
- Confirmar uma ocorrência substitui sua previsão sem contagem dupla.
- Divergência de valor pode atualizar ocorrências futuras somente após confirmação.
- Consultas mostram separadamente valor esperado, margem e impacto conservador.

## Referências

- `spec.md`: regras 5 e 36–48.
- `CONTEXT.md`: Previsão financeira, Recorrência, Ocorrência prevista, Margem de segurança e Saldos projetados.

## Comments

- Seams públicos confirmados antes do TDD: `ForecastDirection`,
  `ForecastStatus`, `SafetyMargin`, `PlannedOccurrence`, `Recurrence`,
  `RecurrenceFrequency`, `PlanningOperations`, projeções e repositório/unidade de
  trabalho de planejamento.
- Previsões avulsas e Recorrências pertencem a uma Conta de recursos e usam sua
  moeda, mas não escrevem no histórico da conta nem criam Movimentações realizadas.
  A Data prevista é obrigatoriamente futura na criação; depois dela o estado
  calculado passa a `OVERDUE`, sem realização automática.
- Recorrências diárias, semanais, mensais e anuais geram Ocorrências previstas sob
  demanda até o horizonte consultado. Datas mensais/anuais preservam a âncora e
  usam o último dia quando necessário, sem desvio nos ciclos seguintes.
- Edições individuais e revisões "desta em diante" são append-only. Mudanças de
  cadência preservam o segmento anterior e não podem eliminar Ocorrências com
  histórico próprio; revisões individuais rejeitam colisões dentro da Recorrência.
- A confirmação apenas vincula uma Ocorrência a uma `Income`, `CashExpense`,
  `BillPayment` ou `InvoicePayment` já persistida e compatível. A previsão-base,
  seus metadados, a divergência e o vínculo ficam imutáveis; Transferências não
  fazem parte deste ticket e uma Movimentação não pode confirmar duas Ocorrências.
- `confirm_future_amount_update(...)` é uma operação separada e explícita. Ela usa
  o valor realizado apenas nas Ocorrências posteriores que ainda são futuras e não
  confirmadas, preservando eventos anteriores e snapshots confirmados.
- A Margem de segurança é opcional, fixa ou percentual. Saídas conservadoras
  acrescentam a margem; entradas a descontam com piso zero, sem inversão de sinal.
  O arredondamento monetário continua centralizado em `Money`, conforme ADR-0009.
- A projeção por conta expõe separadamente valor esperado, margem, impacto normal,
  impacto conservador, Saldo atual e Saldos projetados. Ocorrências confirmadas não
  voltam a compor a projeção porque a Movimentação já está no Saldo real.
- A migração `0007` sucede `0006`, preserva `0001`–`0006` e cria origens e eventos
  de planejamento protegidos por validações, chaves e gatilhos append-only.
- Code review contra `d3860df`: os eixos Spec e Standards terminaram sem achados.
  Durante o review foram corrigidos arredondamento percentual duplicado, composição
  duplicada de consultas e fronteiras de confirmação futura, horizonte e colisão.
- Validação final: 176 testes aprovados, Ruff aprovado e `git diff --check` sem
  erros. Conforme solicitado, nenhuma alteração foi commitada.

## Answer

O Ticket 10 implementa Planejamento como domínio separado das Movimentações
realizadas. Previsões, Recorrências, Ocorrências e Margens permanecem neutras para
a posição financeira atual, podem ser revisadas com histórico explicável e só são
substituídas na projeção quando vinculadas explicitamente a uma Movimentação real.

As consultas produzem cenários normal e conservador sem contagem dupla. A
persistência reconstrói tanto a visão corrente quanto a previsão original de uma
Ocorrência confirmada, incluindo Correções anteriores e divergência realizada.
