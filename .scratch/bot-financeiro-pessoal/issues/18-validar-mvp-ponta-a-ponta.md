# Validar MVP ponta a ponta

Status: open
Blocked by: 14, 15, 16, 17

## Objetivo

Verificar os critérios de aceite do produto por cenários completos e reconciliáveis.

## Escopo

- Cenário de saldo inicial, Receita, Despesa rateada e Transferência.
- Compra parcelada, Faturas, pagamento parcial, Encargo financeiro e Reembolso.
- Conta a pagar, Recorrência variável, Margem de segurança e atraso.
- Correção retroativa e Cancelamento com histórico.
- Backup, restauração e nova apuração dos mesmos totais.
- Operação local sem tráfego financeiro para serviços externos.

## Critérios de aceite

- Todos os critérios de aceite de `spec.md` possuem cobertura verificável.
- Saldos, dívidas, Patrimônio líquido, limites e projeções fecham exatamente em cada cenário.
- Resultados antes e depois de backup/restauração são idênticos.
- Fluxos conversacionais e estruturados produzem o mesmo efeito de domínio.
- Não há dependência de internet nem exposição de rede no caminho principal do MVP.
- Divergências encontradas geram tickets próprios antes do encerramento do MVP.

## Referências

- `spec.md`: documento completo, especialmente Critérios de aceite do MVP.
- `CONTEXT.md` e ADRs 0001–0004.

## Comments

