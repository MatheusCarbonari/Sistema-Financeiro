# Representação monetária exata

Status: accepted

Valores monetários serão representados no domínio por `Decimal` acompanhado obrigatoriamente de um código de moeda, jamais por `float`. Para BRL, valores serão quantizados em centavos com `ROUND_HALF_UP`; na persistência, a quantia será armazenada como inteiro de centavos e a moeda como código de três letras, enquanto interfaces receberão e devolverão quantias decimais como texto.

## Regras

- Aritmética e comparação exigem moedas iguais; conversão cambial futura será uma operação explícita.
- Entradas `float` serão rejeitadas nos limites do sistema para impedir aproximações silenciosas.
- Divisões de Rateios e Parcelas preservam o total original; centavos residuais são distribuídos aos primeiros itens conforme a especificação.
- Percentuais, incluindo Margens de segurança, são calculados em `Decimal` e arredondados uma única vez no limite definido pelo caso de uso.
- A conversão entre `Decimal` e centavos é exata e centralizada no adaptador de persistência.
