# K68-E: piloto sintético offline do AI Reviewer v2

Tudo nesta pasta é preparação. Não há integração HTTP real, leitura de
credencial nem API para publicar em PR. O revisor de PRs reais deve continuar
desativado. Não se assume aprovado US$ 0,45 nem outro teto.

## Quatro casos pré-registrados

Caminhos, código, dados, SHAs do PR e título são inventados. Não copiar diffs do
Equipalvo para este diretório. O material de entrada está em `inputs/`; as
asserções independentes estão em `oracles/` e não entram nos payloads. Para uma
avaliação futura, o operador do envio deve receber só o payload sintético, e a
pessoa que decidir a equivalência causal recebe as evidências sintéticas e
consulta os oráculos separadamente.

| Caso | Gabarito | Gatilho e mecanismo alcançável | Contraprova executável |
| --- | --- | --- | --- |
| K68E-01 | Positivo | Às 18h a troca de comparação inclusiva por estrita passa a rejeitar um horário permitido. | 17h segue permitido e 19h rejeitado nas duas versões. |
| K68E-02 | Positivo | Uma encomenda ainda em rascunho passa de rejeitada a enviada: a guarda rejeita só itens já enviados, não exige mais `packed`. | `packed` continua permitido e `sent` continua rejeitado; a mutação indevida ocorre apenas no gatilho demonstrado. |
| K68E-03 | Negativo, equivalência | Para linhas de dois campos, a desestruturação mantém o mesmo filtro e a leitura do nome. | Lista mista e lista vazia preservam o resultado. Não se afirma equivalência para linhas fora do domínio definido. |
| K68E-04 | Negativo, correção | A lista vazia levantava `IndexError`; o novo guarda retorna `None`. Isso não é regressão atual. | Lista com elemento continua devolvendo o último elemento. |

Os oráculos executam versões antiga e nova dos quatro trechos inventados. Nos
positivos demonstram a violação no pós-patch e a ausência no pré-patch; nas
correções distinguem o defeito antigo do comportamento novo. Antes do envio
hipotético, falhar em qualquer oráculo bloqueia o lote.

## Reprodução autorizada desta etapa

Na raiz, com Python 3.12 e os requisitos isolados já usados pela CI:

```text
python -B .github/ai-review/synthetic-pilot/pilot_offline.py
python -B .github/ai-review/synthetic-pilot/test_pilot_offline.py
```

O primeiro comando executa oráculos e exibe a estimativa. O segundo injeta
respostas simuladas. A etapa offline bloqueia sockets, resolução DNS e conexão
Python; não instancia transporte real, não executa curl nem código do PR
simulado. O plano e schemas de descoberta/refutação são os reais do commit
`fe508a6915ae4c7f6aa7b4c897cebc9b777c5f7e`, travados por SHA-256 (LF canônico),
modelo `moonshotai/kimi-k2.6` e endpoint `chutes/int4`. Arquivos alterados nesses
contratos exigem recalibrar e fixar nova revisão antes de comparar execuções.

O harness mede cobertura textual, valida âncoras e schema local, conserva só
metadados e campos sintéticos causais mínimos em memória e usa IDs imutáveis
na refutação. Não publica. Resposta incompleta, baixa confiança ou
refutação incerta são abstenções; timeout, HTTP inválido e schema/âncora
inválidos são falhas operacionais. Nenhuma dessas situações vira parecer
limpo nem entra em TP, FP ou FN. Uma resposta válida vazia no positivo
é FN. Os contadores de descoberta e de achados retidos são independentes;
a equivalência causal precisa de rótulo humano e não deriva da citação de
uma linha. O denominador corrigido do avaliador legado é reutilizado, não
seu classificador lexical. Sem casos avaliáveis, os contadores e taxas
permanecem não aplicáveis. Contagens dos mocks só validam o harness.

## Preflight e possível rodada futura (não autorizado)

Quatro descobertas, uma por caso, com uma refutação reservada por caso:
**oito chamadas no pior caso**, sem retries. O teto conservador de produção
continua **US$ 0,20 por caso**. Usando os limites de US$ 0,42/M entrada,
US$ 1,80/M saída, 4.096 tokens de conclusão por chamada e a reserva de
200.000 bytes por refutação, o lote soma **US$ 0,40673736** de preflight.
Não é cotação de faturamento nem autorização. Um teto explícito menor
rejeita o lote antes da primeira chamada; o offline não precisa de teto
autorizado por não ter caminho de cobrança.

Se houver autorização operacional específica no futuro: (1) confirmar a rota,
o custo e o tratamento de dados no momento da execução; (2) fixar operador,
teto explícito em dólares e versão dos quatro casos; (3) rodar oráculos,
verificação de integridade, preflight e inspeção dos payloads; (4) em execução
supervisionada independente, permitir somente os payloads inventados e
exatamente o contrato travado, com uma tentativa por requisição e parada
por orçamento; (5) adjudicar sem gabarito no fio, registrar só evidências
sintéticas necessárias, pontuar com avaliação humana independente por etapa.
Não enviar diffs reais, não abrir comentários, não ativar o workflow pago
nem misturar a disponibilidade com a precisão. Quatro casos são um ensaio
de controle, não estimativa representativa de desempenho geral.
