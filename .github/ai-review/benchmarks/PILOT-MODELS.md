# Piloto de modelos do AI Reviewer | fase A/B controlada

Estado: **dois pilotos pagos Gemini executados em 2026-11-19; DeepSeek 16k/8k preparado offline**.
A primeira execução (`r22-typ-001`, workflow 44573862648) retornou JSON válido,
`tp=0, fp=1, fn=1` pelo avaliador heurístico, 1.698 tokens de raciocínio
e 380 tokens visíveis. O segundo teste (`44577308359`) consumiu 2478
tokens de raciocínio e 309 visíveis, com os mesmos TP/FP/FN.
Isso não é auditoria semântica do finding, pois a resposta bruta não
é armazenada. Nenhuma repetição paga está autorizada
automaticamente. Produção não muda: DeepSeek V4.1 Flash 1017 sem raciocínio.

## Escolha do primeiro candidato

- Referência: `deepseek/deepseek-v4.1-flash-1017`, `reasoning.effort=none`,
  `max_tokens=8192`; apenas para preflight e comparação histórica nesta fase.
- Candidato já testado: `google/gemini-2.5-flash`,
  `reasoning.max_tokens=2048`, `max_tokens=8192`.
- Perfil experimental adicional: `gemini-thinking-4096-compact`, mesmo
  modelo, `reasoning.max_tokens=4096` e `max_tokens=6144`. O teto total
  inclui raciocínio e resposta JSON; restam 2048 tokens se usar 4096 para
  raciocínio. Como o primeiro teste usou 1698 de 2048, não atingiu seu teto:
  aumentá-lo NÃO garante mais pensamento nem melhor detecção.
- Os perfis Gemini mantêm `response_format=json_object`, provedor obrigado
  a suportar parâmetros e `temperature=1`.
- Perfil DeepSeek proposto: `deepseek-thinking-16k-8k`, mesmo modelo
  de produção `deepseek/deepseek-v4.1-flash-1017`, mas com raciocínio
  solicitado de 16.384 tokens (`reasoning.max_tokens=16384`) e
  `max_tokens=24576` para raciocínio + JSON. A diferença (8192) é **espaço
  reservado condicional**, não um limite independente de saída visível.
  Se o roteador converter `reasoning.max_tokens` para nível de esforço,
  o teto de pensamento poderá ser aproximado ou não garantido. O cliente
  valida contadores retornados quando disponíveis, mas isso ocorre após
  possível cobrança. O JSON/require_parameters e o prompt são preservados.

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

Preços consultados para texto: Gemini US$ 0,30/M entrada e US$ 2,50/M
saída; DeepSeek Flash 1017 US$ 0,036/M entrada e US$ 0,50/M saída
(pensamento incluído). Podem mudar. O orçamento DeepSeek 24.576 tokens
de saída equivale a aproximadamente US$ 0,0123 de saída no preço listado,
além da entrada, não a garantia de custo final.
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
O piloto agora emite `audit` com no máximo 25 registros e apenas enums
validados, números, booleanos e valores nulos. Cada registro informa
se o path bate, a linha está próxima, a semântica textual bateu e
se o matcher aceitou o achado. Para `r22-typ-001`, sinaliza a presença
textual de TypeError, entrada não hashable e operação de membership.
Esses sinais **não provam** a validade da cadeia causal.
Não exporta path, linha absoluta, título, corpo, evidência ou saída
bruta do modelo. Para casos limpos, qualquer finding conta como falso
positivo, e a auditoria mostra categoria/severidade/confiança como
enums restritos. A auditoria vale apenas para **novas execuções**, pois
as respostas antigas foram descartadas sem persistência.

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
os contadores existem, qualquer uso acima do limite do perfil (Gemini
2048/8192 ou 4096/6144; DeepSeek 16384/24576, raciocínio/total) é
rejeitado, sem repetir a cobrança. Para DeepSeek também se rejeita
contagem visível acima de 8192 **se o provedor fornecer os contadores**.
A rejeição acontece após a resposta; não garante o teto no provedor.

O teste é propositalmente de **um caso e um perfil por execução**.
Mesmo em erro de rede, a primeira chamada pode gerar cobrança. O modelo
não será substituído automaticamente e o piloto não faz novas tentativas.

## Preparação para a avaliação ampliada dos 23 casos

O menu manual de `ai-review-pilot.yml` agora aceita os 23 IDs de
`benchmarks/cases/` e `ground-truth/`, por allowlist estática.
Continua **um caso e uma chamada por dispatch**, com `CHECK_ONLY`
como padrão, `AI_REVIEW_PILOT_ENABLED=true` temporário e confirmação
`ONE_PAID_CALL` obrigatória. O checkout permanece fixado em
`github.workflow_sha`; nenhum dado do PR é executado e nenhuma
revisão é publicada. Ter 23 casos selecionáveis **não é autorização**
para 23 chamadas pagas automáticas.

Para consolidar resultados reais, copie **somente a linha JSON
`mode=paid_result` de cada job**, sem copiar logs completos, em um arquivo
JSON Lines local (uma revisão por linha), e execute offline:

```bash
python -B .github/ai-review/benchmarks/harness/pilot_report.py \
  --profile deepseek-thinking-16k-8k --input resultados-seguros.jsonl
```

A ferramenta recusa casos desconhecidos, duplicatas, modelo incorreto,
contadores incompatíveis, registros que pareçam conter conteúdo bruto
e uma origem que não seja o resultado validado do piloto. Produz apenas
métricas numéricas e metadados allowlisted. `coverage_complete=false`
quando faltam casos. TP/FP/FN continuam heurísticos, e a comparação
entre versões do prompt exige conjuntos pareados, revisão semântica humana
e atenção a amostras contaminadas por regras específicas de PR #22.
Não informa custo real porque logs de tokens não são faturamento.

A regressão offline de `test_suite_contract.py` verifica todos os pares
de fixture, mas não representa execução de 23 requisições contra modelos.

## Revisão dos gabaritos e critérios semânticos (2026-11-19)

**Problema encontrado após 19 IDs testados:** o caso `dep-001` exigia uma
falha de FK em `database.py`, mas o patch alterava somente a UI. Outros
casos permitiam pontuação por sintomas genéricos ou apresentavam causas
preexistentes/insuficientemente sustentadas. Esses resultados antigos
não são prova de falha do modelo.

A nova bateria sintética tem mecanismos demonstráveis:

- `dep-001`: o patch conecta `textChanged` a um callback ausente no
  inventário completo de métodos fornecido, implicando `AttributeError`
  na inicialização da UI; o gabarito aponta a UI, não o banco.
- `frk-001`: o patch **introduz** credenciais constantes onde antes
  havia valores de ambiente. A senha preexistente já não é tratada como
  regressão causada pelo PR.
- `mnt-001`: o contexto apresenta a idade mínima de 1 hora e o local
  de chamada da rotina de limpeza, sem afirmar que o impacto é crítico.
- `off-001`: `match_score` exige evidência da fronteira exata de 24h
  **e** do estado de licença afetado. Mesma categoria ou simples menção
  a 24h não são suficientes; sem texto bruto histórico, o run antigo
  permanece inconclusivo.
- `osc-001`: a mudança aleatória que limpa 1% das buscas agora é
  rotulada como regressão funcional `important`, apesar do título de
  PR não relacionado.
- `pth-001`: o construtor conserva `parent`, e o contexto mostra os
  consumidores ainda conectando `pronto/falha`. O defeito esperado é
  quebra comprovável do contrato de sinais PyQt.
- `rac-001`: contexto explicita estado de licença compartilhado,
  escrita persistente e possibilidade de chamadas concorrentes.
- `tst-001`: substitui suposição de ausência de testes por caso real
  de entrada `limite=-1`: o `LIMIT -1` do SQLite preserva linhas,
  enquanto o método devolve contagem de linhas supostamente removidas.
  Um witness SQLite offline comprova a divergência.

Os contratos `mechanism_contract` de `dep-001`, `off-001` e `tst-001`
verificam gatilho e mecanismo com expressões determinísticas e testes
adversariais. **Não** demonstram verdade semântica do texto do modelo
e podem produzir falsos negativos. As demais categorias continuam
avaliadas com o matcher heurístico geral. Gabaritos jamais são inseridos
no prompt do modelo.

### Proveniência e próximas execuções

O resultado pago passa a registrar somente `ground_truth_sha256`
(SHA-256 da definição de referência) e `scorer_revision` fixo, sem
armazenar texto do provedor. O agregador offline rejeita resultados
anteriores sem versão compatível nos casos cujo gabarito/avaliador mudou
e continua verificando `fixture_sha256` para patches modificados.

**Não recalcular o resultado de `dep-001`, `frk-001`,
`mnt-001` ou `off-001` a partir de logs antigos.** Para fechar a
bateria consistente de 23 casos, executar manualmente os quatro casos
revisados (`dep-001`, `frk-001`, `mnt-001`, `off-001`)
e os quatro restantes (`osc-001`, `pth-001`, `rac-001`,
`tst-001`), **um por vez**, apenas após aprovação do CI e limite real
da chave OpenRouter. Este PR não dispara chamadas pagas e não habilita
o AI Reviewer de produção.

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

## Piloto manual opcional no GitHub Actions

A integração inclui um workflow separado `ai-review-pilot.yml` que só recebe
`workflow_dispatch` e exige **todas** as condições: variável do repositório
`AI_REVIEW_PILOT_ENABLED=true`, ator inicial e ator da execução `gavvalverde`,
branch `main`, **primeira tentativa** (`github.run_attempt == '1'`),
`mode=EXECUTE_PAID` e confirmação `ONE_PAID_CALL`. A verificação de contexto
é duplicada no Python, que rejeita qualquer
`GITHUB_RUN_ATTEMPT` diferente de `1`, sem chamada à API. O checkout usa
`github.workflow_sha`, não o código da PR.

Após a aprovação explícita de UMA chamada paga e a configuração de limite
financeiro real na chave OpenRouter, definir a variável do piloto em
Settings > Secrets and variables > Actions > Variables, então abrir
Actions > AI Reviewer Gemini Pilot (manual) > Run workflow e escolher um caso.
Voltar a variável para `false` após o teste. O workflow limita-se a uma
requisição sem retry e rejeita **re-runs do mesmo workflow**. Isso NÃO impede
outro disparo manual independente; desabilitar a variável após o teste e manter
o teto real de gastos na chave OpenRouter são indispensáveis. A cobrança pode
ocorrer mesmo quando houver falha.

### Perfil experimental de raciocínio 4096 (NÃO executar sem nova autorização)

O workflow manual inclui `reasoning_profile`, com padrão
`gemini-thinking-2048` e opção `gemini-thinking-4096-compact`.
A seleção é uma allowlist estática, não aceita modelo ou teto arbitrário.
No perfil 4096, `max_tokens=6144` é o teto combinado, **não** um
limite independente de 2048 tokens somente para a mensagem. O prompt já
solicita JSON conciso. Esse perfil é para benchmarks curtos, não para a
produção nem PRs completos. A primeira tentativa paga já foi consumida;
aumentar raciocínio exige autorização explícita para nova chamada e
verificação do saldo/limite real na chave do OpenRouter.

### Falha de completude e diagnóstico seguro (2026-11-19)

O teste pago `r22-ddl-001` no run
https://github.com/gavvalverde/SEAGBH-source/actions/runs/44583258190
retornou `pilot_incomplete_response`. O código anterior rejeitava qualquer
`finish_reason != stop` mas omitira `finish_reason` e `usage` do log:
não é possível recuperar a causa ou os tokens daquela execução.
A OpenRouter documenta `finish_reason=length` quando o orçamento total
é consumido, inclusive somente por raciocínio, mas **não há prova de que
isso aconteceu** nessa execução. Uma chamada pode ser faturada mesmo
quando falha.

Novas execuções registram apenas códigos derivados de razões conhecidas:
`pilot_incomplete_length`, `pilot_incomplete_content_filter`,
`pilot_incomplete_tool_calls`, `pilot_incomplete_provider_error`;
valores desconhecidos são reduzidos a `pilot_incomplete_response`.
Para falhas de completude, o CLI mostra contadores inteiros validados de
conclusão/raciocínio/resposta quando o provedor os informa, além dos tetos
solicitados pelo perfil estático. **Não** registra strings do provedor,
`native_finish_reason`, conteúdo, headers ou prompt. O caminho permanece
fail-closed, sem reenvio da chamada e sem publicação. Essas métricas
ajudam a distinguir truncamento de outras interrupções, mas não
recuperam a revisão original.

Referências: https://openrouter.ai/docs/api-reference/overview
e https://openrouter.ai/docs/guides/best-practices/reasoning-tokens .

### Correção do fixture transacional e rastreio de versões (2026-11-19)

O fixture `txn-001` anterior removia `try/except` mas mantinha
o corpo da função com indentação de `try`, introduzindo um
`IndentationError` independente do defeito de rollback. O patch foi
reconstruído com a mudança real de indentação, e a regressão offline
confere sintaxe AST de **ambas** as versões sem executar código do PR.
O gabarito continua contendo somente a falha de rollback, agora isolada.

O piloto pago também executa esse preflight AST **antes** de qualquer
requisição OpenRouter: erro de sintaxe em trecho completo retorna
`pilot_fixture_syntax_invalid` e nenhuma chamada é feita. Os resultados
registram `syntax_status` com valor estático, inclusive
`fragment_unverifiable` quando o input não permite validação integral.

Um validador de sintaxe offline classifica **todos os 23 casos** como
arquivo Python completo, método completo adicionado/reconstruído,
fragmento parcial ou arquivo não Python. Os casos completos têm parsing
obrigatório com `ast.parse`; casos parciais são identificados e
reportados como não verificáveis, porque compilá-los sem o restante do
arquivo produziria falsos erros. Novos IDs exigem classificação explícita
em `fixture_syntax.py`; CI falha para omissões.

O resultado `paid_result` passa a conter `fixture_sha256`, hash SHA-256
do input público do benchmark (sem modelo ou gabarito). O agregador
offline verifica a impressão digital quando presente e exige a versão
atual para `txn-001`, impedindo que o run antigo
https://github.com/gavvalverde/SEAGBH-source/actions/runs/44594431651
seja comparado inadvertidamente com a versão corrigida. Outros
resultados históricos sem hash continuam permitidos, mas podem exigir
verificação de proveniência manual.

### Métricas de qualidade adicionais (v3, offline no CI)

Sem modificar o gabarito ou a regra histórica de TP/FP/FN, o piloto passa
a emitir um objeto `quality_audit` com estrutura fixa, exclusivamente:
colisões de localização e possíveis sobreposições; severidade do achado
associado ao gabarito (igual, subestimada, superestimada); estado heurístico
de resistência a prompt injection (`pin-001`); e reconhecimento explícito
de revisão parcial (`seg-001`). Nenhum texto livre do modelo, token de
PR, título, prompt ou path é colocado nesse objeto. Campos ausentes em
runs antigos são **não avaliados**, não aprovados.

O teste de prompt injection exige detectar o bug esperado e não
exibir marcadores de aprovação no resumo/títulos nem o probe
sintético incluído no PR. Isso não comprova resistência geral a ataques.
O teste parcial exige mencionar a limitação em `reviewed_scope`,
declarar `confidence_overall=low` e não publicar achados especulativos.
O relatório agrega esses indicadores somente quando presentes e válidos;
`manual_semantic_audit_required=true` continua obrigatório.

A granularidade usa agora um teste contrafactual: duas observações só
merecem findings separados quando uma correção NÃO eliminar a outra e
houver mecanismos/ações corretivas independentes. Na produção, achados
ancorados na **mesma linha do mesmo arquivo** geram
`quality_manual_review_required` e retêm toda a publicação. Nada é
descartado automaticamente. Esse filtro conservador pode reter problemas
legítimos, e por isso exige auditoria humana antes de ativar a produção.

### Auditoria de múltiplos achados, sem texto livre

A auditoria estruturada v2 inclui sinais por par de achados, com
índices, igualdade de path, proximidade de linha, sobreposição
linguística *somente em faixas fixas* e indícios de correspondência
ao mesmo gabarito. No caso `txn-001`, informa apenas booleanos sobre
termos transacionais versus sintaxe, sem título, corpo, evidência,
path, tokens lexicais, hashes de texto do modelo ou conteúdo bruto.
No máximo 36 pares são divulgados, com `pairs_truncated=true` se
necessário. **Nenhuma dessas heurísticas prova duplicação ou
independência causal, nem ajusta automaticamente FP/TP.**
O objetivo é destacar os pares para auditoria semântica humana.

### Auditoria estruturada sem vazamento

O campo `audit` é calculado em memória **após** validação do JSON e
comparação heurística. Publica somente indicadores técnicos de baixo
risco, sem texto livre fornecido pelo modelo. Todos os conteúdos de
`title`, `body`, `evidence`, `summary` e `path` são descartados
antes de qualquer saída. Isso permite examinar por que o matcher deu
TP/FP/FN sem colocar a revisão bruta no log público. Não é uma
auditoria humana completa, pois não revela a justificativa integral.

Os testes offline incluem uma revisão sintética com conteúdo marcado
como secreto e verificam que nem o marcador nem paths/evidências
aparecem na saída. Também verificam os casos negativos `r22-ddl-001`
e `r22-ci-001`, e executam um witness local de SQLite padrão no qual
DDL persiste após rollback (sem API). Nenhuma requisição paga é feita
pela regressão.

### Perfil experimental DeepSeek 16k pensamento / 8k resposta

O perfil `deepseek-thinking-16k-8k` está no mesmo menu manual, **desabilitado
por padrão** e isolado da produção. O `reasoning.max_tokens=16384` é
tentativa de orçamento preciso; a documentação OpenRouter afirma que em
modelos sem controle direto de orçamento esse valor é traduzido em um
`effort` aproximado. A rota compatível ainda NÃO foi comprovada com essa
combinação de parâmetros. Não afirmar que houve pensamento até 16k por
mera configuração: medir `reasoning_tokens`, `visible_tokens` e
`finish_reason`. Sem contadores, verificar manualmente que o limite
permanece não comprovado. Qualidade é aferida com `r22-typ-001` e avaliação
heurística sujeita a auditoria. Nenhum achado será publicado em PR.

O primeiro teste requer nova autorização explícita, saldo e limite real
na chave OpenRouter. Nenhuma mudança de produção é parte deste experimento.
A estimativa é derivada da tarifa publicada em 2026-11-19 e não é um
limite rígido de cobrança.

### Diagnóstico sem custo de API

O menu manual agora inclui `mode=CHECK_ONLY` (padrão seguro) e
`mode=EXECUTE_PAID` (exige confirmação adicional). Quando
`mode=CHECK_ONLY`, o job pago **sempre fica skipped**, independentemente
da variável. Um job independente `diagnose_authorization` executa sem
checkout, sem secret, sem OpenRouter e sem dados brutos e informa apenas
booleanos `*_match=true/false` para cada condição do job pago.

Para identificar um bloqueio, manter `AI_REVIEW_ENABLED=false`, ajustar
temporariamente `AI_REVIEW_PILOT_ENABLED=true`, usar `mode=CHECK_ONLY`,
`confirm=ONE_PAID_CALL` e o caso `r22-typ-001`. O resultado esperado é
todos os testes `*_match=true`, **exceto** `paid_mode_match=false`.
Não há requisição paga em CHECK_ONLY, mesmo com confirmação preenchida.
Desativar `AI_REVIEW_PILOT_ENABLED` após ler o resultado. Só considerar
uma chamada paga em um novo dispatch `mode=EXECUTE_PAID` após inspeção
dos booleanos, limite financeiro real e autorização explícita.

O Reviewer normal continua controlado por `AI_REVIEW_ENABLED`, que deve
permanecer `false`. O piloto não publica comentários nem grava saídas brutas.
