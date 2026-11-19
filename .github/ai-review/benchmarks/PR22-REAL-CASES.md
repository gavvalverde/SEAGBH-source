# Casos de qualidade derivados de PRs reais

Em 2026-11-19, a revisão automatizada do PR #22 produziu 2 achados
equivocados (DDL SQLite fora de transação explícita; CI sem dependências
adicionais) e deixou de apontar a falta de validação de tipos em
`operacao not in _OPERACOES`, identificada pelo Copilot.

As provas foram preservadas em três *casos independentes e recortados*:
- r22-typ-001: bug real de entrada não hashable; esperado 1 achado.
- r22-ddl-001: trecho DDL válido sob conexão sqlite3 padrão; esperado 0.
- r22-ci-001: workflow Python 3.12 e módulo somente stdlib, executado
  com sucesso no GitHub; esperado 0.

Os casos são baseados no código do PR #22, mas **não são reprodução exata
do PR completo**. Contexto e trechos foram isolados para medição sem
contaminação cruzada. As evidências completas continuam disponíveis:
- https://github.com/gavvalverde/SEAGBH-source/pull/22
- https://github.com/gavvalverde/SEAGBH-source/actions/runs/44523352425
- https://github.com/gavvalverde/SEAGBH-source/actions/runs/44532822397
- https://github.com/gavvalverde/SEAGBH-source/pull/22#discussion_r4628723730

**Os testes offline não executam IA.** Apenas confirmam a integridade
dos casos, separam enunciado e gabarito e testam a avaliação de respostas
simuladas. CI verde NÃO comprova que o modelo encontrará o bug ou
evitará falsos positivos.

A regra para comparar configurações de modelos é executar exatamente
o mesmo conjunto de casos, sem revelar ground truth no prompt, e medir
TP/FP/FN, precisão, recall, tempo, saída válida e custo por revisão
publicável. A definição de qualidade depende de múltiplos casos,
incluindo exemplos limpos. As execuções pagas exigem orçamento e
autorização explícita antes de serem iniciadas.

Atenção: `run_bench.py` é um utilitário independente que faz chamadas
pagas e pode manter respostas brutas locais. Não executá-lo
automaticamente no CI e não usá-lo com código sensível antes de
revisar armazenamento e descarte seguro dos resultados.
