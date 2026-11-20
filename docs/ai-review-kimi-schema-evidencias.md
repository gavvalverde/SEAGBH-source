# Estabilização Kimi K2.6: JSON Schema e evidências (candidato)

## Falhas observadas

- PR #16, run 44788845517: `quality_manual_review_required`, cinco achados sem citação ancorada, 1265 tokens de saída, nenhum parecer publicado.
- PR #28, run 44789008852: `incomplete_response`/`incomplete_json`, 4626 de 8192 tokens, nenhum parecer publicado.
- Nos dois, `reasoning_tokens=0`. Nem elevar o teto de tokens nem remover o quality gate corrige a causa observada.

## Implementação

1. Preserva modelo `moonshotai/kimi-k2.6`, provedor Baidu, raciocínio desativado, 8192 tokens, uma tentativa, sem fallback e tetos de tarifa 0,42/1,75 USD/M.
2. Projeta o Draft 7 confiável em `response_format.type=json_schema`, `strict=false`, omitindo somente `allOf` condicional do **pedido remoto**. O validador local continua aplicando todo o Draft 7 sem redução de rigor; JSON não é reparado automaticamente.
3. Acrescenta ao prompt de produção Kimi regras para copiar a linha completa do código no campo `evidence`; não altera o prompt compartilhado dos benchmarks.
4. Classifica âncoras ausentes em quatro códigos seguros (`quote_mismatch`, `source_unavailable`, `short_line`, `invalid_reference`) e publica somente contadores numéricos. Não vaza caminhos, linhas, conteúdo bruto ou segredos.
5. Preserva `quality_manual_review_required`, SHA, escopo, deduplicação e política de publicação.

## Limites e validação

- Suporte a `json_schema` no endpoint específico Baidu ainda não homologado; `strict=false` é modo de compatibilidade e **não** garante conformidade. `require_parameters=true` deve impedir roteamento silencioso.
- CI offline Python 3.12, 317 checks sintéticos e 23 Leak Guard precisam passar neste PR.
- Proposta de uma chamada sintética, isolada e paga para homologar `json_schema`, sem publicar comentário em PRs, sem repetição automática.
- Depois, comparar o resultado real nos PRs #16 e #28 com custo controlado, verificando precisão de achados e falsos positivos. Não reexecutar simplesmente um run antigo.
- Sem alterações de SQLite, licença, secrets ou workflows de benchmark. Merge exige decisão separada.

Contrato OpenRouter: https://openrouter.ai/docs/guides/features/structured-outputs

## Homologação e execução de avaliação

- Uma execução inicial de teste do workflow temporário (#44790667492) falhou antes da chamada de inferência devido ao invólucro Bash, sem cobrança reportada. Esse problema foi corrigido na tentativa seguinte.
- [Smoke #44790743639](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44790743639): **sucesso**. Baidu/Kimi K2.6 aceitou `response_format=json_schema` com `strict=false`, `reasoning.enabled=false`, 8192 tokens máximos, JSON válido, 30 tokens de saída e 0 de raciocínio reportado. Custo reportado US$ 0,000209.
- [Avaliação sem publicação #44790916457](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44790916457): exatamente uma chamada para PR #16 no SHA `6721e83a...` e outra para PR #28 no SHA `219bfba2...`; cada uma sem retry/fallback, com GitHub token read-only e método `post()` impedido.
- PR #16: `quality_manual_review_required`, dois achados não ancorados (1 `quote_mismatch` e 1 `short_line`), nenhum comentário. 978 tokens de saída, 0 de raciocínio, US$ 0,002359.
- PR #28: a resposta estruturada passou JSON, schema, escopo e quality gate e atingiu a fronteira de publicação simulada, marcada `would_publish=true`. O resultado `publication_uncertain` é causado **intencionalmente** pelo bloqueio de postagem do dry-run, não por falha de JSON. 716 tokens de saída, 0 de raciocínio, US$ 0,002721. Nenhum comentário foi realmente publicado.
- Total reportado nas três chamadas reais: US$ 0,005289. São observações pontuais, não orçamento cumulativo garantido. A melhora do PR #16 de 5 para 2 achados retidos não demonstra estabilidade estatística.
- Ambos os workflows temporários de inferência são removidos do PR no commit final. A produção na `main` permanece no contrato anterior até aprovação de merge.

## Ajuste final de precisão

O prompt e a descrição de `evidence` na **projeção remota** agora exigem copiar a linha inteira, com pelo menos 12 caracteres não brancos, ou omitir o achado. Não altera o schema local nem transforma texto parcial em prova. Essa última orientação não foi novamente medida em execução paga; requer validação de qualidade em revisões futuras.

## Testes de consistência e mecanismo escolhido (20/11/2026)

- [Execução #44791935487](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44791935487): três tentativas do PR #16 com prompt anterior, todas retidas (3 âncoras inválidas cada); o PR #28 falhou com violação de schema. Portanto, smoke bem-sucedido não significa confiabilidade de revisão.
- [Execução #44792425130](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44792425130): no PR #16, temperatura 0 sem catálogo reteve todos os 5 achados; com catálogo de 31 linhas elegíveis, duas tentativas independentes passaram pelo gate, com 4 e 6 achados. No PR #28, temperatura 0 sem catálogo passou por JSON, Draft 7 e quality gate com 2 achados. Todas as chamadas foram read-only e não publicaram comentários.
- Decisão técnica: fixar `temperature=0.0` **somente no perfil Kimi**, sem mudar o benchmark DeepSeek. Acrescentar um catálogo de linhas exatas do diff já validado, ordenado e limitado a 100 linhas e 18.000 bytes. Linhas curtas (critério existente de 12 caracteres normalizados) ficam fora. Se o catálogo inteiro não couber, omiti-lo por completo, preservando o diff integral e mantendo o quality gate.
- O catálogo é uma ajuda textual e não prova defeito ou substitui a verificação do escopo. Não reescrevemos a resposta do modelo nem aceitamos citações fabricadas. O catálogo usa somente linhas presentes nos hunks fornecidos e não executa código de PR.
- A validação semântica dos 2 achados do PR #28 permanece pendente; linhas literalmente citadas podem sustentar alegações falsas. Novas observações controladas em PRs reais seguem necessárias.
