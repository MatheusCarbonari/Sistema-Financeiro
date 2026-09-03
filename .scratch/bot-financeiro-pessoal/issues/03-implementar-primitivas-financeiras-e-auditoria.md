# Implementar primitivas financeiras e auditoria

Status: resolved
Blocked by: 01

## Objetivo

Implementar os conceitos fundamentais compartilhados por todas as operações financeiras.

## Escopo

- Valor monetário com quantia exata e moeda obrigatória.
- Precisão de centavos para BRL e divisão com soma exata.
- Data efetiva separada do Momento do registro.
- Identidade estável e metadados de origem para cada registro.
- Base de histórico necessária a Correções e Cancelamentos.

## Critérios de aceite

- Não é possível criar valor monetário sem moeda.
- Valores de moedas diferentes não são somados implicitamente.
- Rateios e Parcelas gerados automaticamente preservam o total, atribuindo resíduos aos primeiros itens.
- Data efetiva e Momento do registro são armazenados e consultados separadamente.
- Testes cobrem arredondamento, datas retroativas e integridade transacional.

## Referências

- `CONTEXT.md`: Valor monetário, Data efetiva, Momento do registro.
- `spec.md`: regras 7, 77–79 e seção Datas e localidade.
- ADR-0001 e ADR-0002.

## Comments

- Seams públicos confirmados antes do TDD: `Money`, `RecordMetadata`,
  `AuditTrail` e `SqlAlchemyUnitOfWork`.
- `AuditTrail` foi mantido como fundação append-only. Os efeitos financeiros de
  Correções e Cancelamentos continuam reservados ao Ticket 11.
- Baseline do code review: `bc9d3cc`.

## Answer

Foram implementados o Valor monetário exato com moeda obrigatória, a precisão de
centavos para BRL, a proteção contra aritmética e comparação entre moedas distintas
e a divisão que preserva o total com resíduos nos primeiros itens.

Os metadados de registro mantêm identidade, origem, Data efetiva e Momento do
registro com fuso como informações distintas. A trilha de auditoria é imutável e
append-only, oferecendo somente a base para registrar Correções e Cancelamentos
sem antecipar seus efeitos.

A unidade de trabalho SQLAlchemy usa sessões curtas, commit explícito e rollback
integral em caso de falha. A atomicidade foi verificada em SQLite real sem criar
Contas de recursos, Cartões de crédito ou Movimentações realizadas.
