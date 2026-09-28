# Implementar Correções, Cancelamentos e Reembolsos

Status: resolved
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

- Implementação concluída contra o baseline `cc1a66b`, preservando as migrações
  `0001`–`0007` e adicionando a migração append-only `0008`.
- Correções e Cancelamentos usam Propostas inertes, recusam propostas obsoletas,
  preservam todas as versões e validam motivo e Momento do registro antes de
  qualquer escrita.
- A identidade do fato permanece estável entre versões; a consulta corrente
  apresenta a última versão e omite fatos Cancelados, enquanto o histórico bruto
  continua reconstruível.
- Dependências consumidas bloqueiam reversões inseguras. Transferências recalculam
  as duas Contas atomicamente e tarifas vinculadas permanecem fatos independentes.
- Reembolsos em Conta de recursos e em Cartão permanecem vinculados à Despesa,
  nunca são Receita e compartilham limites e ordem temporal entre os dois canais.
- Gasto bruto usa a versão financeira corrente; valor reembolsado e gasto líquido
  permanecem consultáveis separadamente.
- Casos específicos de Conta a pagar, Cartão, Compra parcelada e Fatura mantêm
  políticas próprias; a fachada pública apenas reúne os seams de aplicação.
- O registro único de codecs concentra somente tradução entre domínio e SQL, sem
  absorver regras financeiras.
- Code review final nos eixos Standards e Spec: nenhum blocker restante.
- Validação final: 218 testes aprovados, Ruff aprovado e `git diff --check cc1a66b`
  sem erros. Permanece apenas o aviso externo de depreciação do TestClient do
  Starlette. Conforme solicitado, nenhuma alteração foi commitada.

## Answer

O Ticket 11 implementa Correções versionadas, Cancelamentos terminais e
Reembolsos pelos canais Conta de recursos e Cartão sem exclusão física ou dupla
contagem. Toda confirmação relevante recalcula Saldos, dívidas e Faturas em uma
única transação e mantém o estado anterior reconstruível.

As prévias descrevem impactos e bloqueios sem produzir efeitos. Dependências já
consumidas impedem confirmações inseguras, e a ordem do Momento do registro é
validada inclusive entre Reembolsos realizados por canais diferentes.
