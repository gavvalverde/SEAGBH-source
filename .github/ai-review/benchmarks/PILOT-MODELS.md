# Piloto de modelos do AI Reviewer | fase A/B controlada

Estado: **preparado, não executado contra API paga**. Produção não muda.
O Reviewer principal continua DeepSeek V4.1 Flash 1017, sem raciocínio.

## Escolha do primeiro candidato

- Referência: `deepseek/deepseek-v4.1-flash-1017`, `reasoning.effort=none`,
  `max_tokens=8192`; apenas para preflight e comparação histórica nesta fase.
- Candidato: `google/gemini-2.5-flash`, `reasoning.max_tokens=2048`,
  `max_tokens=8192`, `response_format=json_object`, provedor obrigado a
  suportar parâmetros, `temperature=1`.

O Gemini 2.5 Flash foi escolhido porque a documentação do OpenRouter anuncia
controle direto do orçamento de pensamento, suporte a saída estruturada e
uso com código. **Essa compatibilidade ainda não foi testada na nossa rota.**
Se não houver rota disponível, a requisição deve falhar em vez de trocar
silenciosamente por outro modelo. Em 2026-11-19, a página do modelo listava
Google AI Studio como rota ativa (o encerramento anterior da rota Vertex
não significa necessariamente encerramento de todas as rotas).

Referências:
- https://openrouter.ai/google/gemini-2.5-flash
- https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
- https://ai.google.dev/gemini-api/docs/pricing

Preços consultados para texto: US$ 0,30/M tokens de entrada e
US$ 2,50/M tokens de saída (pensamento incluído). Podem mudar.
O estimador mostra custo aproximado por requisição; **não é um teto real**.
O limite de gasto confiável precisa ser configurado na própria chave
OpenRouter antes de qualquer teste. Não podemos verificar isso pelo GitHub.

## Casos escolhidos

- `r22-typ-001`: defeito verdadeiro que escapou ao DeepSeek.
- `r22-ddl-001`: cenário SQLite válido, não deve gerar alerta.
- `r22-ci-001`: workflow válido, não deve gerar alerta.

Esses três casos não são uma avaliação estatisticamente significativa.
A qualidade final deve ser medida com os 23 casos e exemplos adicionais.
O `evaluate_review` existente faz correspondência textual aproximada;
o resultado TP/FP/FN é um indicador auxiliar, sujeito a auditoria humana.

## Preflight, SEM rede ou chave

```bash
python -B .github/ai-review/benchmarks/harness/pilot_guarded.py \
  --case r22-typ-001 --profile gemini-thinking-2048
```

O programa NÃO faz chamadas pagas por padrão e informa apenas modelo,
perfil, orçamento teórico, bytes de entrada e limite de conclusão.

## Ativação paga: desabilitada por padrão

O programa tem modo pago para uma única chamada, sem retry, sem escrita
de respostas brutas em disco e **bloqueado em CI**. Também não publica
nada no PR. O operador deve antes:

1. Solicitar e receber autorização explícita do dono do projeto para UMA
   chamada paga e definir o valor máximo aprovado (até US$ 0,05).
2. Configurar o limite de gastos **na chave** de API do OpenRouter; o
   cálculo local não garante cobrança máxima e pode subestimar o uso.
3. Verificar que a chave API só existe em ambiente local confiável, e
   que não há observabilidade externa registrando o prompt ou a resposta.
4. Passar `--paid --confirm-single-paid-call --max-usd 0.05`, com a
   variável `SEAGBH_AI_PILOT_PAID_ENABLED=owner_confirmed`.
   Não colocar secrets em argumentos ou commits.

A API pode omitir os contadores de raciocínio. Nesse caso o resultado
aparece com `reasoning_budget_verified=false`; sucesso na geração NÃO
significa que o limite numérico foi comprovadamente respeitado. Quando
os contadores existem, qualquer uso acima de 2048 tokens de pensamento
ou acima dos 8192 tokens totais é rejeitado, sem repetir a cobrança.

O teste é propositalmente de **um caso e um perfil por execução**.
Mesmo em erro de rede, a primeira chamada pode gerar cobrança. O modelo
não será substituído automaticamente e o piloto não faz novas tentativas.

## Critérios de decisão

Só avaliar troca de modelo após executar o mesmo conjunto de casos e
validar manualmente achados e falsos positivos. Registrar externamente,
sem conteúdo sensível: modelo, caso, sucesso de JSON, TP/FP/FN auditados,
tokens usados, duração e cobrança efetiva. O Gemini pode ser superior em
raciocínio e ainda assim perder em precisão de revisão. A escolha final
depende das medições, não da reputação do modelo.

Não executar `run_bench.py` na fase piloto: ferramenta legada tem
tentativas adicionais e persistência de respostas brutas, diferentes das
garantias deste novo runner.
