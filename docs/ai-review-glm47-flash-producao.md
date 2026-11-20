# Migração AI Reviewer: GLM-4.7 Flash / Cloudflare (sem thinking)

Estado: mudança de configuração preparada sobre a main `43e66b7cbd9fe6b49fad3370d5c19cb9910ccc7f`.
O GLM-5.3 Flash / DeepInfra esgotou 8.192 tokens de completion em
raciocínio, entregou 0 tokens visíveis e falhou com `model_output_limit`
na execução #44783043708. Isso não demonstra incapacidade geral do GLM-5.3,
apenas inviabilidade daquele perfil para esse diff.

## Contrato proposto em produção

- Modelo: `z-ai/glm-4.7-flash`
- Provedor único: `cloudflare` (`only`, `allow_fallbacks=false`)
- Controle: `reasoning: {"enabled": false}`, unificado do OpenRouter
- `temperature=0.3`, `max_tokens=8192`
- `response_format={"type":"json_object"}`, `require_parameters=true`
- `max_price={"prompt":0.2,"completion":0.6}` em USD/milhão de tokens
- `OpenRouter(max_attempts=1, require_reasoning_usage=True)`
- Continua a validação estrita de JSON Draft 7, SHA, escopo, evidências,
  quality gate, deduplicação, mensagens pt-BR e publicação conservadora.
- O benchmark histórico continua com DeepSeek como default.
- `max_price` NÃO impõe teto financeiro acumulado nem controle de fatura.
- Não há fallback para outro modelo/provedor, tentativa repetida,
  relaxamento de JSON ou recuperação tolerante de conteúdo.

## Conformidade e limites

O catálogo OpenRouter lista o GLM-4.7 Flash e os provedores Cloudflare e
NovitaAI. O endpoint nativo da Cloudflare documenta tanto
`chat_template_kwargs.enable_thinking=false` quanto
`response_format.type=json_object`. **Isso não comprova** que a interface
unificada `reasoning.enabled=false` seja integralmente encaminhada pelo
OpenRouter ao endpoint Cloudflare, ou que a resposta reportará
`usage.completion_tokens_details.reasoning_tokens=0`.
A verificação numérica é conservadora: caso o contador falte, seja inválido
ou seja positivo, o runner NÃO publica o parecer. Um contador zero é evidência
reportada, não prova absoluta do funcionamento interno do provedor.
O nome do workflow foi atualizado sem modificar sua lógica de ativação.

## Primeiro ensaio remoto

1. Confirmar o PR e os testes Python 3.12, merge do escopo e código confiável
   no `github.workflow_sha` da `main`.
2. Usar um PR real não Draft com mudança revisável, apenas quando o responsável
   autorizar o teste e eventual custo. É apenas uma tentativa por evento.
3. Conferir status `published` ou diagnóstico, `reasoning_control`, tokens
   prompt/completion/reasoning/visible, duração HTTP e custo reportado.
4. Falha `reasoning_control_unverified` indica ausência de contador e NÃO
   autoriza relaxar a proteção sem novo parecer; `reasoning_control_ignored`
   indica raciocínio observado apesar do opt-out.
5. Se vier JSON válido, verificar a qualidade causal dos achados manualmente.
   Um parecer vazio válido não comprova qualidade para encontrar bugs.
6. Não repetir chamada para ajustar taxa de sucesso; não marcar PR como mergeável
   por causa apenas de um comentário do modelo.

## Qualidade e rollback

O GLM-4.7 Flash sem thinking não foi homologado no benchmark interno.
No catálogo OpenRouter, Terminal-Bench Hard registra 3,8% para o modo
não-thinking versus 22,0% para o thinking (benchmark externo, NÃO mede nossa
revisão). Não é defensável afirmar paridade com GLM-5.3/DeepSeek sem avaliar
os 23 casos congelados por precisão, recall, falso positivo, JSON válido,
latência e custo por parecer útil. A escolha prioriza resposta completa sob
limite financeiro modesto.

Para rollback, desabilitar operacionalmente a revisão automática antes de
qualquer alteração de perfil quando possível, e restaurar o perfil GLM-5.3
do commit `43e66b7cbd9fe6b49fad3370d5c19cb9910ccc7f` em novo PR revisado com CI; não reativar automaticamente
modelo que já falhou por `model_output_limit`. Outra opção é avaliar Kimi
em modo Instant de maneira controlada, sem fallback oculto.

Fontes consultadas:
- https://openrouter.ai/z-ai/glm-4.7-flash
- https://developers.cloudflare.com/workers-ai/models/glm-4.7-flash/
- https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
- https://openrouter.ai/docs/guides/routing/provider-selection
