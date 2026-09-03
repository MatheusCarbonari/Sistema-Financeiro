# Gerenciar Contas de recursos

Status: resolved
Blocked by: 03

## Objetivo

Permitir o ciclo de vida completo das Contas de recursos do Titular.

## Escopo

- Cadastro com nome, tipo, moeda e Saldo inicial.
- Tipos organizacionais: carteira, conta corrente, conta digital, poupança e personalizado.
- Ajuste de saldo explícito e justificado.
- Saldo negativo permitido para contas bancárias e proibido para carteira física.
- Arquivamento apenas com saldo zero e posterior reativação.

## Critérios de aceite

- Saldo inicial e Ajuste de saldo não aparecem como Receita ou Despesa.
- Todo ajuste permanece visível no histórico com seu motivo.
- Uma conta de recursos aceita somente valores de sua moeda.
- Conta arquivada preserva histórico e rejeita novas movimentações.
- As restrições de saldo negativo e arquivamento possuem testes.

## Referências

- `spec.md`: regras 8–11 e 67–72.
- `CONTEXT.md`: Conta de recursos, Conta arquivada, Saldo inicial, Ajuste de saldo, Saldo negativo.

## Comments

- Seams públicos confirmados antes do TDD: `ResourceAccount`, `AccountType`,
  `AccountStatus`, `AccountHistoryEntry` e `ResourceAccountRepository`.
- Para `CUSTOM`, a política de Saldo negativo é obrigatória e explícita na criação.
  `CHECKING`, `DIGITAL` e `SAVINGS` permitem saldo negativo; `WALLET` proíbe.
- O Ajuste de saldo recebe o saldo real desejado e registra tanto esse saldo quanto
  a diferença exata e o motivo, sem produzir Receita ou Despesa.
- A persistência usa a unidade de trabalho existente e não executa `commit` no
  repositório. A revisão Alembic `0001` cria o esquema de Contas de recursos.
- Baseline do code review: `8c905e9`. Os achados iniciais sobre invariantes,
  chaves estrangeiras e histórico append-only foram corrigidos; a revisão final
  de Standards e Spec terminou sem achados materiais.

## Answer

Foi implementado o ciclo de vida de Contas de recursos com cadastro por nome,
tipo, moeda e Saldo inicial. As contas são imutáveis no domínio: Ajustes de saldo
produzem uma nova versão do agregado e acrescentam uma entrada ao histórico com
o saldo real, a diferença, o motivo, a Data efetiva e o Momento do registro.

As regras de moeda, Saldo negativo, arquivamento somente com saldo zero e
reativação são validadas pelo domínio. A política dos tipos bancários e da
carteira não pode ser sobrescrita; contas `CUSTOM` exigem uma escolha explícita.
Saldo inicial e Ajuste de saldo são marcados como neutros para Receita e Despesa.

O contrato de repositório fica na camada de aplicação e o adaptador SQLAlchemy
na persistência. O histórico é append-only no repositório e também no SQLite,
por meio de restrição de chave estrangeira e triggers contra alteração ou
exclusão. A primeira migração Alembic e a ativação de chaves estrangeiras foram
testadas em SQLite, incluindo rollback atômico.
