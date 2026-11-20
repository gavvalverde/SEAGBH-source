# Migração experimental do AI Reviewer: Kimi K2.6 e GLM 5.3 Flash

## Estado e escopo

Preparação local em 2026-11-20, branch `feat/ai-review-kimi-k26-pilot`, base
`f1515ecc2886bcf6f2ee3d337af83b40800f258e`. A main foi conferida com origin/main
após fetch e estava sincronizada. A publicação foi autorizada exclusivamente em
PR Draft para revisão estática e CI offline, sem merge ou execução paga.
O PR #54 não faz parte desta missão e não foi alterado.

O perfil ativo em `runner.build_production_payload()` continua DeepSeek V4.1
Flash 1017, com teto compartilhado de 65.536 tokens e orçamento de raciocínio
solicitado de 16.384. Não há promoção automática de candidato experimental.
`prompt_builder.py`, seus defaults de benchmark, schema Draft 7, coleta por SHA,
evidências, quality gate, deduplicação e regras de publicação foram preservados.

## Auditoria e decisão de implementação

| Componente | Constatação e tratamento |
| --- | --- |
| `runner.py` | O perfil de produção já é aplicado separadamente do builder compartilhado. Mantido; acrescido somente um código seguro de observação do raciocínio na telemetria. |
| `transport.py` | Antes reconhecia opt-out apenas pela igualdade exata com `effort=none` e dependia de contagem positiva. Agora também reconhece `enabled=false`, campos de raciocínio retornados e ausência de contadores; o modo estrito do candidato Kimi recusa ausência de confirmação numérica. |
| `core.py` | Encaminha o prompt compartilhado e valida JSON estrito/Draft 7, escopo e SHA. Não alterado. JSON mode remoto não substitui essas verificações. |
| `prompt_builder.py` | Contém instruções e exemplo JSON do reviewer. Não alterado; os perfis experimentais reutilizam as mensagens efetivamente geradas. |
| `evidence.py` / `quality_gate.py` | Citação literal, limites e retenção conservadora continuam obrigatórios. Não alterados. Uma âncora textual não prova o mecanismo do defeito. |
| Contratos existentes | Os 80 testes anteriores do runner e os demais testes do reviewer continuam presentes. O experimento não usa o parser tolerante do benchmark como validador de publicação. |

Os candidatos estão em `.github/ai-review/production/experimental_profiles.py`.
É uma configuração separada, com funções sem CLI, descoberta de credenciais,
coleta remota ou publicação. A configuração não foi ligada a `run_review()`.

## Perfis propostos

| Campo | Kimi | Comparador GLM |
| --- | --- | --- |
| Identificador do perfil | `kimi-k26-nonthinking` | `glm53-flash-low` |
| Modelo fixado | `moonshotai/kimi-k2.6` | `z-ai/glm-5.3-flash` |
| Provedor candidato | `moonshotai` | `deepinfra` |
| Controle OpenRouter | `reasoning: {enabled: false}` | `reasoning: {effort: low}` |
| `max_tokens` | 8192 | 8192 |
| Temperatura | 0.3 | 0.3 |
| `response_format` | `{type: json_object}` | `{type: json_object}` |
| `provider.require_parameters` | true | true |
| Roteamento | `only` para o provedor fixado, `allow_fallbacks=false` | Idem |
| Teto de preço proposto, USD/milhão | entrada 1.00; saída 4.00 | entrada 0.20; saída 0.60 |

Referências financeiras fornecidas pelo supervisor para os provedores fixados:

| Modelo / provedor | Entrada, USD/milhão | Saída, USD/milhão |
| --- | --- | --- |
| Kimi K2.6 / Moonshot AI | 0.95 | 4.00 |
| GLM 5.3 Flash / DeepInfra | 0.1005 | 0.335 |

São referências para planejamento, não garantias futuras nem preços agregados
de outros provedores. Os campos `provider.max_price` continuam com os tetos por
token da primeira tabela; não implementam um orçamento total ou por chamada.

Não se envia `thinking: {type: disabled}` nativo da Moonshot. A interface
unificada documentada pelo OpenRouter é `reasoning.enabled=false`; encaminhar
o formato nativo no Chat Completions não foi presumido. `exclude=true` apenas
oculta raciocínio e não é tratado como desativação. GLM solicita esforço baixo:
nenhum contador comprova que esse esforço qualitativo foi aplicado, e não há
alegação de thinking desativado. 8192 é teto compartilhado de completion, não
reserva independente de JSON visível nem garantia financeira.

## Capacidades dos endpoints: pendência explícita

Foram consultadas somente páginas públicas de documentação e catálogo. As
páginas dos modelos anunciam `response_format`; os provedores candidatos estão
listados publicamente. Isso não confirma a combinação exata por endpoint, os
limites atuais, as permissões da conta nem a aplicação efetiva do opt-out.
Não foi feita consulta à API de catálogo/endpoints nem inferência OpenRouter.

Por isso nenhum registro real de capacidades está embutido como aprovado.
`build_experimental_payload()` e `experimental_transport()` recusam a ausência
de `EndpointCapabilities` com `experimental_endpoint_unverified`. O registro
precisa confirmar modelo/provedor, parâmetros, modo `json_object`, limite de
completion >=8192 e, respectivamente, opt-out ou esforço `low`. Divergências
são recusadas sem reduzir controles ou escolher outro endpoint.

O registro é uma declaração de configuração confiável, não uma autenticação
nem uma consulta remota. Deve vir de metadados/documentação do endpoint,
revisados pelo supervisor; nunca do PR ou do modelo. Os registros usados nos
testes são explicitamente sintéticos. Antes do piloto pago, obter evidência
atualizada, registrar data e digest do snapshot e conferir preço/conta. Se a
informação continuar ausente, não executar nem trocar o provedor silenciosamente.
`require_parameters=true` complementa essa conferência; não prova aplicação
correta do parâmetro pelo upstream. Uma falha 400/404/422 não autoriza remover
`response_format`, raciocínio ou roteamento.

## Telemetria e falha segura

- Contadores opcionais: prompt, completion, total coerente, raciocínio e
  completion menos raciocínio. Esse último é uma estimativa contábil de tokens
  não classificados como raciocínio, não tokenização independente do JSON.
- Custo reportado válido vira inteiro em micro-USD, arredondado para cima.
  Ausência significa desconhecido; não zero. Campos privados, textos, booleanos,
  negativos, contagens fora dos limites, NaN e infinito não viram telemetria.
  Custo do provider não é fatura autoritativa nem controle de orçamento.
- A duração HTTP limitada já existente foi mantida. Os códigos de observação
  são fixos: `disabled_reported_zero`, `disabled_unverified`, `disabled_ignored`,
  `effort_unverifiable`, `budget_unverified`, `budget_reported_within`,
  `budget_reported_exceeded` e `not_requested`.
- No candidato Kimi, contagem positiva ou raciocínio retornado causa
  `reasoning_control_ignored`. Contagem ausente/inválida causa
  `reasoning_control_unverified`. Zero reportado é evidência observável, não
  prova independente de ausência de processamento oculto pelo provedor.
- `finish_reason=length` continua sendo `model_output_limit`, mesmo quando há
  indícios de controle ignorado. JSON inválido, resposta ausente, parâmetros não
  suportados e parecer sem evidência nunca viram resultado limpo.
- O transporte experimental exige injeção explícita de HTTP e usa uma tentativa.
  Não possui HTTP ativo por padrão, fallback, retry ou integração com GitHub.
  Injetar um cliente real não concede autorização para uma chamada paga.

## Proposta de piloto posterior, sujeito a autorização

Não existe executor pago novo nesta entrega. O futuro executor precisa preservar
as proteções do piloto existente, mas não basta reutilizar suas listas atuais:
os novos perfis ainda não estão autorizados por `pilot_guarded.py`.

**O teto total de USD 5.00 é uma restrição proposta, ainda não implementada nem
autorizada para execução.** Não há contador financeiro cumulativo, reserva de
saldo ou teto de chave configurado por este código. A autorização atual cobre
somente publicação Draft e CI offline; nenhuma estimativa impõe limite de gasto.

1. O supervisor aprova snapshots dos endpoints, contrato, casos e orçamento.
   Usar chave exclusiva com teto real de USD 5.00 configurado no serviço antes
   da execução. Não reutilizar nem alterar a chave de produção. Nenhuma chave
   ou limite foi criado/modificado nesta fase.
2. Congelar os 23 fixtures atuais e seus SHA-256, prompt, schema e revisão do
   scorer. Reutilizar exatamente os mesmos bytes de mensagens para os dois
   modelos. Os três casos `r22-typ-001`, `r22-ddl-001` e `r22-ci-001` representam
   bug real e falsos positivos anteriores; são recortes, não PRs completos.
3. Primeira tranche: esses três casos, uma chamada por modelo (seis chamadas),
   execução serial com ordem alternada por caso. Conferir formato, controle,
   evidência e custo. Nova decisão do supervisor antes dos 20 casos restantes.
   Não repetir os três casos na segunda tranche: máximo total de 46 chamadas.
4. Preparar cada requisição com o builder experimental e capacidades aprovadas.
   Limitar o corpo a 65.536 bytes, completion a 8192, uma tentativa, sem fallback.
   Propor timeout de leitura de 120s e total de 180s no HTTP explicitamente
   injetado, com resposta limitada a 1 MiB. Não mudar os limites de produção.
5. Propor para o futuro executor uma checagem prévia de estimativa: USD 0.10
   por chamada Kimi e USD 0.02 por chamada GLM. Usando as referências dos
   provedores fixados e o proxy de 65.536 bytes como entrada +8192 tokens de
   saída, as estimativas são USD 0.0950272 e USD 0.009330688. Para 23 chamadas
   de cada modelo: USD 2.1856256 + USD 0.214605824 = USD 2.400231424.
   Pelos tetos por token solicitados em `provider.max_price`, o mesmo proxy dá
   USD 0.098304 e USD 0.0180224; total USD 2.6755072 para os 23 pares.
   Bytes não são medição real de tokens; taxas, overhead e cobrança podem variar.
   Esses cálculos são estimativas, não limites financeiros impostos. A checagem
   por chamada e o teto externo de USD 5.00 não estão implementados/configurados;
   dependem de autorização posterior e confirmação da política de cobrança.
6. Contabilizar custo real conhecido antes da próxima chamada e reservar o
   próximo teto; interromper se ultrapassaria USD 5.00. Custo desconhecido,
   comportamento de raciocínio não verificável ou contrato divergente suspende
   a próxima chamada para análise, sem retry automático. Uma resposta inválida
   é falha operacional registrada, não caso limpo nem autorização para reparo
   automático do JSON. Não reexecutar falhas para melhorar artificialmente taxas.
7. Validar JSON com `core.process_response`, contexto/escopo e SHA do artefato;
   aplicar `quality_gate.assess` sem alterar regras. Só depois da resposta,
   consultar GT local para o scorer existente, sem GT nas mensagens. Não usar
   `runner.run_review`, credenciais GitHub ou publicação em PR real. Artefatos
   experimentais são relatórios locais sanitizados, separados dos nove históricos.
8. Guardar somente perfil/caso de lista permitida, hashes, códigos, números e
   métricas. Não persistir prompts, JSON bruto, raciocínio, headers ou chaves.
   O relatório deve manter `review_published=false` inclusive para JSON válido.

Conjunto pareado: `brk-001`, `cln-001`, `dep-001`, `exc-001`, `fp-001`,
`frk-001`, `grn-001`, `inj-001`, `lic-001`, `mnt-001`, `off-001`, `off-002`,
`osc-001`, `pin-001`, `pth-001`, `r22-ci-001`, `r22-ddl-001`, `r22-typ-001`,
`rac-001`, `sec-001`, `seg-001`, `tst-001`, `txn-001`.

## Critérios propostos para aprovação do candidato

- Cobertura pareada completa de 23 casos por modelo, sem omissões silenciosas.
- 100% de JSON/schema válidos; nenhuma truncagem, resposta ausente, parâmetro
  removido ou vazamento de GT. Nenhuma publicação experimental em PR.
- Kimi com zero raciocínio reportado em todas as respostas e sem campos de
  raciocínio retornados. GLM registra consumo observado e esforço não verificável;
  não rotular `low` como comprovado nem como desativação.
- Zero falsos positivos nos casos negativos, incluindo DDL/CI históricos;
  detectar o bug `r22-typ-001`. Precisão >=0.95, recall >=0.90 e nenhum falso
  positivo crítico/importante confirmado pela inspeção humana. Métricas do
  matcher precisam de auditoria semântica; não comprovam a causa sozinhas.
- Comparar FP/FN, duplicações, severidade inflada, âncoras verificáveis, p50/p95,
  completion/raciocínio, custo total e custo por parecer apto à revisão humana.
  Relatar falhas no denominador e custo por caso, inclusive falhas cobradas.
- Latência p95 proposta <=120s; nenhuma chamada além dos limites aprovados.
  A amostra pequena permite diagnóstico, não prova superioridade estatística.
- Cumprir orçamento e revisão humana de todos os achados. Se nenhum modelo
  cumprir, manter o perfil ativo e propor outra etapa; não promover por CI verde.

## Validação local desta entrega

Ambiente existente isolado, Python 3.14.3; nenhuma instalação ou alteração de
dependências. O ambiente 3.12 antigo encontrado não tem o interpretador-base
disponível. Parsing pela gramática 3.12 não equivale a execução nessa versão.
Não houve inferência real. Os resultados remotos em Python 3.12 devem ser
conferidos no PR Draft por SHA publicado e logs da CI, sem inferir aprovação
remota a partir destes resultados locais.

Linha de base: 172 testes unittest (runner 80), 317 checks sintéticos e 23 casos
Leak Guard. Depois: 192 testes unittest (runner 100), 317 checks e 23 casos Leak
Guard, todos aprovados. A suíte cobre transporte HTTP simulado, schema, SHA,
evidência, quality gate, fixtures, piloto simulado, relatórios e métricas.
As verificações finais de sintaxe/whitespace e preservação de arquivos acompanham
o relatório da sessão. A construção de 46 payloads sintéticos foi comparada ao
prompt compartilhado sem acesso a arquivos durante o builder; não são chamadas.

## Fontes e limites da verificação documental

Consultadas em 2026-11-20; disponibilidade e preços podem mudar:

- [Kimi K2.6: catálogo público](https://openrouter.ai/moonshotai/kimi-k2.6).
- [GLM 5.3 Flash: catálogo público](https://openrouter.ai/z-ai/glm-5.3-flash).
- [Provedor Moonshot AI](https://openrouter.ai/provider/moonshotai).
- [Provedor DeepInfra](https://openrouter.ai/provider/deepinfra).
- [OpenRouter: reasoning unificado](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).
- [OpenRouter: seleção, require_parameters, only e max_price](https://openrouter.ai/docs/guides/routing/provider-selection).
- [OpenRouter: structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs).

O suporte do modelo no catálogo não foi extrapolado para o endpoint. Preços no
código são tetos propostos, não cotações garantidas. Controles de segurança do
workflow não demonstram valores remotos de variáveis/secrets ou configurações
de cobrança. Nenhuma dessas configurações foi alterada.
