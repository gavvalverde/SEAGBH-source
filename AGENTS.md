# Orientações para agentes — SEAGBH

## Escopo e fonte de verdade

- Este arquivo orienta o trabalho em todo o repositório. Leia também eventuais instruções mais específicas nos subdiretórios.
- Antes de editar, confirme a raiz com `git rev-parse --show-toplevel`, a branch e `git status`. Preserve alterações locais e arquivos não rastreados, especialmente `.github/ai-review/results/`.
- Investigue o checkout atual. Separe código implementado, integração parcial, proposta documental e comportamento remoto não verificado. Não reutilize estados antigos de branches, modelos ou variáveis como fatos atuais.
- Respeite o escopo autorizado. Não altere serviços externos, credenciais, banco privado ou políticas de segurança como efeito colateral de uma investigação.

## Mapa da implementação

| Caminho | Responsabilidade e limites atuais |
| --- | --- |
| `src/main_qt.py` | Entrada PyQt6: verifica licença, abre ativação quando necessário e inicia splash/janela principal. Não executar para uma simples inspeção: pode consultar Firebase e alterar estado local. |
| `src/ui/main_window.py` | Navegação com páginas carregadas sob demanda, menus, timers, workers e atualização. As abas recebem uma instância de `Database`. |
| `src/ui/tab_dashboard.py`, `src/ui/tab_eventos.py`, `src/ui/tab_equipamentos.py`, `src/ui/tab_manutencao.py`, `src/ui/tab_relatorios.py` | Interface e ações de almoxarifado; relatórios usam ReportLab. Há regras de negócio nos handlers existentes: extraia-as gradualmente para funções testáveis, preservando interfaces. |
| `src/ui/dialogs.py`, `src/ui/dialogs_utils.py` | Operações de eventos, retorno, auditoria, backup/restauração e diagnóstico de permissões de escrita. Este diagnóstico não é um sistema de autorização por usuário. |
| `src/ui/login_dialog.py`, `src/ui/licenca_dialog.py` | Login legado sem chamador encontrado em `src/` e ativação de licença integrada à entrada. A existência do diálogo de login não fornece autenticação global. |
| `src/core/database.py` | SQL operacional, DDL e adaptações SQLite/PostgreSQL. SQLite é o padrão; `SEAGBH_DB_ENGINE` seleciona o backend. O construtor carrega configuração de ambiente e cria/atualiza tabelas. |
| `src/core/licenca.py` | Licenciamento legado com HMAC, armazenamento local, verificação REST no Firebase, expiração, renovação e estados de revogação/offline. |
| `tools/gerador_chaves.py` | Ferramenta administrativa PyQt6 de licenças, com autenticação Firebase e operações remotas de leitura/escrita/exclusão. Não executar como teste da aplicação. |
| `src/core/sync_outbox.py` | Fundação SQLite isolada: registro transacional, sequência local, eventos UUID e confirmação de entrega. Ainda não está ligada às escritas operacionais ou a um sincronizador remoto. |
| `src/core/multiativacao_dominio.py` | Tipos e decisões puras sobre empresa, licença, dispositivo e capacidade. Não autentica, persiste, reserva vagas ou concede acesso. |
| `src/core/atualizacao.py`, `src/core/versao.py` | Consulta de versão no Firebase, download, validação opcional de hash e substituição do executável Windows por script. |
| `SEAGBH.spec` | Empacotamento PyInstaller; contém caminhos absolutos e inclui banco, backups e relatórios locais. Exige revisão antes de gerar/distribuir um build. |
| `.github/ai-review/production/` | Transporte, orquestração, schema/escopo, evidência e gate conservador do AI Reviewer. |
| `.github/ai-review/benchmarks/harness/`, `.github/ai-review/benchmarks/cases/`, `.github/ai-review/ground-truth/` | Prompt compartilhado, fixtures, avaliador e experimentos. Ground truth nunca deve entrar no payload do modelo. |

As propostas de sincronização e multiativação estão em `docs/sincronizacao-local-v1.md` e `docs/arquitetura-multiativacao.md`. Elas não comprovam a existência de backend, autenticação multiempresa, RLS ou sincronização em produção. `MIGRACAO_POSTGRES.md` e `tools/migrar_sqlite_para_postgres.py` tratam do caminho legado de PostgreSQL direto, não de sincronização multiempresa.

## Implementação e separação de responsabilidades

- Use Python legível, nomes consistentes com o módulo e tipos nas interfaces em que ajudarem a explicitar o contrato. Prefira mudanças pequenas e reutilize validações existentes.
- Preserve assinaturas, formatos de retorno, sinais Qt, parâmetros SQL e compatibilidade dos dados persistidos. Mude contratos somente com análise dos chamadores e testes de regressão.
- Mantenha widgets, mensagens e apresentação em `src/ui/`; regras puras e integrações em `src/core/`; SQL operacional em `Database`. Não crie novos acessos diretos ao cursor nos handlers. Evite introduzir ORM, framework ou reorganização ampla sem necessidade aprovada.
- Valide tipos, limites e estados na fronteira adequada. Não transforme falhas de rede, persistência ou validação em sucesso, dados vazios ou resultado “limpo”.
- Capture exceções esperadas e preserve o diagnóstico útil. Não acrescente `except Exception: pass` para esconder falhas; reporte erros recuperáveis na interface sem mascarar a operação não concluída.
- Use logging por módulo com dados mínimos. Nunca registre senhas, tokens, chaves de licença, segredos de assinatura, payloads privados ou respostas brutas de serviços. Não reproduza literais sensíveis encontrados no código em saídas de ferramentas ou relatórios.
- Feche conexões, arquivos e respostas de rede nos caminhos de sucesso e de erro. Escritas locais críticas precisam de atomicidade e recuperação testada.

## Componentes críticos

### Banco, transações e recuperação

- Não instancie `Database()` em auditorias ou testes sem redirecionar explicitamente a configuração para um banco temporário. O construtor pode abrir o banco operacional e executar DDL; não leia o `.env` pessoal para descobrir credenciais.
- Preserve SQL parametrizado e a adaptação de placeholders de `_CompatCursor`. Identificadores SQL dinâmicos precisam de lista permitida; não concatene entradas do usuário.
- SQLite utiliza foreign keys e WAL. Não trate o arquivo `.lock` atual como exclusão mútua comprovada. Não compartilhe conexão/cursor entre threads.
- Defina a unidade de transação antes de alterar escritas. Considere rollback, auditoria e, quando integrada, outbox na mesma operação; métodos existentes que fazem commit internamente precisam ser levados em conta.
- Planeje migrações compatíveis com os dois backends realmente suportados. Preserve dados, teste upgrade/falha/rollback e não execute migrações reais ou opções destrutivas sem autorização explícita.
- Os diálogos atuais copiam arquivos SQLite e usam checkpoint WAL; não presuma backup consistente sob concorrência nem compatibilidade com PostgreSQL. Não restaure por cópia sobre conexões abertas. Uma evolução deve testar snapshots e restauração com dados temporários.

### Licença, autenticação e integrações

- Nunca contorne validação de licença, autenticação ou autorização para facilitar testes. Preserve o contrato de `licenca_ativa()` e teste que resultados negativos impeçam acesso, inclusive após ativação pelo diálogo.
- Trate como críticos os limites do legado: tolerância offline de 72 horas, revogação com 24 horas de regularização, exclusão remota, expiração e renovação. Mudanças exigem testes das fronteiras exatas e das falhas de rede/relógio.
- Os testes de licenciamento usam dados temporários em `tests/conftest.py`. Preserve esse isolamento; não abra, restaure ou apague a licença e os backups reais do usuário.
- Há credencial local e material de assinatura embarcados no legado. Não copie, amplie ou tome isso como padrão para funcionalidades novas. Proponha correção e migração ao supervisor, sem expor valores ou substituir unilateralmente o mecanismo.
- UUID de empresa/dispositivo não autentica o chamador. `avaliar_ativacao()` não reserva a última vaga: backend confiável, autorização e reserva atômica continuam necessários.
- A outbox exige transação de negócio aberta, não faz commit e só deve receber confirmação após ACK validado do backend. Preserve ordenação por sequência e identidade estável dos eventos; não prometa entrega exatamente uma vez.
- Não conecte os módulos parciais diretamente à UI como se autorização, isolamento entre empresas, idempotência remota e conflitos já estivessem resolvidos.

### Threads, interface e atualização

- Execute trabalho demorado fora da thread da interface e comunique resultados por sinais/slots PyQt6. Atualize widgets somente na thread da UI.
- Crie e encerre a conexão de banco dentro do worker que a utiliza. Projete cancelamento cooperativo e encerramento dos workers; não use `QThread.terminate()` como padrão para novas rotinas.
- Preserve a distinção entre falha e ausência de registros no dashboard. Não considere timers, sinais ou conexões fechados sem verificar todos os caminhos.
- Não execute o atualizador em verificações locais. A aplicação de atualização é restrita ao executável congelado; preserve essa proteção para não sobrescrever o interpretador.
- Mudanças em URLs, validação do binário, hash, scripts de substituição ou política de versão exigem revisão de segurança. O hash opcional atual não equivale a assinatura/autenticidade obrigatória.

## Dependências, execução e testes

- O alvo declarado nos workflows de regressão é Python 3.12. Confirme o interpretador e as dependências antes de concluir compatibilidade; um resultado em outra versão não valida automaticamente o ambiente CI.
- Imports da aplicação mostram PyQt6, ReportLab e psycopg opcional, mas não há manifesto geral de dependências rastreado. Não invente versões, instale globalmente ou misture o ambiente do reviewer com o da aplicação.
- O reviewer possui requisitos fixados em `.github/ai-review/production/requirements.txt` e `.github/ai-review/production/requirements-test.txt`. Use ambiente isolado e instale dependências apenas quando autorizado.
- Execute os comandos abaixo a partir da raiz, conforme o componente alterado e o escopo da tarefa. São testes locais; não inicie a aplicação contra os dados privados.

```text
python -B -m unittest -v tests.unit.test_sync_outbox
python -B -m unittest -v tests.unit.licenciamento.test_multiativacao_dominio
python -B -m pytest -q -p no:cacheprovider tests/unit/licenciamento
python -B .github/ai-review/production/test_core.py
python -B .github/ai-review/production/test_evidence.py
python -B .github/ai-review/production/test_quality_gate.py
python -B .github/ai-review/production/test_runner.py
python -B .github/ai-review/production/test_planning_v2.py
python -B .github/ai-review/production/test_production_v2.py
python -B .github/ai-review/benchmarks/harness/test_bench.py
python -B .github/ai-review/benchmarks/harness/leak_guard.py
git diff --check
```

- A lista completa de regressões do reviewer, incluindo contratos de fixtures, pilotos simulados e métricas, está em `.github/workflows/ai-review-ci.yml`. Outbox e domínio têm CI própria em `.github/workflows/sync-outbox-ci.yml` e `.github/workflows/multiativacao-ci.yml`.
- `test_bench.py` exercita contratos sintéticos; `run_bench.py` é o orquestrador capaz de fazer chamadas pagas. Não confunda aprovação offline com qualidade real do modelo ou compatibilidade de serviços remotos.
- Alterações funcionais exigem testes proporcionais ao risco: entradas inválidas, fronteiras, rollback, isolamento e falhas reais simuladas. Não use `assert True`, não altere expectativas para ocultar defeitos e informe testes não executados/dependências ausentes.
- Prefira `-B`, compilação em memória e cache de testes desativado para verificações documentais. Sintaxe aprovada não comprova funcionamento.

## AI Reviewer e workflows

- O **Reviewer v2** utiliza Kimi K2.6 (`moonshotai/kimi-k2.6`) via Baidu/OpenRouter, com `reasoning.enabled=false`, até **4.096 tokens de saída por chamada**, oito chunks de descoberta mais uma refutação e teto conservador de **US$0,20 por revisão**. Antes de gastar, valida-se a viabilidade da refutação do diff completo; resposta com `analysis_status=incomplete`, confiança geral baixa, schema/âncora inválido ou orçamento excedido nunca publica parecer limpo. A refutação usa `candidate_id` imutável e um schema separado: `retain`, `refute` ou `uncertain` para cada candidato, com contraprova breve. Exige classificar o comportamento pós-patch (`current_state`) e fornecer `behavior_path`; `retain` só é coerente com regressão alcançável, enquanto correção pelo patch, limitação documentada ou defeito preexistente não devem ser publicados como regressões. Um veredicto contraditório retém a revisão, sem automaticamente provar ou corrigir a causalidade. Candidatos desconhecidos/omitidos/duplicados ou decisão incerta impedem publicação; somente os achados originais podem ser apresentados.
- O runner v1 preservado apenas para regressões offline selecionava Kimi K2.6 (`moonshotai/kimi-k2.6`) via Baidu Qianfan/OpenRouter, com `reasoning.enabled=false`, máximo de 8.192 tokens de conclusão, temperatura 0.0, catálogo de linhas do diff limitado e provedor fixado e filtro de preço 0,42/1,75 USD por milhão de tokens de entrada/saída; exige contador explícito de reasoning zero. Isso não comprova ausência de processamento oculto, compatibilidade do endpoint, nem orçamento financeiro cumulativo. Baidu pode reter prompts. O default DeepSeek de `prompt_builder.py` permanece para contratos históricos do benchmark; o JSON Schema e o catálogo foram integrados no PR #59, com dois reviews publicados nos PRs #16/#28; a auditoria do #28 mostrou dois achados semanticamente não confirmados, apesar das citações corretas. O PR #60 contém um prompt compacto específico do Kimi, testado em duas execuções por caso: reconheceu cinco regiões na fixture #16 e não repetiu os falsos positivos conhecidos do PR #28, reduzindo tokens de entrada. A precisão geral e equivalência ao DeepSeek não estão provadas; CI final e merge humano continuam necessários.
- `.github/workflows/ai-review.yml` usa `pull_request_target` para PRs internos, abertos, não Draft, destinados à `main`, nos eventos declarados e somente na primeira tentativa. Depende de `AI_REVIEW_ENABLED` com valor literal `true`, checagem Bash estrita antes das etapas com credenciais e defesa adicional no runner.
- O checkout deve permanecer no repositório literal e em `github.workflow_sha`, com credenciais não persistidas e submódulos/LFS desativados. Código, requisitos, prompt e schema executados devem vir da revisão confiável, nunca do head/merge do PR.
- Preserve a validação Draft 7 de `core.py`, os limites de escopo, a checagem de SHA, o transporte limitado, a deduplicação e os controles de `evidence.py`/`quality_gate.py`. Citação literal e heurísticas de mecanismo não comprovam a existência de um defeito.
- Os comentários são consultivos: investigue causa, fluxo e impacto antes de aceitar ou corrigir um finding. Erros operacionais e retenção para revisão humana não são review limpa.
- Não altere workflows, variáveis, modelos, raciocínio, limites de tokens ou publicação sem autorização explícita. O YAML não comprova os valores remotos de variáveis, secrets ou políticas.
- `.github/workflows/ai-review-benchmark.yml` é um workflow legado, de execução manual potencialmente paga, e não possui todos os controles de orçamento por chamada existentes no piloto protegido de `.github/workflows/ai-review-pilot.yml`. Não dispare esses workflows, `run_bench.py`, pilotos pagos ou chamadas OpenRouter sem autorização específica. Preserve cases, GT, matcher e métricas fora do escopo autorizado.

## Política de abertura e revisão de Pull Requests

- **Padrão obrigatório:** publicar todo PR novo como **aberto para revisão** (`draft=false`, **Ready for review**) já na criação, após os testes pertinentes, inclusive quando o autor for Codex. Não criar PR em Draft por hábito.
- A abertura deve permitir que **Kimi K2.6 (AI Reviewer)** e **Codex Review** sejam acionados pelos mecanismos existentes do GitHub. Confirmar separadamente o estado e o SHA analisado por cada revisão; um disparo não garante que ambos concluíram.
- Acompanhar os checks de CI e revisões no **HEAD final**. Distinguir `success`, `skipped`, `failure`, revisão pendente, comentários e `quality_manual_review_required`. Revisão retida ou pulada **não equivale a aprovação**.
- Investigar achados reproduzíveis, corrigir na mesma branch quando cabível e conferir as verificações novamente. Não repetir inferências pagas manualmente, alterar modelo, tokens, qualidade ou segredos sem autorização explícita.
- **Não fazer merge automaticamente** apenas porque o PR está Ready ou porque os bots não apontaram defeitos. Respeitar a decisão de integração do responsável e as proteções operacionais do projeto.
- Exceções ao estado Ready exigem pedido explícito do responsável ou um bloqueio real de segurança/publicação; comunicar o motivo sem camuflar a pendência.

## Fluxo de trabalho e entrega

1. Investigue implementação, instruções, estado Git e chamadores; explique o problema e sua causa provável antes de editar código.
2. Apresente solução compatível, riscos e regressões. Em decisões arquiteturais ambíguas ou alterações críticas incompatíveis, apresente alternativas antes de modificar.
3. Quando a tarefa autorizar sincronização, atualize a `main` com segurança; preserve alterações locais e interrompa diante de divergências que exijam decisão. Não use reset, clean, stash ou rebase para descartar trabalho.
4. Crie uma branch específica a partir da base conferida. Não desenvolva diretamente na `main`.
5. Implemente mudanças pequenas, crie/atualize testes relevantes e valide o comportamento com o isolamento necessário.
6. Revise o diff, os arquivos não rastreados, dados sensíveis e escopo antes de preparar o PR. Só faça commit, push, publicação ou operações externas no escopo autorizado pelo usuário.
7. Investigue comentários do AI Reviewer e registre quais são confirmados, prováveis ou não reproduzidos. Considere que tornar um PR elegível pode acionar uma revisão paga conforme a configuração vigente.
8. Entregue relatório com evidências, arquivos alterados, comandos/resultados, limitações e pendências. O merge é decisão humana; não inicie outra missão automaticamente.
