# Reavaliar inicialização local no Windows

Status: open

## Objetivo

Definir uma forma confiável de iniciar e encerrar a aplicação no Windows sem
depender do launcher gerado `bot-financeiro.exe`.

## Contexto

- A execução de `.venv\Scripts\bot-financeiro.exe` no terminal integrado do
  VS Code coincidiu com um travamento grave do sistema e exigiu desligamento
  forçado.
- O comportamento não pôde ser reproduzido com segurança e não há evidência
  conclusiva que atribua a causa ao Norton, Python, Uvicorn ou ao código da
  aplicação.
- O launcher e o `uvicorn.exe` usam o mesmo stub gerado pelo `pip`; ambos são
  não assinados.
- O histórico do Norton não contém bloqueio, quarentena, regra de programa ou
  execução em Sandbox para `bot-financeiro.exe`.
- Não executar novamente o launcher em uma sessão normal do Windows enquanto
  não existir uma estratégia de reprodução isolada e recuperável.

## Solução provisória

Iniciar a aplicação sem o launcher próprio:

```powershell
.venv\Scripts\python.exe -m uvicorn bot_financeiro.interface.web:create_app --factory --host 127.0.0.1 --port 8000
```

Esse caminho foi verificado manualmente e encerra normalmente com `Ctrl+C`.

## Escopo futuro

- Decidir se o entrypoint `bot-financeiro` deve ser removido do
  `pyproject.toml` ou substituído por outra forma de inicialização.
- Documentar um único comando suportado para Windows.
- Investigar o launcher somente em ambiente isolado que possa ser descartado
  ou reiniciado sem risco aos dados e à sessão principal.
- Se surgir um seam fiel ao incidente, criar teste de regressão antes da
  correção.

## Critérios de aceite

- A inicialização suportada não executa `bot-financeiro.exe`.
- A aplicação continua vinculada exclusivamente a `127.0.0.1` por padrão.
- `Ctrl+C` encerra o servidor e devolve o controle ao terminal.
- A forma recomendada de iniciar e encerrar a aplicação está documentada.
- Nenhuma alegação causal sobre o incidente é registrada sem evidência
  reproduzível.

## Referências

- Ticket 02 — Criar shell Web/PWA local.
- ADR-0004 — Operação local antes do acesso remoto.

## Comments

