# Implementar Correções, Cancelamentos e Reembolsos

Status: open
Blocked by: 06, 07, 09

## Objetivo

Permitir mudanças e devoluções preservando a explicabilidade de todos os cálculos financeiros.

## Escopo

- Correção versionada com motivo e Momento do registro.
- Cancelamento sem exclusão física do histórico.
- Reembolso vinculado à Despesa original.
- Reembolso em Conta de recursos ou Cartão de crédito.
- Recálculo atômico de dependências afetadas.

## Critérios de aceite

- É possível reconstruir o estado anterior e explicar cada alteração.
- Cancelamento remove efeitos atuais sem apagar o fato registrado.
- Reembolso não é Receita e mantém a Despesa original visível.
- Relatórios oferecem gasto líquido e decomposição entre bruto e reembolsado.
- Alterações retroativas relevantes produzem uma descrição de impacto antes da confirmação.

## Referências

- `spec.md`: regras 49–57.
- `CONTEXT.md`: Correção, Cancelamento e Reembolso.
- ADR-0002.

## Comments

