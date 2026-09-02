# Decidir stack e persistência local

Status: open

## Objetivo

Escolher a base técnica da aplicação Web/PWA e da persistência local sem acoplar o domínio financeiro ao canal de interação.

## Escopo

- Comparar opções de frontend, backend local e armazenamento persistente.
- Garantir suporte a transações atômicas, valores monetários exatos, histórico auditável e backups consistentes.
- Definir limites entre domínio, aplicação, persistência e interfaces.
- Registrar em ADR as escolhas que criarem dependência de longo prazo.

## Critérios de aceite

- A stack escolhida executa integralmente no computador do Titular.
- O desenho não exige nuvem, serviço externo ou modelo de IA.
- O núcleo financeiro pode ser utilizado por interfaces futuras sem depender da Web/PWA.
- Migrações, transações, backup e restauração possuem uma estratégia definida.
- As decisões difíceis de reverter estão registradas em ADR.

## Referências

- `spec.md`: Canal do produto, Acesso e segurança, Backup e portabilidade.
- ADR-0003 e ADR-0004.

## Comments

