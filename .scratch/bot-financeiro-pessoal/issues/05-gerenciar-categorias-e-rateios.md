# Gerenciar Categorias financeiras e Rateios

Status: open
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

