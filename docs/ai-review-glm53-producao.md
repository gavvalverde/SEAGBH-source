# GLM-5.3 Flash: candidato local para o AI Reviewer

## Estado e escopo

Preparação offline em 20/11/2026, sobre o commit
`2574903f3378e7f6303c4b85a40752c577ddae39` do PR #55. Na conferência inicial,
os PRs #54 e #55 estavam abertos em Draft; `main` local, `origin/main` e a
referência remota de `main` estavam em
`f1515ecc2886bcf6f2ee3d337af83b40800f258e`. Nenhum deles foi modificado.
Esta implementação local não ativa o modelo remoto e não autoriza inferência,
publicação, merge ou alteração de secrets, orçamento e workflows.

O runner passa a selecionar explicitamente o candidato GLM, mantendo o prompt
compartilhado. O padrão histórico de `prompt_builder.py` continua DeepSeek,
com seus parâmetros anteriores. `experimental_profiles.py` e a proposta
comparativa do PR #55 ficam preservados como referência daquela etapa.

## Payload e transporte

Além de `messages`, produzidas pelo mesmo construtor sem alterar o texto:

```json
{
  "model": "z-ai/glm-5.3-flash",
  "temperature": 0.3,
  "max_tokens": 8192,
  "reasoning": {"effort": "low"},
  "response_format": {"type": "json_object"},
  "provider": {
    "only": ["deepinfra"],
    "allow_fallbacks": false,
    "require_parameters": true,
    "max_price": {"prompt": 0.2, "completion": 0.6}
  }
}
```

`main()` continua construindo `OpenRouter(..., max_attempts=1)`: uma tentativa
HTTP por revisão, sem retry, backoff ou substituição de provedor/modelo. O
transporte genérico mantém seus contratos para os outros chamadores; não usar
seu default de tentativas ao homologar este candidato. `provider.only` fixa a
família DeepInfra, não uma região/variante específica do mesmo provedor.

Os preços máximos por milhão de tokens são os do perfil experimental do GLM:
US$ 0,20 de entrada e US$ 0,60 de saída. São filtros de roteamento solicitados,
não um teto financeiro cumulativo. O teto total de US$ 5 da proposta anterior
continua sem implementação, configuração ou autorização de execução.

`low` não desliga o raciocínio e não define um limite rígido para seus tokens.
O teto solicitado de 8.192 pode ser compartilhado entre raciocínio e resposta:
não há reserva garantida de tokens para o JSON visível.

## Compatibilidade: evidência documental e pendências

Consulta somente a páginas públicas, sem chamadas à API OpenRouter ou à API de
inferência DeepInfra. Declaração documental não equivale a homologação remota.

| Controle | Evidência consultada | Limite da confirmação |
| --- | --- | --- |
| Modelo/provedor | [Catálogo OpenRouter](https://openrouter.ai/z-ai/glm-5.3-flash) lista GLM-5.3 Flash e DeepInfra | Não comprova disponibilidade para a conta e os filtros deste payload. |
| `json_object` | [Guia específico da DeepInfra](https://deepinfra.com/blog/glm-5-3-flash-documentation-integration-guide) documenta `response_format` com esse tipo | Confirmado documentalmente no serviço nativo; combinação via OpenRouter pendente. |
| `temperature` | O mesmo guia lista o parâmetro de amostragem | Aceitação e aplicação de `0.3` junto de `low` e JSON mode via OpenRouter pendentes. |
| Esforço `low` | [Anúncio específico da DeepInfra](https://deepinfra.com/blog/glm-5-3-flash-deepinfra) declara `reasoning_effort` com `low`, `high`, `max` | Não comprova tradução e aplicação efetiva do objeto unificado `reasoning` por esse endpoint. |
| Objeto `reasoning` | [Documentação OpenRouter](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens) descreve `reasoning.effort` e o orçamento compartilhado | Suporte varia por modelo/provedor; nenhum snapshot atual de capacidades foi obtido pela API nesta etapa. |
| Obrigatoriedade e roteamento | [Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection) documenta `require_parameters`, `only`, `allow_fallbacks` e `max_price` | São controles declarados pelo gateway; não provam o comportamento interno do modelo. |

Permanece pendente homologar a combinação completa, a telemetria realmente
retornada, o provedor servido, a latência e o espaço disponível para o JSON.
Na ausência dessa evidência, não ativar produção nem remover parâmetros.
Erros 400/404/422, indisponibilidade, preço incompatível ou timeout encerram a
tentativa; não autorizam fallback, retry ou relaxamento dos requisitos.

## Contratos preservados e regressões

- Schema Draft 7, parser estrito, linhas/escopo, evidências, quality gate,
  deduplicação, coleta e revalidação por SHA permanecem no mesmo caminho.
- JSON mode não substitui validação local. `finish_reason=length`, conteúdo
  ausente/inválido/truncado e achado sem evidência nunca viram revisão limpa.
- A telemetria usa somente códigos fixos e números limitados. Ausência de
  contagem de raciocínio ou custo significa desconhecido, não zero.
  `effort_unverifiable` explicita que a contagem não comprova esforço baixo.
  Respostas brutas, raciocínio e erros privados não são escritos nos logs.
- Ground truth continua exclusivamente no avaliador. Não há alteração de cases,
  matcher, métricas, prompt ou política de publicação.

Linha de base reexecutada: 192 testes, 317 checks sintéticos e 23 casos do
Leak Guard, todos aprovados. Após a mudança: 195 testes (103 no runner, incluindo
três regressões novas), os mesmos 317 checks e 23 casos, sem falhas ou skips.
As novas regressões exercitam o `main()` real com clientes simulados, falhas
do transporte/validação sem publicação e telemetria ausente/positiva/inválida.
As expectativas alteradas pertencem ao perfil de produção; os testes históricos
do benchmark continuam exigindo DeepSeek. Nenhum teste foi removido.

Validação com o ambiente isolado já disponível, Python 3.14.3, usando `-B` e
`-W error::ResourceWarning`, com sockets bloqueados no processo de teste.
Executadas todas as onze suítes de `.github/workflows/ai-review-ci.yml`:
core, evidências, quality gate, contratos/fixtures, relatórios/piloto protegido,
métricas, runner/transporte, harness e Leak Guard. Compilação em memória,
gramática Python 3.12 e whitespace também são verificados. Não houve execução
desta alteração em Python 3.12 ou CI remota; parsing não confirma compatibilidade.

## Homologação proposta: uma única chamada, ainda não autorizada

1. Obter autorização separada para uma chamada, modelo/provedor, custo máximo
   financeiro efetivamente imposto externamente e dados sintéticos escolhidos.
   Conferir capacidades atuais da combinação e política de dados da conta.
   Se os parâmetros não estiverem confirmados, parar antes da chamada.
2. Congelar a revisão do código e o digest do payload sintético, sem GT nem
   dados privados. Construir o payload com `build_production_payload()` e usar
   `OpenRouter(..., max_attempts=1)`, fora do workflow e sem cliente GitHub.
3. Executar somente `complete()` uma vez. Timeout ou erro encerra o ensaio,
   inclusive quando o resultado da cobrança for incerto. Não repetir.
4. Validar em memória o resultado com `core.process_response()`, SHA/escopo
   sintéticos e `quality_gate.assess()`. Não chamar `run_review()` com GitHub
   real, não publicar comentários nem registrar resposta/raciocínio brutos.
5. Registrar apenas códigos e números seguros: tentativas, duração, contagens,
   custo reportado e resultados da validação. Conferir separadamente o provedor
   e a cobrança na atividade da conta, sem exportar payloads. `low` continua
   qualitativo mesmo se a chamada passar. Submeter o resultado ao supervisor;
   uma amostra não comprova qualidade geral nem autoriza ativação.

## Rollback planejado

Antes de qualquer publicação, basta retirar este diff local com procedimento
revisado que preserve outros trabalhos; não há estado remoto a reverter nesta
etapa. A referência íntegra é o commit `2574903f3378e7f6303c4b85a40752c577ddae39`.

Se uma ativação futura for autorizada e falhar, suspender novas revisões pelo
procedimento operacional aprovado, sem retries ou fallback automáticos. Em
nova alteração revisada, restaurar desse commit o perfil anterior de
`runner.py` e as expectativas de produção correspondentes: DeepSeek
`deepseek/deepseek-v4.1-flash-1017`, 65.536 tokens de conclusão, pedido de 16.384
tokens de raciocínio, JSON mode e `require_parameters=true`, preservando uma
tentativa e a seleção explícita via `model=PRODUCTION_MODEL`. Reexecutar todas
as regressões e obter autorização antes de publicar
e reativar. Não reverter transporte experimental, prompt, históricos ou outros
commits indiscriminadamente. `reviewer_version()` muda com o código; revisar a
elegibilidade de revisões anteriores, pois a deduplicação já é por SHA e versão.

Riscos residuais: qualidade real ainda não medida, esforço não verificável por
contagem, parâmetros/telemetria sujeitos ao gateway e provedor, possível
truncamento com 8.192 tokens, disponibilidade reduzida sem fallback e ausência
de teto financeiro total implementado. Nenhuma dessas limitações autoriza
ativação automática ou relaxamento da validação.
