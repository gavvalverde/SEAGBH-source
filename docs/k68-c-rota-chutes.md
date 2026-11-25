# K68-C: rota Kimi K2.6 no Chutes, sem ativação

A mudança configura somente o v2. O v1 fica com a configuração Baidu que seus
testes offline preservam. O workflow de produção, a variável
`AI_REVIEW_ENABLED`, as políticas de conta e os segredos não são alterados.
Manter o revisor desligado; nem integrar código nem aprovar um PR autoriza
inferência, teste pago ou envio de diffs reais.

## Contrato do payload

```json
{
  "model": "moonshotai/kimi-k2.6",
  "temperature": 0.0,
  "max_tokens": 4096,
  "reasoning": {"enabled": false},
  "provider": {
    "only": ["chutes/int4"],
    "allow_fallbacks": false,
    "require_parameters": true,
    "max_price": {"prompt": 0.42, "completion": 1.80}
  }
}
```

Descoberta e refutação continuam incluindo `response_format.type=json_schema`
e seus schemas distintos com `strict=false`. A validação Draft 7 local e o
requisito de reasoning reportado explicitamente como zero permanecem
obrigatórios. A falta de rota elegível, erro HTTP, conteúdo incompleto ou
resposta incompatível não vira parecer limpo nem dispara retry. Os gates de
cobertura, âncoras, deduplicação e releitura do HEAD permanecem intactos.

## Evidência pública e limites da comprovação

Em 25/11/2026 às 14:25 UTC, o [catálogo do modelo](https://openrouter.ai/api/v1/models/moonshotai/kimi-k2.6/endpoints)
listou uma entrada `chutes/int4` e nenhuma Baidu. O Chutes anunciava
US$0,39/M de entrada e US$1,80/M de saída, contexto de 262.144 tokens e
máximo de 65.535 tokens de conclusão. O limite separado de entrada não foi
fornecido. `temperature`, `max_tokens`, `reasoning` e
`structured_outputs` foram anunciados; o catálogo não listou literalmente
`response_format`. A [documentação de saída estruturada](https://openrouter.ai/docs/guides/features/structured-outputs)
usa `response_format` como campo no fio para essa capacidade. Isso torna o
contrato plausível, não homologado.

A [documentação de roteamento](https://openrouter.ai/docs/guides/routing/provider-selection#targeting-specific-provider-endpoints)
permite fixar variante pelo slug com sufixo em `provider.only`, em vez de
usar o slug amplo `chutes`. Não há outra variante/região Chutes neste
snapshot; o roteador documenta que slugs sem região podem abranger regiões
futuras da mesma variante. Antes de qualquer uso real, reconferir o catálogo
e as restrições aplicáveis à conta. Não há prova de aceitação do schema, de
`reasoning.enabled=false`, do contador de zero reasoning, nem de
elegibilidade na conta. O inspetor de snapshot é offline, não adiciona
consulta na execução nem substitui o filtro remoto.

## Custo de preflight

Mantém-se a estimativa conservadora por bytes serializados, com teto de
US$0,42/M de entrada, mais 4.096 tokens de conclusão por chamada a
US$1,80/M. Reserva-se uma refutação de até 200.000 bytes além das chamadas
de descoberta. O aumento máximo só pelo preço da saída, para nove chamadas,
é US$0,0018432. O limite de US$0,20 não muda: oito descobertas no limite
de 90.000 bytes mais a refutação estimariam US$0,4527552 e seriam bloqueadas
antes da primeira chamada. Na fixture de fronteira, três descobertas
totalizando 205.973 bytes mais a reserva estimam US$0,19999986; acrescentar
um único byte ultrapassa o teto. Isso é uma estimativa local, não uma
garantia de faturamento externo.

## Privacidade e próximo passo

O [diretório do OpenRouter](https://openrouter.ai/providers) informa que
Chutes não treina com o conteúdo e retém prompts. O [feed público ZDR](https://openrouter.ai/api/v1/endpoints/zdr)
não listou `chutes/int4` para este modelo no mesmo horário. A
[política do Chutes](https://chutes.ai/privacy), §§ 4 e 6, declara que não
usa conteúdo para treino, limita a retenção própria com exceções e descreve
retenção possível por operadores de modelos padrão. Os
[termos do Chutes](https://chutes.ai/terms) distinguem modelos padrão de
modelos confidenciais. Não há confirmação pública de prazo contratual de
retenção, operadores/regiões efetivos ou isolamento confidencial para a rota
OpenRouter exata. O catálogo indicava suporte a cache implícito. Tratar
títulos, descrições e patches como conteúdo que pode ser retido externamente.
Autorização, prazo, processamento e tratamento de código-fonte devem ser
resolvidos antes de enviar diffs reais. Nenhum logging ou armazenamento
novo foi acrescentado.

Se aprovado separadamente, testar com uma única chamada, sem retries, usando
um patch inteiramente inventado e o schema de descoberta real, com limite
financeiro definido previamente. Não ativar a revisão nem enviar diffs reais
durante esse teste. Uma resposta compatível só confirma o contrato observado
na amostra, não a precisão do revisor. A issue #68 continua aberta.
