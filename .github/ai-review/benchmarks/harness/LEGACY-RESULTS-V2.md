# Resultados do avaliador legado: versão 2.0.0

A versão se aplica apenas aos novos resultados do benchmark manual
`run_bench.py`. Não altera o piloto protegido, os contratos compartilhados
do parser histórico, a revisão de produção ou resultados já gravados.

- Cada execução tem diretório próprio `results/legacy-v2-<id>/` e um par
  `results.jsonl` / `summary.json`, com a mesma versão e o mesmo `run_id`.
  Não se sobrescreve execução anterior, mesmo para o mesmo modelo e data.
- Cada linha é `evaluated` ou carrega um motivo de não avaliação:
  `timeout`, `transport_error`, `http_error`, `api_error`,
  `invalid_json`, `invalid_schema`, `incomplete` ou `no_ground_truth`.
  Sem gabarito, não inicia a chamada. O status HTTP, quando disponível, fica
  em campo próprio. Não gravamos a resposta bruta do provedor.
- A saída elegível é um objeto JSON único, sem recuperação de trecho de
  prosa ou de texto truncado, com os campos usados pelo avaliador e
  término `stop`. Se a resposta sinaliza `analysis_status`, ela deve
  sinalizar `complete`. Confiança baixa, por si só, não prova truncamento.
  O parser legado compartilhado com o piloto mantém o contrato anterior.
- Casos não avaliáveis têm `match: null`. Uma saída válida e vazia frente
  a um positivo continua sendo FN. O resumo separa `cases_run` (casos
  selecionados), `cases_evaluable`, motivos, positivos selecionados e
  positivos avaliados para distinguir cobertura de qualidade condicional.
  Sem denominador, as taxas são `null` (não aplicável), não zero.
  Estatísticas históricas de calibração/ECE e o placeholder de duplicação
  não são reproduzidos no novo resumo por não medirem o que seus nomes indicam.
- `compare.py` mantém a leitura v1 e aceita v2, mas recusa comparação
  cruzada de versões ou arquivo misto. Não há migração automática.
- O helper multi-match segue exclusivamente as arestas válidas do matcher.
  Maximiza cardinalidade, depois escore total. Para entrada ordenada fixa,
  empates são determinísticos. Isso não comprova causalidade semântica.

Os módulos `test_legacy_evaluator.py` e `test_legacy_matching.py` executam
offline. O teste do orquestrador substitui o subprocesso HTTP; todas as
saídas vão para diretório temporário. Não execute o benchmark manual
para validar esta alteração.
