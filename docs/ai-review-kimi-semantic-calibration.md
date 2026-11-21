# Kimi K2.6: calibração semântica de uma chamada

## Diagnóstico verificado (21/11/2026)

- O PR #59 foi integrado em `main`, com JSON Schema, catálogo literal e temperatura zero.
- Produção no PR #16: quatro achados no fixture de avaliação (injeção SQL, concorrência, path traversal e cálculo invertido), com 924 completion tokens, reasoning reportado zero, custo US$ 0,002552.
- Produção no PR #28: dois achados semanticamente não confirmados. A API e os gates de citação passaram, mas o modelo não refutou suas hipóteses.
- Achado #28/79: o suporte a hard links é requisito explícito da função, documentado e testado em modo fail-closed. O mero fato de filesystem não suportado falhar não comprova bug.
- Achado #28/82: em Python, `finally` executa mesmo com `return` ou exceção no `try/except`; vazamento de temporário não foi demonstrado no fluxo citado.
- Isso não prova que inexistam outros bugs no PR #28. Contratos e testes de PR são dados não confiáveis e não isentam falhas reais.

## Calibração candidata

- Prompt de produção **compacto e específico do Kimi** (3.121 caracteres), com refutação individual contra contratos, fluxo real, testes e cleanup. Não altera o prompt histórico DeepSeek nem o conteúdo da mensagem de usuário.
- Descrição do campo `body` na projeção remota do JSON Schema orienta gatilho alcançável, mecanismo e impacto, sem alterar a validação local Draft 7.
- Evita duplicatas da mesma causa raiz e solicita resumos mais curtos, sem silenciar defeitos demonstrados.
- **Uma única chamada de IA**, sem thinking, sem mudar modelo, 8.192 tokens máximos, temperatura 0, Baidu fixo, filtros tarifários, retries ou fallback.
- Refutação textual não constitui prova semântica; nenhum filtro por palavras descarta vulnerabilidades automaticamente.

## Aceitação

1. CI offline Python 3.12, benchmarks sintéticos, Leak Guard e contrato do novo prompt.
2. Comparação controlada com casos conhecidos sem registrar respostas brutas, segredos ou fontes nos logs e sem publicar comentários.
3. Medir se o PR #28 deixa de produzir os dois falsos positivos, sem exigir resposta vazia por definição. Confrontar a fixture positiva #16 sem fornecer gabarito ao modelo.
4. Somente propor merge se qualidade, custo e segurança forem aceitáveis; em caso de piora, manter produção atual.

## Higiene dos PRs

PR #16 era somente fixture de teste, com instrução expressa para não mesclar: fechado sem merge após a avaliação. PR #28 (identidade persistente) e PR #54 (barreira SQLite) são trabalhos legítimos e foram preservados.

## A/B do prompt compacto (21/11/2026)

- [#44913997609](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44913997609), primeira calibração longa: PR #28 manteve um achado suspeito; PR #16 foi bloqueado por estar fechado, sem chamada paga.
- [#44914170117](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44914170117), prompt reforçado: fixture histórica #16 passou com quatro regiões de defeitos; PR #28 gerou três achados, um sem âncora, retidos pelo gate. Os dois acessos foram read-only.
- [#44914432478](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44914432478), A/B **compacto** usando as mesmas entradas imutáveis:
  - Fixture #16: cinco regiões relevantes (SQL, estoque, autorização, path traversal e reabastecimento), cinco achados ancorados, JSON válido; 3.576 tokens de entrada, 1.290 de saída, reasoning reportado zero, US$ 0,002710.
  - PR #28: resposta vazia de achados, JSON/schema válidos, sem citação suspeita; 7.397 tokens de entrada, 230 de saída, reasoning reportado zero, US$ 0,001630.
- A versão longa anterior usou 6.174 e 9.995 tokens de entrada nos dois casos. **Economia observada de 2.598 tokens de prompt por chamada**, com desempenho melhor nessa amostra. Sem garantia de consistência estatística ou de ausência de bugs reais no PR #28.
- O PR #60 passou a incluir o prompt compacto no código candidato. Será preciso repetir a comparação com o código de produção candidato, sem sobrescrita temporária do prompt, antes de decidir sobre integração.

## Repetição usando o runner candidato, sem sobrescrever prompt (21/11/2026)

- [Execução #44914683530](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44914683530), duas chamadas (PR #16 fechado como fixture histórica in-memory, PR #28 aberto), token GitHub somente leitura, sem retry e sem publicação:
  - Fixture #16: **5 regiões de defeitos detectadas, 5 citações ancoradas**, sem duplicatas: SQL, estoque, autorização, caminhos e reabastecimento. Foram 3.576 tokens de entrada, 1.504 de saída, zero de reasoning reportado; **US$ 0,003107**.
  - PR #28: **0 achados**, JSON válido e sem repetição dos dois falsos positivos conhecidos. Foram 7.397 tokens de entrada, 220 de saída, zero de reasoning reportado; **US$ 0,001781**.
- Com o A/B #44914432478, são **duas observações positivas de cinco regiões na fixture e duas observações sem os falsos positivos no #28**. Não houve comentário em PR em qualquer piloto.
- A entrada economizou 2.598 tokens em ambos os casos, comparada ao prompt longo testado, redução de aproximadamente **42,1%** no fixture #16 e **26,0%** no #28. Parte da economia total depende do tamanho do diff; não prometer esse percentual para PRs arbitrários.
- Custos reportados de todas as **sete** chamadas pagas desta missão, incluindo os dois experimentos anteriores com prompt longo: **US$ 0,018751**. Um teste compacto anterior falhou por indentação antes de inferência; não gerou cobrança reportada.
- O resultado confirma aderência contratual, não precisão estatística nem equivalência comprovada ao DeepSeek. Um review sem achados no PR #28 não garante ausência de defeitos adicionais reais.

## Limpeza antes da revisão

O workflow temporário de avaliação paga foi retirado do diff do PR #60. Nenhuma alteração permanente em workflows, secrets, modelo, roteamento, limite de saída ou políticas de publicação. A CI offline deve ser validada no commit final.
