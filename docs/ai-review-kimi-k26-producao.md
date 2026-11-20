# Kimi K2.6 econômico no AI Reviewer (candidato)

## Decisão e escopo

Migrar apenas o perfil de produção do AI Reviewer de GLM-4.7 Flash/Cloudflare para `moonshotai/kimi-k2.6` via OpenRouter. O perfil inicial escolhe **Baidu Qianfan** (`provider.only=["baidu"]`), o menor preço público encontrado para o Kimi K2.6 na consulta de 20/11/2026. Uma única inferência sintética paga, executada para homologação, está documentada abaixo.

Preços publicados na consulta: Baidu Qianfan 0,3895 USD por milhão de tokens de entrada e 1,64 USD por milhão de saída; Chutes 0,39 / 1,80; Moonshot AI 0,95 / 4,00. O preço efetivo depende do momento da chamada e de descontos. Fontes: https://openrouter.ai/moonshotai/kimi-k2.6/providers e https://openrouter.ai/docs/guides/routing/provider-selection .

## Contrato fixado

- `model="moonshotai/kimi-k2.6"`, `temperature=0.3`, `max_tokens=8192`, `reasoning={"enabled": false}`.
- `response_format={"type":"json_object"}`, `provider.only=["baidu"]`, `allow_fallbacks=false`, `require_parameters=true`.
- `provider.max_price={"prompt":0.42,"completion":1.75}` (USD por milhão de tokens), bloqueando rotas mais caras do que esse limite.
- Com a tarifa de saída Baidu observada em 20/11/2026 (US$ 1,64/M), 8.192 tokens de conclusão correspondem a até cerca de US$ 0,0134 de saída, sem incluir entrada, taxas e variações de preço. O dobro do limite anterior não significa o dobro de gasto em toda revisão.
- `max_attempts=1` e `require_reasoning_usage=True` no transporte. Resposta com `reasoning_tokens` ausente, inválido ou positivo, ou contendo raciocínio exposto, **não é publicada**.
- Não adicionar `reasoning.max_tokens` como se fosse um teto comprovado: o OpenRouter documenta esse parâmetro apenas para modelos compatíveis. A desativação foi escolhida como a alternativa econômica e verificável por telemetria.
- A soma de tokens de resposta, inclusive raciocínio quando contado pelo provedor, fica limitada por `max_tokens=8192` solicitado. `finish_reason=length` impede publicação; saída JSON truncada é erro e não parecer limpo. O provedor ainda precisa respeitar a configuração.
- O limite de bytes da requisição, schema Draft 7, evidências, quality gate, escopo por SHA, deduplicação, sanitização, logs sem payloads e política de publicação são preservados.

## Riscos e restrições

O endpoint Baidu/Qianfan via OpenRouter **passou em uma homologação sintética** com a combinação exata de `reasoning.enabled=false`, `response_format=json_object`, `temperature=0.3`, `max_tokens=8192`, roteamento exclusivo e telemetria de `reasoning_tokens=0`. Isso comprova apenas esse teste, não uma garantia para todas as chamadas e diffs; `require_parameters=true` e o validador continuam falhando de modo seguro em erros. O contador zero é a observação do provedor, não prova independente de ausência de processamento oculto.

**Privacidade:** OpenRouter identifica Baidu Qianfan como provedor que retém prompts; PRs podem incluir trechos ainda não publicados ou informações internas. A escolha minimiza preço, não retenção de dados. Consulte https://openrouter.ai/providers antes de autorizar produção.

`provider.max_price` **não é um teto de gasto total por chamada ou por mês**, pois não reserva saldo nem limita automaticamente a quantidade total de chamadas. Usar política de orçamento da conta/chave OpenRouter se for necessário impor teto monetário cumulativo.

O orçamento 8192 mantém um limite conservador com mais espaço para JSON estruturado e achados detalhados. PRs complexos podem exceder esse limite: a revisão falha, não é publicada e não deve fazer retry automático ou remover os controles para contornar o erro. Benchmark de qualidade contra GLM/DeepSeek permanece pendente.

## Homologação real sintética (20/11/2026)

- Execução GitHub Actions [#44787587157](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44787587157), job `smoke`: **success**, uma requisição, sem retry, sem checkout e sem permissão de escrita GitHub.
- Provedor solicitado: somente `baidu`, sem fallback, com `max_price` 0,42/1,75 USD/M. Nenhum dado de PR foi enviado, somente duas mensagens sintéticas.
- Resposta: `model=moonshotai/kimi-k2.6`, JSON analisável e conteúdo sintético correto, `finish_reason=stop`, `completion_tokens=11`, `reasoning_tokens=0`, custo reportado de **US$ 0,000030**.
- Parâmetros do teste iguais ao perfil de produção, incluindo `max_tokens=8192`; um teste curto não demonstra saída de 8.192 tokens nem eficácia de revisão de código.
- O workflow temporário de uma execução foi retirado do PR após a homologação; nenhuma execução repetida é necessária.

## Validação e ativação

1. CI offline Python 3.12 aprovada: 196 testes Python, 317 verificações sintéticas e 23 casos Leak Guard (execução [#44786858679](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44786858679)). Validar novamente após a remoção do workflow temporário e atualização documental.
2. Homologação sintética concluída, **sem publicação de review real e sem benchmark de qualidade**. Proteções de falha, JSON, SHA e custo relativo preservadas.
3. A promoção depende de integração revisada; após ativar, observar uma revisão pequena para medir qualidade, uso de tokens e falsos positivos. O uso do provedor Baidu, que retém prompts, deve permanecer uma decisão consciente.
4. Em erro do endpoint, manter falha explícita. Rollback requer PR revisado para restaurar GLM-4.7 Flash/Cloudflare e seus testes, não fallback silencioso.

Nenhum secret, variável, banco SQLite ou workflow de benchmark pago foi modificado.
