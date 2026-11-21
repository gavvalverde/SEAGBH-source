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
