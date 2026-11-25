# K68: diagnóstico de transporte e elegibilidade do Kimi

Escopo: [issue #68](https://github.com/gavvalverde/SEAGBH-source/issues/68). Este
trabalho não muda modelo, provedor, fallbacks, limites de preço, orçamento,
credenciais, envio de diffs nem número de tentativas. Não foi feita inferência
no OpenRouter, nem foram consultados dados de conta.

## Evidência das três execuções

| PR | Execução GitHub Actions | Estágio e código | Blocos concluídos/planejados | Publicado |
| --- | --- | --- | --- | --- |
| #89 | [45609648292](https://github.com/gavvalverde/SEAGBH-source/actions/runs/45609648292) | `model / http_error` | 0/2 | Não |
| #93 | [45616770611](https://github.com/gavvalverde/SEAGBH-source/actions/runs/45616770611) | `model / http_error` | 0/1 | Não |
| #92 | [45617887044](https://github.com/gavvalverde/SEAGBH-source/actions/runs/45617887044) | `model / http_error` | 0/1 | Não |

No código anterior, `OpenRouter.complete()` já anexava `reply.status` ao
`Problem("http_error", ...)`. A captura em `run_review_v2()` encaminhava o
código textual do erro, mas não o status numérico para o resultado público do
runner. Isso explica a perda de diagnóstico, não o motivo de cada recusa.
Sem o número HTTP, não se pode reclassificar retroativamente esses logs como
falha de rota, credencial, saldo, limite, parâmetros ou indisponibilidade
temporária. Preparar os diffs e o plano não equivale a concluir blocos ou
realizar revisão semântica.

## Resultado público e ação do operador

Os campos de diagnóstico descrevem o erro, o estágio e a rota **configurada**,
não garantem que um provedor tenha recebido o pedido. Código HTTP é um número
validado, não extraído de mensagem ou corpo. A classificação é fixa e orienta
a investigação, não prova a causa. Não incluir corpos, mensagens/headers do
provedor, chaves, prompts, diffs ou identificadores de conta em issues ou
logs públicos.

Use a [referência oficial de erros](https://openrouter.ai/docs/api_reference/errors-and-debugging):
`400/422` pedem verificação do contrato; `401/403`, verificação autorizada
da autenticação/políticas; `402`, do saldo ou limite; `404`, da rota/modelo
(inclusive filtros); `429`, de limites/capacidade; `5xx`, do serviço. Um
timeout ou erro de conexão não tem um status HTTP comprovado. Nenhuma dessas
classes autoriza retry, alteração de credencial ou liberação de parecer.

## Catálogo público e próximos passos

A consulta pública [de endpoints do modelo](https://openrouter.ai/api/v1/models/moonshotai/kimi-k2.6/endpoints)
em 25/11/2026 às 13:06 UTC (13 entradas) não apresentou Baidu Qianfan para Kimi K2.6. Baidu ainda aparece
na [lista geral de provedores](https://openrouter.ai/Providers), o que não
significa elegibilidade para este modelo. **Nenhum endpoint publicado atendia
aos dois tetos de US$ 0,42 de entrada e US$ 1,75 de saída por milhão de tokens**;
não resolveríamos o problema simplesmente liberando fallback sob o mesmo teto.

| Rota pública | USD/M entrada / saída | Recursos anunciados no catálogo | Política geral no diretório OpenRouter |
| --- | --- | --- | --- |
| Chutes int4 | 0,39 / 1,80 | `reasoning` e `structured_outputs`; não lista `response_format` no snapshot | Sem treino, retém prompts |
| DigitalOcean | 0,57 / 2,40 | `reasoning`, `response_format` e `structured_outputs` | Sem treino, retenção zero no diretório |
| Inceptron int4 | 0,5655 / 3,06 | `reasoning`, `response_format` e `structured_outputs` | Sem treino, retenção zero no diretório |
| Moonshot AI | 0,95 / 4,00 | `reasoning`, `response_format` e `structured_outputs` | Sem treino, retém prompts |

A [seleção de provedor](https://openrouter.ai/docs/guides/routing/provider-selection)
aplica filtros de provedor, preço e parâmetros. A [documentação de saídas estruturadas](https://openrouter.ai/docs/guides/features/structured-outputs) associa o marcador `structured_outputs` ao campo `response_format` no payload; a ausência do nome literal no catálogo de Chutes não demonstra incompatibilidade. Os rótulos de suporte do
catálogo não demonstram aceitação do payload exato, das opções aninhadas
(como `reasoning.enabled=false`), do schema, das janelas de contexto ou
da telemetria de uso exigida pelo runner. Não são informação de saldo,
preferências, região, chaves, guardrails ou capacidade efetiva da conta. A
política geral do provedor não substitui verificar a política do
**endpoint** ([retenção](https://openrouter.ai/blog/insights/zero-data-retention/)).
Disponibilidade pública pode mudar; a falta de rota no snapshot é uma
explicação plausível para a indisponibilidade atual, não prova os códigos
das três execuções históricas.

O módulo `route_snapshot.py` compara apenas os metadados de um JSON fornecido localmente com a rota fixa; ele não é importado pelo runner nem consulta a rede. Os testes usam fixtures, não credenciais.

1. Integrar somente o diagnóstico offline após aprovação humana. Conferir
   novamente o catálogo público antes de considerar reativação.
2. Se autorizado separadamente, o operador pode conferir saldo/restrições
   e [visibilidade de modelos para a conta](https://openrouter.ai/docs/client-sdks/typescript/api-reference/models/models#listforuser)
   sem enviar diffs nem invocar inferência. Modelo visível não prova rota.
3. Se Baidu não voltar a um endpoint elegível e aceitável para dados, comparar
   uma rota explícita alternativa com fallback desligado. DigitalOcean é
   candidata a comparar, não substituição automática. Mudança de provedor,
   teto, modelo, proteção de dados ou fallback exige autorização específica e
   revisão do orçamento agregado, sem relaxar validação do schema.
4. Somente com autorização adicional: uma chamada limitada com material
   sintético, rota e orçamento fixos e sem retries, para verificar
   conectividade, aceitação do schema e reasoning reportado. Não enviar código
   real para testar a disponibilidade. Rede recuperada não valida precisão:
   falsos positivos e negativos continuam etapa independente da #68.
