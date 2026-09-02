# Finanças Pessoais

Este contexto representa o controle financeiro individual de uma única pessoa. Não abrange finanças compartilhadas, divisão de despesas ou acertos entre pessoas.

## Linguagem

**Titular**:
A única pessoa a quem pertencem todos os dados financeiros controlados pelo bot.
_Evitar_: Usuário, cliente, membro, participante

**Valor monetário**:
Quantia acompanhada obrigatoriamente de sua moeda; valores em moedas diferentes não formam um total sem uma conversão explícita.
_Evitar_: Número, valor sem moeda

**Conta de recursos**:
Local financeiro do Titular que mantém dinheiro disponível em uma única moeda, como uma carteira ou conta bancária.
_Evitar_: Fonte de dinheiro, conta de saldo

**Conta arquivada**:
Conta de recursos de saldo zero, preservada no histórico e indisponível para novas movimentações até eventual reativação.
_Evitar_: Conta excluída, conta ativa

**Cartão de crédito**:
Meio de pagamento cujas compras geram uma dívida a ser reunida e quitada por meio de uma Fatura.
_Evitar_: Conta bancária, dinheiro disponível

**Limite do cartão**:
Capacidade máxima de crédito concedida ao Cartão de crédito, sem constituir dinheiro ou patrimônio do Titular.
_Evitar_: Saldo da conta, Patrimônio líquido

**Limite disponível**:
Parte do Limite do cartão ainda não comprometida pelo principal pendente de compras realizadas, inclusive Parcelas futuras.
_Evitar_: Saldo da conta, valor da próxima Fatura

**Despesa**:
Valor consumido pelo Titular, reconhecido quando a compra ou uma obrigação confirmada ocorre, mesmo que o pagamento aconteça depois.
_Evitar_: Pagamento de fatura, saída bancária

**Conta a pagar**:
Obrigação confirmada de pagamento futuro, originada por uma Despesa já reconhecida e distinta de uma mera Previsão financeira.
_Evitar_: Previsão, Despesa futura, Fatura

**Pagamento de conta**:
Saída de uma Conta de recursos que quita total ou parcialmente uma Conta a pagar sem criar uma nova Despesa.
_Evitar_: Nova despesa, previsão

**Transferência**:
Movimentação de dinheiro entre duas Contas de recursos do Titular, sem representar Receita, Despesa ou alteração do Patrimônio líquido.
_Evitar_: Receita na conta de destino, Despesa na conta de origem

**Crédito contratado**:
Dívida originada pelo recebimento de recursos de empréstimo ou financiamento; o valor recebido não constitui Receita.
_Evitar_: Receita, limite de cartão

**Amortização**:
Parte de um pagamento de Crédito contratado que reduz o principal da dívida sem constituir Despesa.
_Evitar_: Juros, Despesa

**Juros**:
Custo cobrado pelo uso de crédito e reconhecido como Despesa, separado da Amortização do principal.
_Evitar_: Amortização, principal

**Receita**:
Valor efetivamente recebido pelo Titular, reconhecido separadamente de transferências, Saldos iniciais e Ajustes de saldo.
_Evitar_: Transferência recebida, Saldo inicial, Ajuste de saldo

**Categoria financeira**:
Classificação personalizável e hierárquica, exclusiva de Receita ou de Despesa, usada para analisar a origem ou a finalidade de um valor; pode ser uma categoria raiz ou uma Subcategoria.
_Evitar_: Estabelecimento, descrição, etiqueta

**Subcategoria**:
Categoria financeira subordinada a outra Categoria financeira, permitindo análises tanto no nível detalhado quanto no agrupamento superior.
_Evitar_: Etiqueta, Rateio

**Categoria arquivada**:
Categoria financeira preservada no histórico, mas indisponível para novos Rateios até que seja reativada.
_Evitar_: Categoria excluída, categoria sem movimento

**Rateio**:
Parte do valor de uma Receita ou Despesa atribuída a uma Categoria financeira de mesma natureza; a soma de todos os Rateios deve ser igual ao valor total.
_Evitar_: Parcela, nova despesa, etiqueta

**Fatura**:
Conjunto de movimentações de um Cartão de crédito reunidas em um ciclo delimitado por fechamento e vencimento.
_Evitar_: Despesa, cartão

**Crédito no cartão**:
Valor a favor do Titular mantido no Cartão de crédito, capaz de compensar dívidas futuras sem constituir Saldo da conta.
_Evitar_: Limite disponível, Saldo da conta, Receita

**Encargo financeiro**:
Juro, multa ou outro custo cobrado em razão de uma dívida, reconhecido como Despesa separada do valor original.
_Evitar_: Principal, ajuste silencioso da dívida

**Compra parcelada**:
Despesa reconhecida integralmente na data da compra, cuja dívida total reduz imediatamente o Patrimônio líquido e cujo pagamento é distribuído em Parcelas futuras.
_Evitar_: Despesa recorrente, conjunto de compras mensais

**Parcela**:
Parte programada do pagamento de uma Compra parcelada; antes do pagamento, afeta o planejamento do fluxo de caixa, e, quando paga, reduz em igual valor o Saldo da conta e a dívida.
_Evitar_: Nova despesa, assinatura

**Pagamento de fatura**:
Transferência de uma Conta de recursos que reduz a dívida de uma Fatura sem criar uma nova Despesa.
_Evitar_: Despesa do cartão, nova compra

**Saldo da conta**:
Valor disponível em uma Conta de recursos em determinado momento.
_Evitar_: Saldo, Patrimônio líquido, limite do cartão

**Saldo negativo**:
Valor devido quando uma conta bancária fica abaixo de zero, reduzindo o Patrimônio líquido sem representar dinheiro disponível.
_Evitar_: Limite disponível, Saldo da conta positivo

**Saldo inicial**:
Valor conhecido de uma Conta de recursos no início de seu acompanhamento pelo bot, sem representar Receita ou Despesa do período.
_Evitar_: Receita inicial, depósito

**Ajuste de saldo**:
Correção explícita e justificada que reconcilia o Saldo da conta calculado com o valor real, sem representar Receita ou Despesa.
_Evitar_: Receita, Despesa, alteração manual invisível

**Patrimônio líquido**:
Total dos Saldos das contas do Titular menos suas dívidas pendentes, incluindo as de Cartão de crédito.
_Evitar_: Saldo da conta, limite disponível

**Resumo financeiro**:
Visão conjunta do dinheiro disponível, das dívidas, do Patrimônio líquido e dos compromissos futuros do Titular.
_Evitar_: Saldo, extrato

**Alerta financeiro**:
Aviso sobre compromisso próximo ou atrasado, sem confirmar pagamento nem produzir qualquer efeito financeiro.
_Evitar_: Pagamento automático, Movimentação realizada

**Movimentação realizada**:
Entrada, saída ou transferência que efetivamente ocorreu e, por isso, altera a posição financeira atual do Titular.
_Evitar_: Previsão, agendamento

**Proposta de lançamento**:
Interpretação ainda não confirmada de uma operação financeira, sem qualquer efeito sobre saldos, dívidas ou relatórios.
_Evitar_: Movimentação realizada, rascunho contabilizado

**Data efetiva**:
Data em que um fato financeiro ocorreu e passou a produzir efeitos nos saldos, dívidas e relatórios.
_Evitar_: Momento do registro, data de digitação

**Momento do registro**:
Instante em que um fato foi informado ao bot, preservado para auditoria e distinto de sua Data efetiva.
_Evitar_: Data efetiva

**Correção**:
Alteração explícita de uma Movimentação realizada que preserva os dados anteriores e o motivo da mudança.
_Evitar_: Sobrescrita silenciosa, nova movimentação independente

**Cancelamento**:
Invalidação de uma Movimentação realizada, retirando seus efeitos dos cálculos sem apagá-la do histórico.
_Evitar_: Exclusão definitiva, estorno

**Reembolso**:
Devolução real de valor vinculada a uma Despesa anterior, reduzindo seu custo líquido sem constituir Receita ou apagar a compra original.
_Evitar_: Receita, Cancelamento, exclusão da compra

**Previsão financeira**:
Entrada ou saída esperada para uma data futura, usada no planejamento sem alterar o Saldo da conta ou o Patrimônio líquido atual.
_Evitar_: Movimentação realizada, Conta a pagar, saldo atual

**Recorrência**:
Regra que produz uma sequência de Previsões financeiras sem realizar automaticamente as entradas ou saídas esperadas.
_Evitar_: Movimentação realizada, parcelamento

**Ocorrência prevista**:
Previsão financeira individual pertencente a uma Recorrência, confirmada separadamente quando o evento financeiro realmente acontece.
_Evitar_: Movimentação realizada, Recorrência inteira

**Margem de segurança**:
Valor adicional e explícito reservado acima de uma Previsão financeira para absorver incerteza, sem representar gasto esperado ou realizado.
_Evitar_: Despesa, valor esperado ocultamente inflado

**Orçamento**:
Limite de gasto definido para uma Categoria financeira e um período, distinto de uma estimativa do que provavelmente acontecerá.
_Evitar_: Previsão financeira, Saldo projetado, Margem de segurança

**Saldo projetado**:
Estimativa do Saldo da conta em uma data futura, calculada a partir do saldo atual e das Previsões financeiras até aquela data.
_Evitar_: Saldo da conta, Patrimônio líquido

**Saldo projetado conservador**:
Estimativa futura que considera tanto as Previsões financeiras quanto suas Margens de segurança explicitamente identificadas.
_Evitar_: Saldo da conta, Saldo projetado sem margem
