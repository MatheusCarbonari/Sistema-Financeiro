# SQLite, transações e migrações

Status: accepted

SQLite será o banco local do MVP, acessado por SQLAlchemy síncrono, com uma `Session` curta e exclusiva por operação lógica. Cada caso de uso confirmado que escreva dados será uma única transação atômica: todas as alterações vinculadas são confirmadas juntas ou todas são revertidas; nenhuma transação ficará aberta enquanto o Titular revisa uma Proposta de lançamento.

## Estratégia de transações

- A camada de aplicação define o início e o fim da unidade de trabalho; repositórios não executam `commit` por conta própria.
- Sucesso confirma a transação somente depois de validar invariantes e gravar todos os efeitos; qualquer falha executa `rollback`.
- Cada requisição ou tarefa usa sua própria `Session`, nunca compartilhada entre threads ou operações concorrentes.
- Leituras usam sessões curtas e não provocam gravações implícitas.
- Restrições de integridade e chaves estrangeiras complementam, mas não substituem, invariantes do domínio.

## Estratégia de migrações

- Toda mudança de esquema recebe uma nova revisão Alembic versionada; revisões já aplicadas não são reescritas.
- Na inicialização controlada, a aplicação identifica revisões pendentes, cria e valida um backup pré-migração e aplica `upgrade` até `head` antes de aceitar uso normal.
- Falha de migração impede abertura para escrita e preserva o banco e o backup para diagnóstico.
- `downgrade` não é o mecanismo normal de recuperação de dados; uma incompatibilidade é recuperada restaurando o backup pré-migração.
- Testes de migração cobrirão banco vazio e atualização a partir de cada versão ainda suportada.
