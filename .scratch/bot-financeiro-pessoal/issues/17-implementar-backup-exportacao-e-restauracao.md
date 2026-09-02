# Implementar backup, exportação e restauração

Status: open
Blocked by: 03, 11

## Objetivo

Proteger o histórico contra erro e corrupção e permitir portabilidade completa dos dados.

## Escopo

- Backup automático diário com retenção das 30 versões mais recentes.
- Destino local configurável.
- Exportação completa em formato próprio e formato portátil documentado.
- Validação prévia e confirmação antes da restauração.
- Preservação de vínculos, histórico e moedas.

## Critérios de aceite

- Backup consistente pode restaurar todas as entidades e relações do MVP.
- Rotação remove apenas versões além da retenção configurada.
- Backup inválido ou incompatível é rejeitado sem alterar dados atuais.
- Restauração exige confirmação e mantém uma opção recuperável do estado anterior.
- Exportação portátil é legível sem depender da aplicação e preserva a semântica documentada.

## Referências

- `spec.md`: Backup e portabilidade; Critérios de aceite do MVP.
- ADR-0001 e ADR-0002.

## Comments

