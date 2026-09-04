# Gerenciar Categorias financeiras e Rateios

Status: resolved
Blocked by: 03

## Objetivo

Implementar categorias hierárquicas separadas para Receitas e Despesas e a distribuição exata de valores entre elas.

## Escopo

- Árvores independentes por natureza financeira.
- No máximo dois níveis: categoria e Subcategoria.
- Rateios em categorias-pai ou Subcategorias.
- Agregação de valores próprios e descendentes.
- Exclusão, arquivamento, reativação e reclassificação histórica seguras.

## Critérios de aceite

- A soma dos Rateios deve ser exatamente igual ao valor total.
- Uma categoria não pode receber Rateio de natureza incompatível.
- Categoria-pai com filhas pode receber Rateio diretamente.
- Categoria com histórico não pode ser apagada sem reclassificação prévia.
- Categoria arquivada permanece em relatórios, mas não aceita novos Rateios.
- Relatórios podem omitir categorias zeradas sem arquivá-las.

## Referências

- `spec.md`: regras 12–27.
- `CONTEXT.md`: Categoria financeira, Subcategoria, Categoria arquivada e Rateio.

## Comments

- Seams públicos confirmados antes do TDD: `FinancialNature`,
  `FinancialCategory`, `CategoryStatus`, `CategoryCatalog`, `Allocation`,
  `AllocationPart`, `AllocationReclassification`,
  `FinancialCategoryRepository` e `AllocationRepository`.
- `CategoryCatalog` concentra somente árvore, hierarquia, ciclo de vida,
  agregação mínima e seleção de categorias com Rateios; relatórios completos
  continuam fora deste ticket.
- A reclassificação preserva um evento imutável com origem, destino, nomes das
  categorias, Valor monetário e metadados do registro, sem criar um sistema de
  auditoria genérico.
- A migração `0002` sucede a revisão `0001`, que permaneceu intacta.
- Baseline do code review: `b9aed26`. Os achados intermediários sobre mutações
  silenciosas, validação contextual e registro do schema foram corrigidos. Os
  passes finais de Standards e Spec terminaram sem achados materiais.
- Conforme solicitado, nenhuma alteração deste ticket foi commitada.

## Answer

Foram implementadas árvores independentes de Categorias financeiras para
Receita e Despesa, com categoria raiz e Subcategoria, limitadas a dois níveis.
O catálogo permite criar, renomear, arquivar, reativar e excluir categorias,
impedindo exclusão enquanto existirem filhas ou Rateios ainda associados.

Rateios preservam exatamente o Valor monetário total, aceitam categorias-pai ou
Subcategorias ativas e rejeitam natureza ou moeda incompatível. A agregação
separa valor próprio do total com descendentes, e a consulta mínima de atividade
omite categorias zeradas sem arquivá-las. Categorias arquivadas continuam
participando do histórico, rejeitam novos Rateios e podem ser reativadas.

A reclassificação altera a classificação atual sem sobrescrever seu histórico.
Cada evento preserva as categorias e o valor transferido, e a persistência exige
que a sequência de eventos explique exatamente o estado atual dos Rateios. Os
repositórios SQLAlchemy validam as invariantes públicas e não executam `commit`.
A migração Alembic `0002` cria categorias, Rateios e histórico append-only, com
chaves estrangeiras e triggers de proteção verificadas em SQLite.
