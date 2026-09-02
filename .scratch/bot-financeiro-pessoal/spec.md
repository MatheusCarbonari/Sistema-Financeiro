# Bot financeiro pessoal

Status: Definido para MVP

## Objetivo

Ajudar uma única pessoa a registrar e consultar sua vida financeira, distinguindo a posição atual do planejamento futuro.

## Canal do produto

- O MVP será uma aplicação web responsiva/PWA com conversa e apresentações estruturadas dos dados.
- O núcleo financeiro será independente do canal de interação.
- Telegram é uma evolução prevista para registros e consultas rápidas.
- WhatsApp não faz parte do MVP.

## Acesso e segurança

- O MVP será executado e acessado somente no computador local do Titular.
- O MVP não oferecerá acesso pela internet nem sincronização em nuvem.
- A possibilidade futura de acesso remoto não autoriza expor o serviço local antes de uma revisão de segurança.
- Identidade, armazenamento e configuração devem permanecer como limites explícitos para permitir evolução posterior.
- No MVP, a sessão do sistema operacional do Titular é a fronteira de confiança; não há desbloqueio adicional do bot.
- Proteção contra outros usuários do computador e criptografia adicional dos dados em repouso ficam para fases posteriores.
- O MVP não envia mensagens ou dados financeiros a modelos de IA ou outros serviços externos.
- A experiência conversacional inicial usa interpretação determinística, comandos e perguntas guiadas locais.
- O uso futuro de IA local ou externa depende de avaliação específica de privacidade e segurança.

## Backup e portabilidade

- O MVP cria backup local automático diário e conserva as 30 versões mais recentes.
- O destino do backup é configurável para permitir unidade externa ou pasta sincronizada no futuro.
- O Titular pode solicitar uma exportação completa a qualquer momento.
- A exportação oferece um formato portátil além do formato próprio usado para restauração.
- Uma restauração é validada e exige confirmação antes de substituir os dados atuais.

## Escopo confirmado

- Um único Titular, com dados exclusivamente individuais.
- Controle de Contas de recursos e Cartões de crédito.
- Receitas, Despesas, transferências, Faturas e Pagamentos de fatura.
- Previsões financeiras que apoiam o planejamento sem alterar a posição atual.
- Compras parceladas, com separação entre o reconhecimento da Despesa, a dívida assumida e o fluxo de caixa de cada Parcela.
- Consultas de posição atual e projetada.
- Apenas BRL no MVP, preservando a moeda em todo Valor monetário para permitir expansão futura.

## Regras confirmadas

1. Uma compra no Cartão de crédito cria a Despesa e a dívida na data da compra.
2. O Pagamento de fatura reduz simultaneamente o Saldo da conta pagadora e a dívida, sem criar outra Despesa.
3. Uma Compra parcelada reduz o Patrimônio líquido pelo valor integral na data da compra.
4. As Parcelas distribuem o pagamento no tempo e afetam o Saldo projetado dos respectivos períodos.
5. Previsões financeiras não alteram o Saldo da conta nem o Patrimônio líquido atuais.
6. Uma consulta genérica por "saldo" retorna um Resumo financeiro decomposto, em vez de um número sem qualificação.
7. Valores em moedas diferentes permanecem separados enquanto não houver uma conversão explícita.
8. Cada Conta de recursos possui uma única moeda e somente movimentações nessa moeda compõem seu saldo.
9. Uma Conta de recursos pode começar com um Saldo inicial, que não é Receita.
10. Divergências podem ser corrigidas por um Ajuste de saldo explícito e justificado, sem classificá-lo como Receita ou Despesa.
11. Saldos iniciais e Ajustes de saldo permanecem visíveis no histórico.
12. Uma Despesa preserva seu valor total e pode ser dividida em Rateios associados a Categorias financeiras.
13. A soma dos Rateios deve ser exatamente igual ao valor total da Despesa.
14. O Titular pode criar e editar Categorias financeiras organizadas hierarquicamente.
15. Uma Categoria financeira nunca utilizada pode ser excluída definitivamente.
16. Uma Categoria financeira com histórico pode ser arquivada e reativada; seu histórico permanece nos relatórios.
17. Para excluir definitivamente uma categoria utilizada, todos os seus Rateios devem antes ser reclassificados.
18. Uma categoria com Subcategorias não pode ser excluída enquanto possuir filhas.
19. Ausência de movimento não arquiva uma categoria automaticamente.
20. Por padrão, relatórios omitem categorias sem movimento no período consultado.
21. Categorias-pai e Subcategorias podem receber Rateios diretamente.
22. Ao receber uma categoria-pai que possui Subcategorias, o bot sugere o detalhamento, mas permite continuar sem ele.
23. Consultas de uma categoria-pai agregam seus Rateios diretos e os de suas Subcategorias.
24. No MVP, a hierarquia possui no máximo dois níveis: categoria e Subcategoria.
25. Receitas e Despesas possuem árvores de Categorias financeiras separadas.
26. Cada Categoria financeira pertence exclusivamente a uma das duas naturezas: Receita ou Despesa.
27. Receitas também podem ser divididas em Rateios, sujeitos à mesma regra de soma exata.
28. Uma Receita é reconhecida somente quando o valor é efetivamente recebido em uma Conta de recursos.
29. Antes do recebimento, uma entrada esperada é uma Previsão financeira e não afeta a posição atual.
30. Uma estimativa de gasto permanece Previsão financeira e não afeta a posição atual.
31. Uma obrigação confirmada cria uma Despesa e uma Conta a pagar, reduzindo o Patrimônio líquido sem alterar imediatamente o Saldo da conta.
32. O Pagamento de conta reduz o Saldo da conta e quita total ou parcialmente a Conta a pagar, sem criar outra Despesa.
33. Uma Transferência vincula uma Conta de recursos de origem a outra de destino, reduzindo e aumentando os respectivos saldos pelo mesmo valor.
34. Transferências não compõem totais de Receita ou Despesa nem alteram o Patrimônio líquido.
35. Uma tarifa associada a uma Transferência é registrada separadamente como Despesa categorizada.
36. Uma Recorrência gera Ocorrências previstas sem realizar automaticamente qualquer movimentação.
37. A passagem da data prevista não altera saldos; a ocorrência permanece pendente ou atrasada até confirmação explícita.
38. A confirmação vincula a Ocorrência prevista à Movimentação realizada correspondente para evitar duplicidade.
39. A edição de uma Recorrência permite alterar somente uma ocorrência ou a ocorrência escolhida e as seguintes.
40. Uma Recorrência possui um valor esperado, mas cada Ocorrência prevista pode ser ajustada individualmente.
41. Ao confirmar uma cobrança por valor diferente do previsto, o valor confirmado cria uma única Despesa e substitui a previsão daquela ocorrência.
42. Após uma divergência, o bot pode oferecer a atualização do valor esperado para as ocorrências seguintes, sem fazê-la automaticamente.
43. Uma Previsão financeira pode possuir uma Margem de segurança separada de seu valor esperado.
44. A Margem de segurança afeta somente o Saldo projetado conservador, nunca a posição atual ou os totais realizados.
45. Consultas de planejamento identificam claramente o valor esperado, a Margem de segurança e o impacto conservador resultante.
46. A Margem de segurança pode ser definida como valor fixo ou percentual por Recorrência.
47. Uma Ocorrência prevista pode substituir a margem herdada sem alterar as demais.
48. Nenhuma margem é aplicada por padrão ou alterada por sugestão sem confirmação do Titular.
49. Previsões não realizadas podem ser editadas ou excluídas.
50. A Correção de uma Movimentação realizada preserva os valores anteriores, o momento e o motivo da alteração.
51. O Cancelamento remove os efeitos de uma Movimentação realizada dos cálculos sem apagá-la do histórico.
52. A Correção de uma operação vinculada recalcula seus efeitos dependentes, como dívida e Parcelas ainda abertas.
53. Correções com efeitos retroativos relevantes exigem confirmação explícita do Titular.
54. Um Reembolso preserva e referencia a Despesa original, sem ser classificado como Receita.
55. Um Reembolso recebido em Cartão de crédito reduz sua dívida ou cria crédito; quando recebido em Conta de recursos, aumenta o respectivo saldo.
56. Relatórios apresentam o gasto líquido por padrão e permitem decompor Despesa bruta e Reembolso.
57. Cancelamento corrige um registro que não deveria produzir efeitos; Reembolso registra uma devolução financeira que realmente ocorreu.
58. Cada Cartão de crédito possui dia de fechamento e dia de vencimento.
59. Compras são atribuídas à Fatura conforme o fechamento, com Correção explícita quando a Fatura real divergir.
60. Uma Fatura pode estar aberta, fechada, parcialmente paga, paga ou atrasada.
61. Pagamentos parciais reduzem a dívida pelo valor efetivamente pago, mantendo o restante devido.
62. Encargos financeiros são Despesas vinculadas à Fatura e separadas da dívida original.
63. Pagamento superior à dívida somente é aceito quando representa Crédito no cartão real.
64. Cada Cartão de crédito pode possuir um Limite do cartão.
65. O Limite disponível desconta todo o principal ainda devido de compras realizadas, inclusive Parcelas futuras.
66. Limites de cartão não compõem Saldo da conta ou Patrimônio líquido e são exibidos separadamente no Resumo financeiro.
67. Carteira, conta corrente, conta digital, poupança e tipos personalizados são variações principalmente organizacionais de Conta de recursos.
68. Uma Conta de recursos somente pode ser arquivada com saldo zero.
69. Uma Conta arquivada preserva o histórico, não aceita novas movimentações e pode ser reativada.
70. Contas bancárias podem possuir Saldo negativo; carteira ou dinheiro físico não podem.
71. O Saldo negativo reduz o Patrimônio líquido, e seus Encargos financeiros são Despesas separadas.
72. Contrato, limite e amortização de cheque especial ficam fora do MVP como parte de Crédito contratado.
73. Relatórios de Despesas atribuem o valor integral de uma Compra parcelada à sua Data efetiva.
74. Consultas de fluxo de caixa distribuem os pagamentos pelas datas de suas Parcelas.
75. Faturas exibem apenas as Parcelas atribuídas ao respectivo ciclo.
76. Consultas distinguem explicitamente gasto incorrido de dinheiro efetivamente movimentado no período.
77. Valores em BRL possuem precisão de centavos no MVP.
78. Divisões automáticas de Parcelas e Rateios preservam a soma exata do valor original.
79. Centavos residuais são atribuídos aos primeiros itens da divisão, com possibilidade de Correção para refletir a cobrança real.

## Consultas essenciais do MVP

- Resumo financeiro atual decomposto.
- Receitas e Despesas por período.
- Gastos por Categoria financeira e Subcategoria, respeitando Rateios.
- Extrato de uma Conta de recursos.
- Compras, dívida, Fatura atual ou futura e Limite disponível por Cartão de crédito.
- Contas a pagar e Ocorrências previstas próximas ou atrasadas.
- Saldo projetado e Saldo projetado conservador até uma data.
- Busca de movimentações por descrição, valor, categoria, conta ou período.
- Comparação entre valores previstos e realizados.

## Confirmação conversacional

- O bot não inventa campos financeiros obrigatórios e pergunta apenas pelos dados ausentes ou ambíguos.
- Toda alteração começa como Proposta de lançamento sem efeito financeiro.
- Antes de salvar, o bot apresenta valor, data, conta ou cartão, categoria e efeito financeiro de forma estruturada.
- Uma Proposta de lançamento somente produz efeitos depois da confirmação do Titular.
- Correções, Cancelamentos, restaurações e ações em massa sempre exigem confirmação explícita.
- Um modo rápido com possibilidade de desfazer pode ser avaliado após o MVP.

## Datas e localidade

- Saldos e relatórios usam a Data efetiva do fato financeiro.
- O Momento do registro é preservado separadamente para auditoria.
- Lançamentos retroativos são permitidos e seus efeitos relevantes são apresentados antes da confirmação.
- Expressões relativas de data usam o fuso configurado do Titular.
- O padrão inicial é português do Brasil, fuso `America/Sao_Paulo` e calendário civil local.
- Localidade e fuso podem se tornar configuráveis depois do MVP.

## Entrada de dados

- No MVP, lançamentos são informados manualmente pela conversa ou por formulários.
- Cada registro preserva sua origem, inicialmente manual, para permitir novos canais no futuro.
- Importação exige deduplicação e conciliação antes de entrar no escopo.

## Alertas

- O MVP apresenta Alertas financeiros para Faturas, Contas a pagar e Ocorrências previstas próximas ou atrasadas.
- Alertas aparecem no Resumo financeiro e podem usar notificações locais do sistema mediante autorização.
- A antecedência é configurável, com padrão inicial de três dias.
- Não há envio por e-mail, Telegram ou WhatsApp no MVP.
- Um Alerta financeiro nunca realiza ou quita uma operação automaticamente.

## Resposta padrão de saldo

O Resumo financeiro deve apresentar, no mínimo:

- dinheiro disponível agora;
- dívida total de cartão;
- Patrimônio líquido;
- próximos compromissos relevantes;
- dinheiro disponível projetado após esses compromissos.

## Critérios de aceite do MVP

- Cadastrar, consultar, editar, arquivar e reativar Contas de recursos e Cartões de crédito segundo suas regras de histórico.
- Registrar e consultar Receitas, Despesas, Rateios, Transferências, Compras parceladas, Faturas, Contas a pagar, pagamentos e Reembolsos.
- Manter Saldos das contas, dívidas, Patrimônio líquido e limites de cartão reconciliáveis a partir do histórico.
- Criar Previsões financeiras e Recorrências, compará-las com o realizado e calcular projeções normal e conservadora.
- Responder ao conjunto de consultas essenciais com distinção explícita entre gasto, pagamento, saldo, dívida e patrimônio.
- Exigir confirmação antes de produzir efeitos financeiros e preservar Correções e Cancelamentos para auditoria.
- Alertar localmente sobre compromissos sem realizá-los automaticamente.
- Criar, validar, exportar e restaurar backups conforme as regras de retenção e confirmação.
- Operar integralmente no computador local, sem enviar dados financeiros para serviços externos.

## Fora do escopo confirmado

- Múltiplos titulares.
- Finanças compartilhadas.
- Divisão de despesas e acertos entre pessoas.
- Controle de Crédito contratado, incluindo empréstimos e financiamentos.
- Integrações com Telegram e WhatsApp.
- Hospedagem remota e acesso pela internet.
- Importação de extratos CSV/OFX e integração bancária.
- Orçamentos por Categoria financeira.
- Investimentos, valorização de ativos e rendimentos ainda não realizados.

## Evoluções futuras

- Conversão e consolidação de valores em moedas diferentes após o MVP.
- Contas a receber e reconhecimento de receitas antes do recebimento.
- Inclusão futura de Crédito contratado, preservando a distinção entre principal, Amortização, Juros e tarifas.
- Deduplicação e conciliação para futuras importações de extratos.
- Orçamentos por categoria, preservando sua distinção em relação a Previsões financeiras.
- Proteção futura contra outros usuários locais, criptografia adicional, autenticação e acesso remoto.
- Integrações futuras com Telegram, WhatsApp e instituições financeiras.
- IA local ou externa para interpretação de linguagem natural, condicionada a avaliação de privacidade e segurança.
