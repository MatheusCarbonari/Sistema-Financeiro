# Backup e restauração consistentes

Status: accepted

Backups do MVP serão snapshots consistentes criados pela API de backup do SQLite, nunca cópias simples do arquivo enquanto o banco estiver aberto. Cada backup concluído terá manifesto com versão do esquema, versão da aplicação, data, tamanho e checksum; a gravação ocorrerá em arquivo temporário e só será publicada após validação.

## Estratégia de backup

- Um backup automático diário e backups manuais usarão o mesmo mecanismo.
- Somente snapshots concluídos e validados entram na retenção das 30 versões mais recentes.
- O destino será configurável e separado do arquivo ativo; falha no backup não modifica nem bloqueia permanentemente o banco principal.
- Exportação portátil será um artefato versionado distinto do backup de recuperação e será detalhada no Ticket 17.

## Estratégia de restauração

- Antes de restaurar, a aplicação valida manifesto, checksum, integridade SQLite e compatibilidade de esquema em uma cópia temporária.
- A restauração exige confirmação explícita, fecha conexões, cria um backup de segurança do estado atual e substitui o banco somente depois de todas as validações.
- Falha em qualquer etapa mantém o banco atual utilizável; a aplicação reabre e verifica o banco restaurado antes de considerar a operação concluída.
