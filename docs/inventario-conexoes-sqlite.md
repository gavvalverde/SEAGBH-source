# Inventário de acessos SQLite e plano de integração — 04D2B3

Inventário estático da main `26e5eada3a281be6c08d97d4df174a127b388427`, conferida
em 21/11/2026. O PR #54 integrou a barreira cooperativa, e o #61 integrou
a regressão Windows/Linux. O [PR #62](https://github.com/gavvalverde/SEAGBH-source/pull/62),
Draft em `c8b8c86802dcc287f7c318967cdae2003934ea79`, é referência para
o ciclo da conexão, ainda sujeito a revisão. Este documento só propõe a
integração. A restauração continua bloqueada na UI.

## Como o inventário foi verificado

Foram examinados os arquivos rastreados em `src/`, `tools/`, `tests/`, a
entrada de empacotamento e a CI SQLite. A busca por imports, fábricas, métodos
SQLite, SQL, tipos de recursos e threads foi cruzada com uma inspeção AST do
Python de produção e com a leitura dos chamadores, sinais Qt e caminhos de erro.
O código do #62 foi lido diretamente do objeto Git, sem executar ou integrar a
branch. Os links apontam para os arquivos da base acima. Nas tabelas, os números
entre parênteses são linhas da função ou do ponto de chamada naquela base.

Foram localizados **cinco pontos de `sqlite3.connect`** no código da aplicação
e das ferramentas, em quatro arquivos. Uma das aberturas serve a três papéis
distintos: leitura da origem operacional, validação da cópia e leitura de
candidatos à retenção ou pré-validação. O inventário abaixo separa esses papéis.
Os testes abrem bancos sintéticos próprios. Fixtures do AI Reviewer são dados
de teste, fora da árvore de execução operacional.

## Identidade, aberturas e responsabilidade

**Banco operacional** é o `seaghb.db` escolhido por
[`get_db_path()`](../src/core/database.py#L23): em desenvolvimento, a pasta
do fonte da aplicação, normalmente `src/`, e no executável congelado, a
pasta de `sys.executable`. A configuração seleciona SQLite por padrão ou
PostgreSQL. O caminho SQLite legado é derivado da instalação e não passa
pela canonicalização da barreira. Os workers atuais reconstroem `Database`
e leem novamente a configuração. O backup usa `get_db_path()` à parte e recebe
o backend da instância de UI. A integração deve escolher backend e caminho
uma vez por sessão e transmitir a identidade validada, sem compartilhar a
conexão entre threads.

### Aplicação e ferramentas

| Abertura efetiva e finalidade | Identidade e dono | Transação, recursos e erros atuais | Risco e integração proposta |
| --- | --- | --- | --- |
| [`Database.__init__`](../src/core/database.py#L97), connect (145), carga de configuração (106), marcador legado (135–143), PRAGMAs (146–147), cursor (148), DDL (150–151) | Banco operacional, uma conexão por instância, na thread que a criou. `timeout=30`, afinidade padrão do driver. | Leitura e escrita. Ativa FKs e solicita WAL. Cria cursor persistente e usa cursores temporários nos PRAGMAs. DDL/migrações têm commits próprios. A captura de exceção do construtor registra e propaga o erro sem cleanup explícito de conexão/cursor parcial. | Adquirir compartilhado no caminho canônico antes do marcador, connect, PRAGMAs e DDL. Abrir pelo caminho devolvido pela concessão. Tratar toda falha após adquirir, e fechar cursores e conexão na thread dona. `Database.fechar` (830) faz commit seguido de close num único `try`, suprime ambos os erros e não fecha o cursor explicitamente. Evitar persistir no fechamento uma transação que falhou em outro método. |
| [`backup_sqlite.criar_backup`](../src/core/backup_sqlite.py#L354) → `_abrir_leitura` (158/161) no snapshot (394) | Origem existente, caminho absoluto explícito. `_caminho` (129) inspeciona componentes e tipo/nlink, recusa links/reparse, UNC, parent traversal e ADS, e resolve a origem. Dono é a thread do chamador, `_BackupThread.run` na UI. | URI escapada `mode=ro`, `timeout=0.1`, `isolation_level=None`. Sem commit da origem ou alteração explícita de journal mode. `closing` encerra a origem inclusive se abrir o destino, copiar ou executar o PRAGMA falhar. Cancelamento/prazo por callback e erros do serviço por fase. | Leitura de origem viva pode usar WAL/SHM e disputar com manutenção. Adquirir no namespace do operacional antes do connect, manter até a Backup API terminar e a origem fechar. Preservar URI de leitura, inclusive recusa de origem ausente. O lock do catálogo de backups é separado. |
| [`criar_backup`, destino da cópia](../src/core/backup_sqlite.py#L395), `fonte.backup(destino)` (397) e PRAGMA (398) | Arquivo de staging exclusivo em pasta de backup, criado pelo serviço após validar a árvore e recusar origem dentro dela. Mesmo dono que a origem. | A Backup API grava snapshot no destino. Após retorno, exige `journal_mode=DELETE` no destino e consome a resposta do PRAGMA com cursor temporário. `closing` aninhado fecha destino antes da origem. Só depois reabre para validar (401), sincroniza (404) e publica sem sobrescrita por hard link (411). Falhas antes da publicação passam pelo cleanup; falhas posteriores podem ser avisos. | Destino privado dispensado da concessão do operacional. Vincular os dois donos à duração da operação nativa, proibir reentrada no destino no callback, fechar também o cursor do PRAGMA e comprovar fim da cópia antes de publicar ou limpar. |
| [`backup_sqlite._validar`](../src/core/backup_sqlite.py#L165) → `_abrir_leitura` (187) | Staging reaberto (401), catálogo em `_retencao` (288, 303, 317), ou arquivo do chamador de `validar_backup` (223), inclusive via pré-validação. A thread chamadora possui cada abertura. | Inspeciona cabeçalho/auxiliares, lê em `mode=ro`, aplica `query_only` e `trusted_schema=OFF`, checa integridade, FKs, tabelas/colunas/chaves. Sem commit. Fecha conexão via `closing`, fecha explicitamente os cursores de integridade/FK e consome ou descarta os demais temporários. Progress handler distingue prazo/cancelamento de falha SQLite. Hash e assinatura são verificados após fechar. | Pressupõe artefato estável e independente. Retenção usa lock de catálogo e pode remover excedentes após revalidar. Definir propriedade do artefato durante todo o preflight e coordenar com retenção ou exigir cópia privada estável. Se uma API aceitar intencionalmente um banco vivo, tratar esse caminho como operacional protegido; o cabeçalho e o hash não classificam um arquivo como inativo. |
| [`restauracao_sqlite._estrutura`](../src/core/restauracao_sqlite.py#L259) → connect (262), chamada por `validar_candidato_restauracao` (296) após `validar_backup` (311) | Somente candidato, caminho absoluto explícito com inspeção de componentes e assinatura/identidade. `_caminho` (198) inspeciona sem seguir links e retorna o `Path`; `validar_backup` resolve internamente para a sua própria abertura. Chamador síncrono. | Segunda leitura do candidato em URI `mode=ro`, sem commit, com progress handler, PRAGMAs de leitura e enumeração de esquema/tabelas. Fecha cursores de consulta e conexão. PRAGMAs de configuração usam cursores temporários. A validação distingue prazo de outros erros e checa identidade/hash/auxiliares. | As duas aberturas e leituras de byte têm janelas de alteração entre si. Manter o artefato sob domínio estável durante todas elas; revalidar identidade sob o mesmo contrato. [`inspecionar_destino_restauracao`](../src/core/restauracao_sqlite.py#L361) e `planejar_restauracao` (433) só observam metadados/auxiliares do destino. Devem continuar sem abrir o operacional nessa fase. |
| [`tools/migrar_sqlite_para_postgres.main`](../tools/migrar_sqlite_para_postgres.py#L181), connect (188), cursor (189), `_rows_from_sqlite` (133) | CLI independente, processo/thread principal. `--sqlite-path` pode ser relativo ao CWD ou explícito, default `src/seaghb.db` (75, 167). Verifica existência e abre o caminho original sem validação de identidade, modo leitura ou canonicalização da barreira. | SELECT por tabela, `fetchall`, sem transação de leitura única e sem commit SQLite. Cursor não é fechado explicitamente. Abertura de SQLite, cursor e conexão PostgreSQL ocorrem antes do `try/finally` (200). Falha intermediária deixa recursos fora dele, e falha no primeiro close pode pular o segundo. O contexto PostgreSQL controla carga, DDL, eventual TRUNCATE opcional e commit/rollback do destino. | Usar leitura protegida e validar/canonicalizar antes da abertura. Declarar snapshot consistente com transação de leitura única ou backup estável já validado. Estruturar cleanup progressivo em ordem inversa e definir limite operacional de uma leitura longa. Preservar semântica PostgreSQL e não tocar destinos reais em teste. |

### Chamadores indiretos, threads e encerramento

| Entrada | Acesso e ciclo observado | Integração necessária |
| --- | --- | --- |
| [`main_qt.main`](../src/main_qt.py#L20) → `on_splash_done` (56) → [`MainWindow.__init__`](../src/ui/main_window.py#L93) (106) | A janela constrói a conexão principal depois do splash. `MainWindow` injeta a mesma instância nas abas (251–271) e diálogos. O app retém a janela. Falhas depois de abrir a conexão e antes de a janela concluir não têm cleanup explícito na entrada. | Adquirir antes da abertura real. Tratar indisponibilidade e inicialização interrompida antes de expor a UI ou habilitar workers. Encaminhar fechamento da sessão principal por um único fluxo. |
| [`_CarregarDadosThread.run`](../src/ui/tab_dashboard.py#L14) (23–29) | Possui outro `Database`. Consulta inventário, eventos, manutenção e atrasos. Apesar da finalidade leitora, repete o DDL da fábrica. Não há close explícito em sucesso ou falha; erro emite coleções vazias. `_carregar_dados_async` (150), timer (54) e `atualizar` (186) disparam o worker. | Usar a mesma identidade operacional e sessão própria, fechada no `run` antes de sinalizar sucesso. Separar falha/cancelamento de resultado vazio. Impedir novos disparos na saída e esperar término real. |
| [`_AtrasosThread.run`](../src/ui/main_window.py#L56) (65–74) | Outro `Database`, também passa pelo DDL. Tenta fechar só após consulta bem-sucedida e suprime falha de fechamento. Caminhos manual (365) e automático (382, timer em 211) iniciam o worker. | Fechar em todos os caminhos na thread dona e propagar diagnóstico seguro. Encerramento precisa abranger ambos os gatilhos. |
| [`FazerBackupDialog._fazer_backup`](../src/ui/dialogs_utils.py#L242) (251) → `_BackupThread.run` (166–182) | Transfere backend, caminho e pasta à thread, sem entregar `db.conn`. Faz backup na thread e sinaliza progresso. `Event` para cancelamento, diálogo retém o worker até consumir `finished` (264, 331). Recusa PostgreSQL. | Passar identidade da sessão verificada e adquirir somente no serviço dono da abertura. A janela deve aguardar a conclusão real do backup, inclusive no cancelamento e na corrida com a publicação. |
| [`MainWindow.closeEvent`](../src/ui/main_window.py#L655) e [`FazerBackupDialog.eventFilter`](../src/ui/dialogs_utils.py#L304), `closeEvent` (311) e `_backup_finalizado` (331) | Fechamento principal solicita cancelamento do backup e adia o fechamento enquanto ativo; sem backup, chama `db.fechar` e aceita. Não drena dashboard/atrasos. Caminhos de saída também incluem `QApplication.quit` após atualização (651) e término de `app.exec` (main_qt:73–79). | Estado de encerramento idempotente para impedir novos trabalhos, cancelar de forma cooperativa, drenar todos os donos e rejeitar fechamento confirmado se algum recurso estiver incerto. Não aguardar workers segurando mutex de sessão/UI. |

### Cursor operacional e limite das transações

[`_CompatCursor`](../src/core/database.py#L66) guarda o cursor nativo,
retorna o resultado bruto de `execute`/`executemany` e delega atributos,
inclusive o acesso à conexão pelo cursor. Não há consumidor operacional externo
encontrado para esses atributos brutos. `Database` guarda ambos em atributos
públicos. Seus métodos entregam dados materializados ou escalares. Consultas
com `fetchone` podem deixar um statement ativo até reuso ou fechamento do
cursor. Nenhum `blobopen`, `ATTACH` ou BLOB incremental foi localizado em
`src/` e `tools/`. Esse fato não protege uma fábrica que devolve handles
brutos. A futura sessão precisa delimitar os recursos permitidos e fechar o
cursor persistente e os temporários explicitamente.

| Grupo de operações em `Database` | Funções e linhas | Transação e risco |
| --- | --- | --- |
| Inicialização SQLite | [`_criar_tabelas`](../src/core/database.py#L158) e script SQLite (208–257), `_coluna_existe` (260, PRAGMA em 269), `_atualizar_estrutura` (272–284) | Cinco tabelas, commits em 258 e 284. `executescript` no modo legado pode confirmar transação pendente antes do script. As duas colunas são checadas e adicionadas em comandos separados; o tratamento genérico de ALTER oculta falhas. Concessões compartilhadas permitem inicializações paralelas e não serializam DDL. |
| Escritas compostas | [`criar_evento`](../src/core/database.py#L300), [`remover_equipamento`](../src/core/database.py#L520) | BEGIN, várias escritas, commit e rollback em exceção. Preservar atomicidade de negócio, inclusive quando rollback ou close também falharem. |
| Outras escritas | `atualizar_evento` (350), `remover_evento` (358), `concluir_evento` (362), `cadastrar_equipamento` (394), `registrar_retorno` (465), `editar_equipamento` (513), `cadastrar_saida_manutencao` (597), `registrar_retorno_manutencao` (606), `adicionar_equipamento_evento` (619), `remover_equipamento_evento` (628), `salvar_notas` (670), `registrar_auditoria` (819) | Commit por chamada. A remoção de vínculos executa dois DELETEs antes do commit. Sem rollback local uniforme; uma falha pode deixar transação pendente afetando a próxima chamada ou o fechamento. Operação, auditoria e conclusão disparadas em chamadas separadas são commits distintos. |
| Consultas | `listar_eventos` (288), `obter_evento` (337), `listar_equipamentos` (371), `obter_id_por_codigo` (404), `esta_em_manutencao` (423), `equipamentos_do_evento` (434), `pendentes_retorno` (450), `eventos_atrasados` (478), `obter_equipamento` (532), `listar_equipamentos_disponiveis` (539), `verificar_pendencia_retorno` (566), `obter_nome_evento` (575), `obter_nome_equipamento` (580), `listar_manutencao` (587), `obter_status_manutencao` (612), `devolvidos_retorno` (639), `contar_pendentes` (650), `obter_notas` (665), `localizar_equipamento` (688), consultas de relatórios (723–799) e `listar_auditoria` (800) | Reutilizam o cursor do dono, sem commit próprio. Há consultas compostas com chamadas internas. Nenhuma delas é motivo para adquirir e liberar a concessão por comando. |

Os consumidores abaixo usam a sessão da janela na thread de UI. Eles herdam
o caminho, a concessão futura e os limites de commit da tabela acima. Nenhum
deve obter concessão própria, reter cursor ou fechar a sessão compartilhada.
Nas falhas, o método de banco precisa deixar a transação em estado definido
antes de o handler continuar a operar.

| Arquivo | Funções com acesso indireto e pontos de chamada |
| --- | --- |
| [tab_eventos.py](../src/ui/tab_eventos.py#L113) | `atualizar_lista` (115), `_on_editar` (199), `_on_remover` (221), `_on_localizar` (236). Consultas e exclusão de evento. |
| [tab_equipamentos.py](../src/ui/tab_equipamentos.py#L111) | `listar_todos` (113), `_on_filtrar` (118), `_on_cadastrar` (182–183), `_on_consultar` (202, 207), `_on_editar` (223, 230–231), `_on_remover` (262–263). Cadastro, edição, remoção, auditoria e consultas. |
| [tab_manutencao.py](../src/ui/tab_manutencao.py#L90) | `atualizar_lista` (91), `_on_cadastrar_saida` (141–151), `_on_registrar_retorno` (166–172). Manutenção de equipamentos, distinta da barreira do banco. |
| [tab_relatorios.py](../src/ui/tab_relatorios.py#L87) | `_rel_eventos_andamento` (89), `_rel_eventos_detalhados` (137) → `_emitir_relatorios_detalhados` (159–160), `_rel_inventario` (227), `_rel_manutencao` (280). Consultas antes da geração de PDF. |
| [dialogs.py](../src/ui/dialogs.py#L85) | `CriarEventoDialog._adicionar` (104–145) e `_finalizar` (164–168), `RetornoDialog._carregar_listas` (237–240) e `_on_scan` (264–270), `EditarEventoDialog._build_ui` (296), `_recarregar_equips` (361), `_remover_equip` (379–383), `_abrir_notas` (389–392) e `_salvar` (396), `AdicionarEquipEventoDialog._adicionar` (461–492), `HistoricoDialog._carregar` (551) e `DetalhesEventoDialog._build_ui` (612, 656). |
| [dialogs_utils.py](../src/ui/dialogs_utils.py#L27) | `AuditoriaDialog.__init__` (39) consulta a sessão principal. `PermissoesDialog` (111–144) usa o caminho do banco em diagnóstico do filesystem; não abre SQLite. `RestaurarBackupDialog` (64–105) sinaliza indisponibilidade; `_restaurar` não altera arquivos. |

### Operações sobre conexões emprestadas e outras fronteiras

- [`sync_outbox.py`](../src/core/sync_outbox.py#L51) recebe a conexão real
  do chamador. `criar_estrutura_outbox` (51) executa DDL de tabela/índice
  fora da transação de negócio. `registrar_alteracao` (81) e `confirmar_entrega`
  (162) exigem transação ativa. `listar_pendentes` (131) materializa linhas.
  Os cursores de `Connection.execute` são temporários, sem close explícito.
  Nenhuma função abre caminho, confirma, reverte ou fecha a conexão. Não há
  chamador operacional identificado. A futura integração exige empréstimo
  estritamente síncrono na thread dona ou adaptação explícita do contrato de
  tipo, sem permitir fuga da conexão. Concessão cobre toda a transação de
  negócio, evento e commit/rollback. Confirmação continua após ACK validado.
- **Acessos auxiliares.** O marcador `.lock` legado, diagnósticos de permissões,
  hashing, staging, catálogo e empacotamento não são conexões SQLite. O
  [spec](../SEAGBH.spec#L8) referencia banco e Backups locais como dados de
  distribuição. Revisar empacotamento em tarefa própria antes de distribuir
  uma build ou ferramenta de manutenção. `tools/testar_conexao_postgres.py`
  usa PostgreSQL. Licença, download e testes de limpeza de temporários não
  acrescentam caminho de abertura SQLite. O preflight reconhece auxiliares
  do destino por metadados; ele não faz checkpoint.
- **Teste e CI.** `tests/unit/test_backup_sqlite.py` (67, 118, 150),
  `test_backup_ui.py` (61, 184), `test_barreira_sqlite.py` (52, 158),
  `test_restauracao_bloqueada_ui.py` (59), `test_restauracao_sqlite.py` (52,
  299) e `test_sync_outbox.py` (29, 70, 195, 205) têm aberturas sintéticas,
  incluindo leitor/escritor concorrente e cliente não cooperativo. Usam
  diretórios temporários e fechamento explícito ou `closing` no dono da
  fixture. O [workflow SQLite](../.github/workflows/sqlite-barreira-ci.yml)
  executa testes de barreira, backup e preflight. Os testes do #62 cobrem
  passagem normal, recusa antes do connect, abandono e falhas simuladas.
  Eles ainda não demonstram o ciclo de `Database` operacional com seus
  recursos nativos e o encerramento Qt.

## Contrato de segurança a comprovar

1. **Abrir sob concessão.** Usar a barreira existente
   ([`_banco`](../src/core/barreira_sqlite.py#L178), [`_adquirir`](../src/core/barreira_sqlite.py#L407))
   para resolver identidade e adquirir compartilhado antes de marcador, SQLite,
   PRAGMA ou DDL. O lock estável `.seagbh-access-v1.lock` não é o `.lock` antigo
   nem o catálogo. A barreira valida componentes, links, hard links e aliases
   conforme a plataforma, e exige volume fixo no Windows. Usar a mesma identidade
   para todos os participantes da aplicação. Filesystem local controlado e
   permissões apropriadas continuam pré-condições. Erro de caminho/contenção
   deve impedir abertura, sem fallback. O contrato não elimina TOCTOU.
2. **Propriedade da conexão e dos recursos.** O [piloto #62](https://github.com/gavvalverde/SEAGBH-source/blob/c8b8c86802dcc287f7c318967cdae2003934ea79/src/core/conexao_sqlite_protegida.py)
   adquire compartilhado antes do connect, mantém referências fortes a sessões
   abertas ou com erro e fecha SQLite antes da concessão. O contexto retorna a
   conexão nativa. O mutex protege o estado da sessão, sem impedir uso externo
   do handle ou garantir afinidade da thread. A fábrica trata falha de `connect`,
   mas a construção/inscrição da sessão ocorre depois, fora desse tratamento.
   Esses limites precisam ser resolvidos ou testados antes de adotar o piloto.
3. **Fechamento lógico versus quiescência.** `with connection` demarca
   transações, sem fechar a conexão. O CPython 3.12 usa `sqlite3_close_v2`.
   A API nativa pode retornar sucesso e deixar a conexão pendente até finalizar
   statements, BLOBs e backups. O efeito concreto do binding depende também
   da limpeza que ele próprio faz. A implementação consultada fecha BLOBs
   registrados no binding e esvazia o cache de statements antes da chamada
   nativa. Statements ainda referenciados por cursores vivos podem persistir.
   A prova precisa cobrir os recursos permitidos, inclusive retorno prematuro, iterador parcialmente
   consumido, cache de statements e callback de backup interrompido. A saída da
   sessão deve barrar novos usos, drenar operações, decidir rollback ou commit
   na fronteira de negócio, finalizar cursores, BLOBs e backups e só então
   fechar a conexão na thread dona e liberar a concessão. Para o primeiro PR,
   manter conexão e cursor brutos privados e materializar as respostas.
   Recursos adicionais exigem escopo de vida explícito ou recusa antes de usar.
   Um close de estado
   incerto conserva a proteção e produz diagnóstico explícito. GC e retry de
   close de um descritor nativo de identidade incerta não são mecanismos de
   recuperação. [Python 3.12](https://docs.python.org/3.12/library/sqlite3.html#how-to-use-the-connection-context-manager),
   [CPython 3.12](https://github.com/python/cpython/blob/3.12/Modules/_sqlite/connection.c),
   [SQLite close](https://www.sqlite.org/c3ref/close.html) e
   [SQLite Backup API](https://www.sqlite.org/c3ref/backup_finish.html).
4. **Prazos e ordenação.** O prazo monotônico da barreira limita a espera por
   contenção, sem interromper I/O nativo bloqueado. O busy timeout do driver e
   os callbacks do backup são controles distintos. Nunca aguardar upgrade de
   compartilhado para exclusivo segurando o compartilhado. Na integração do
   backup, escolher a ordem concessão da origem → lock do catálogo → SQLite,
   com liberações reversas, e auditar todos os chamadores para evitar inversão.
   Não bloquear o event loop por operações longas nem encerrar worker com
   `terminate()`. Cuidado com leitores longos que retêm snapshots.
5. **WAL e outros processos.** Um leitor `mode=ro` pode depender de WAL/SHM;
   WAL pode conter commits ausentes no arquivo principal. Locks da barreira
   ficam em outro arquivo e somente coordenam clientes cooperativos. Existência,
   ausência ou idade de `-wal`, `-shm`, journal ou lock não prova inatividade.
   Identificar ferramentas externas, versões antigas e serviços que abram o
   mesmo banco. Se não houver como excluir esses clientes por controle
   operacional verificável, a futura manutenção deve recusar substituição.
   Não herdar conexão/locks em processos filhos: iniciar processos com spawn
   ou exec. Os callbacks de fork da barreira têm escopo limitado a descritores
   temporários e às chamadas que executam os hooks. Morte do processo pode
   liberar locks sem indicar um arquivo já recuperado ou uma operação concluída.
   [SQLite WAL](https://www.sqlite.org/wal.html).

## Próximos PRs propostos

Cada PR limita seu escopo e pode ser revisado separadamente. O conjunto só
comprova cobertura operacional quando todos os participantes migrarem e o
fechamento transversal estiver demonstrado.

| PR | Dependências e escopo | Risco, aceite e testes |
| --- | --- | --- |
| **1. Sessão SQLite e `Database`** | Resolver revisão do #62, aprovar posse dos recursos e adaptar o ramo SQLite. Abrir caminho sintético injetável, manter API de domínio e ramo PostgreSQL. Todos os chamadores atuais de `Database` precisam de cleanup mínimo antes de habilitar a nova fábrica. Enquanto os dois workers ainda tiverem falhas de ciclo, manter a integração em testes ou sem ativação operacional. | Sem I/O SQLite quando exclusivo ativo, sem marcador em recusa, sem leak após erro em connect, PRAGMA, cursor, DDL ou inicialização da janela. Testar caminho ausente e já existente, esquema legado, rollback/commit e falhas de cleanup, inclusive falha de commit sem suprimir tentativa de fechar. Verificar retenção enquanto restar recurso e ausência de mudança em PostgreSQL, com dependências simuladas. |
| **2. Workers e encerramento da GUI** | Depende do contrato de sessão. Passar identidade da janela aos workers sem passar conexão. Fechar no dono, coordenar timers, sinais, cancelamento e saída de aplicação. | Com Qt offscreen pausar workers por eventos, iniciar fechamento e confirmar ausência de novas sessões ou sucesso prematuro. Exercitar sucesso, erro e cancelamento em dashboard, atrasos e backup, inclusive disparo tardio e múltiplas saídas. Erro de consulta não vira resultado vazio. |
| **3. Backup, catálogo, preflight e migração** | Depende da política de identidade, prazo e fechamento do PR 1. Separar mudanças de backup/preflight e CLI em dois PRs se o diff crescer. Definir ordem da concessão e do catálogo, modo leitura e escopo de snapshot de migração. | Testar origem viva com commits no WAL, backup íntegro, manutenção recusada enquanto backup ativo, cancelamento antes/depois da publicação e retenção preservando candidato em uso conforme política escolhida. Testar erro ao conectar PostgreSQL com substituto controlado e origem sintética, sem executar TRUNCATE real. Conferir que destino de preflight segue sem abertura. |
| **4. Contenção e falhas em processos reais** | Depende das fábricas que estiverem sob teste. Ampliar matriz existente Python 3.12 Windows/Linux, com cada processo abrindo suas próprias conexões. | Pipes/eventos com prazos confirmam aquisição, consulta ativa, erro, fechamento e morte do dono. Desafiar compartilhados simultâneos, exclusivo nos dois sentidos, aliases suportados/recusados, leitor/statement pendente, BLOB se suportado ou recusa explícita, backup iniciado/cancelado, e crash. Testar tanto lock cooperativo quanto sinais de recursos SQLite remanescentes com casos controlados independentes do lock. Mocks só para injetar falhas precisas, sem substituir a contenção real. |
| **5. Validação transversal** | Depende dos PRs 1–4. Refazer inventário; revisar limites de implantação, caminhos congelados, clientes externos, versões antigas e namespaces. | Comprovar por SHA e plataforma que nenhum caminho operacional mapeado contorna a concessão e que desligamento só confirma quiescência após recursos e workers drenados. Exercitar cliente externo para demonstrar o limite cooperativo. Inventariar skips e versões de SQLite efetivamente testadas. Manter restauração bloqueada até aprovação separada de exclusão real, resgate, diário, recuperação e reabertura. |

**Próximo PR recomendado.** Revisar o #62 com foco em posse de recursos e
falhas entre aquisição e entrega da sessão. Em seguida, iniciar o PR 1 no ramo
SQLite de `Database`, com testes sintéticos da inicialização e do fechamento.
A habilitação operacional espera a correção dos donos de todas as instâncias
alcançadas pela fábrica. O plano não exige mudança de dados reais, migração
automática de diretórios ou modificação do AI Reviewer.

## Ciclo de vida da GUI (04D2B6)

O inventário acima registra o fluxo anterior. A janela agora usa a mesma
fábrica para o banco principal, o dashboard e a checagem de atrasos. Sem
caminho explícito, a fábrica continua sendo `Database()`. Em homologação,
passe `banco_sqlite_protegido` com um caminho absoluto e sintético. Só o
caminho é compartilhado. Cada worker abre, consulta e fecha sua própria
sessão. Resultados bem-sucedidos saem depois do fechamento. A falha de
consulta do dashboard preserva os últimos dados válidos.

O pedido de saída bloqueia novos trabalhos, cancela cooperativamente os
workers e aguarda a conclusão do backup em curso. Os workers permanecem
referenciados até a confirmação de término. A janela fecha o banco principal
na thread da GUI, depois dos workers. Falha de fechamento mantém o dono
vivo e a saída pendente até uma nova tentativa. Se a montagem da interface
falhar e a limpeza também falhar, o ponto de entrada mantém a tentativa
disponível na thread que abriu o banco.

A saída do Qt e o reinício após atualização usam o mesmo fluxo. Um
download pode demorar a reagir ao cancelamento durante I/O bloqueado. O
atualizador é iniciado depois da drenagem. Se falhar ao iniciar nessa
etapa, a GUI informa a falha e conclui o fechamento já confirmado.

O backup fica indisponível no modo protegido. Uma integração futura
precisa adquirir a concessão compartilhada para a identidade canônica do
banco antes de abrir a origem e mantê-la até o fim da Backup API e o
fechamento da conexão de origem. O lock do catálogo protege os artefatos
separadamente. O encerramento cobre os recursos controlados pela GUI.
Processos externos, clientes legados, falhas permanentes de driver e
encerramentos forçados pelo sistema permanecem fora dessa garantia.

## Limites desta auditoria

O inventário é do código rastreado na revisão citada. Não há inspeção de bancos
locais, configurações privadas, plugins externos, executáveis implantados,
processos do usuário ou montagens reais. Não houve teste de substituição,
falha de energia, isolamento administrativo ou compatibilidade de produção.
Verificar referências, Markdown e whitespace é validação documental. Qualquer
resultado da CI de regressão nesta branch valida as suítes existentes nos
runners observados, sem homologar a integração futura ou o Draft #62.
