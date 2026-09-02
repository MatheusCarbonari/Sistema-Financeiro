# Implementar primitivas financeiras e auditoria

Status: open
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

