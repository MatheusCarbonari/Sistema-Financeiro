# Implementar Alertas financeiros locais

Status: open
Blocked by: 02, 07, 08, 10

## Objetivo

Avisar sobre compromissos próximos ou atrasados sem realizar operações automaticamente.

## Escopo

- Alertas dentro da aplicação e no Resumo financeiro.
- Notificações locais opcionais mediante permissão.
- Antecedência configurável, com padrão de três dias.
- Faturas, Contas a pagar e Ocorrências previstas.

## Critérios de aceite

- Alertas distinguem próximo, vencendo hoje e atrasado.
- A ausência de confirmação nunca marca item como pago ou realizado.
- Negar permissão de notificação não prejudica alertas dentro da aplicação.
- Nenhum alerta usa e-mail, Telegram, WhatsApp ou outro serviço externo.
- Fuso do Titular é respeitado nos limites de data.

## Referências

- `spec.md`: seção Alertas e regras 37 e 60.
- `CONTEXT.md`: Alerta financeiro.

## Comments

