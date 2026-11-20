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
