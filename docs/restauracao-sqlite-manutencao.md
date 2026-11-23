# Restauração SQLite: pré-validação e proposta de manutenção — 04D2A

## Estado entregue e contratos observados

src/core/restauracao_sqlite.py somente inspeciona e planeja. O executor
experimental é separado e está descrito na seção 04D2K5. A UI permanece bloqueada pela 04D1.
Os workflows existentes permanecem inalterados. A CI isolada da barreira está
definida em .github/workflows/sqlite-barreira-ci.yml; não integra clientes reais.

Database escreve o marcador banco.lock em modo w, abre SQLite, habilita foreign
keys/WAL e executa DDL e commits. O marcador não estabelece exclusividade.
MainWindow também pode iniciar workers que criam Database. A entrada main_qt
verifica licença e inicia a GUI; não é uma entrada de manutenção isolada.

Database adiciona eventos.local_evento e eventos.notas ao iniciar. Não existe
histórico versionado de esquema que defina compatibilidade. validar_backup
verifica integridade física, FKs, cinco tabelas mínimas e suas colunas/chaves,
independência, tamanho e SHA-256. Isso não garante compatibilidade completa.

O spec PyInstaller inclui banco, Backups e relatórios locais em caminhos
absolutos. Não oferece manutenção isolada; distribuir dados/resgates ou criar
uma ferramenta de manutenção requer revisão de empacotamento em escopo próprio.

## API, estados e limites

| API/tipo | Contrato |
| --- | --- |
| validar_candidato_restauracao | Caminho explícito, arquivo regular independente sem links/reparse points/hard links; reutiliza validar_backup e observa colunas da aplicação. |
| inspecionar_destino_restauracao | lstat/access; não abre o destino, nem mesmo com SQLite em leitura. |
| planejar_restauracao | Consolida diagnóstico e reinspeciona o destino após avaliar o candidato. Não reserva recursos. |
| CandidatoRestauracao | ValidacaoBackup, assinatura de metadados, compatibilidade, colunas ausentes, tabelas adicionais e horário. |
| InspecaoDestino | Existência, auxiliares, indícios de permissão, impedimentos e horário; exclusividade sempre NAO_COMPROVADA. |
| PlanoRestauracao | Estado do candidato, falhas por fase, inspeções, impedimentos, avisos, horário e etapas pendentes. Nunca autoriza substituição. |

Os dataclasses frozen não constituem autorização nem assinatura de consentimento.
O chamador pode construir objetos; nenhum executor deve confiar neles como prova.
Os hashes identificam somente os bytes observados naquela inspeção.

Candidato RECUSADO contém código que diferencia corrupção, FKs inválidas,
esquema não reconhecido, ausência, auxiliares, caminho inseguro e alteração
durante inspeção. Falha estrutural não é apresentada como corrupção física.
NAO_INSPECIONADO indica que falhou a inspeção do destino e o planejamento não
prosseguiu na leitura do candidato.

Candidato VALIDADO indica integridade e reconhecimento mínimo. Sua
compatibilidade pode ser INCOMPATIVEL, quando faltam colunas consumidas pela
aplicação, ou NAO_COMPROVADA, mesmo quando todas estão presentes. As duas migrações
legadas também não são executadas. Tipos, constraints, índices, gatilhos, conteúdo
e regras de negócio ainda exigem contrato completo. Tabelas adicionais são
informadas e preservadas, sem aprovação semântica. user_version não é convertido
em uma numeração fictícia de versão da aplicação.

auditoria.usuario é opcional para o preflight: existe no DDL atual, porém as
operações de auditoria identificadas em Database não a exigem. Sua ausência
isolada não produz INCOMPATIVEL nem migração. As demais colunas obrigatórias
permanecem inalteradas; mesmo com integridade validada, a compatibilidade global
continua NAO_COMPROVADA.

O plano distingue esses estados dos impedimentos do destino e da ausência de
prova de exclusividade. Mesmo sem riscos locais detectados, compatibilidade e
exclusividade continuam sendo impedimentos. Todo plano exige nova validação
antes de qualquer execução. O executor experimental refaz as verificações.

Caminhos relativos, parent traversal, UNC/dispositivos Windows e componentes
com symlinks/reparse points são recusados. A inspeção detecta identidade por
dispositivo/inode, hard links e árvores sobrepostas. Destino não pode estar
na árvore de backups/resgate nem ser ancestral dessas pastas, inclusive quando
ainda não existe. As duas pastas devem ser disjuntas nos dois sentidos.
destino_na_arvore_de_backups e banco_na_arvore_de_resgate continuam indicando
os casos de banco dentro da respectiva árvore. Os novos impedimentos
backups_na_arvore_do_destino e resgate_na_arvore_do_destino identificam as
pastas abaixo do destino. resgate_e_backups_sobrepostos cobre a sobreposição
entre as duas pastas nos dois sentidos, inclusive igualdade. Ancestrais que já
sejam arquivos regulares são recusados pelo validador de caminhos existente.
A futura pasta de resgate pode não existir; não é criada nem testada por escrita.

-wal, -shm, -journal, .lock e lock do catálogo são sinais conservadores de risco,
inclusive quando residuais. Nenhum auxiliar é apagado ou interpretado como prova
de processo vivo. lstat detecta também links quebrados; falha de leitura de
metadados produz NAO_VERIFICADA, nunca AUSENTE. Não há checkpoint ou mudança de
journal mode.

O destino não é aberto por SQLite porque uma conexão pode interagir com journal
e WAL/SHM. Uma conexão de leitura não prova encerramento das demais conexões.
O candidato deve ser um artefato fechado/estável em diretórios controlados,
conforme o contrato de validar_backup. Só esse artefato é aberto com mode=ro,
query_only e trusted_schema desabilitado. immutable=1 não é usado para fingir
estabilidade de um banco vivo.

Há comparação de hashes antes/depois e de identidade/tamanho/tempos. No Windows,
lstat/fstat podem expor semânticas diferentes para ctime: a checagem cruzada usa
os demais campos e ctime é comparado entre consultas da mesma API. Há testes
para a diferença e para alterações do handle.

Essas verificações não eliminam TOCTOU nem protegem contra um atacante
concorrente que controle as árvores. Não provam estabilidade contínua ou
exclusividade do candidato. os.access fornece apenas indícios sujeitos a ACLs
e corridas; não garante rename/replace ou permissões futuras. Leituras podem
atualizar atime. O prazo é cooperativo, sem interromper syscalls de disco
bloqueadas. Não há garantia uniforme para filesystems de rede.

Se o progress handler da inspeção estrutural interromper SQLite por expiração,
o resultado conserva o código prazo_excedido. A identificação depende do sinal
do próprio callback; outro erro SQLite permanece inspecao_falhou, sem divulgar
SQL ou mensagem bruta. Conexões e cursores são fechados nos dois caminhos.

## 04D2B1 — barreira cooperativa isolada, sem integração operacional

src/core/barreira_sqlite.py implementa coordenação de participantes cooperativos.
Não importa SQLite, Database, Qt, licença ou configuração, não acompanha conexões
e não cria executor. Database, workers, ferramentas e UI não foram alterados.
Consequentemente, a aplicação atual ainda não participa dessa barreira.

### Entradas e ciclos de vida auditados

| Entrada observada | Ciclo de vida atual e integração necessária |
| --- | --- |
| Database.__init__, src/core/database.py | get_db_path deriva o caminho da instalação/src; escreve marcador .lock, conecta SQLite, habilita FKs/WAL e executa DDL/migrações. Adquirir concessão antes do marcador e da conexão, mantendo-a até fechar a conexão. O construtor também carrega ambiente; não executá-lo para inspeção. |
| MainWindow.__init__, src/ui/main_window.py | Cria Database principal; abas e diálogos recebem essa instância. Cada instância adicional do aplicativo precisa participar do mesmo namespace canônico. |
| _CarregarDadosThread.run, src/ui/tab_dashboard.py | Cria Database próprio na thread; não há fechamento explícito. Instalar try/finally/context manager no worker, fechar conexão e só então liberar sua concessão. Falhas não devem virar dados vazios. |
| _AtrasosThread.run, src/ui/main_window.py | Cria Database próprio; tenta fechar somente após consulta bem-sucedida e oculta falha de fechamento. Corrigir o ciclo de vida em etapa autorizada, incluindo exceções de consulta. |
| MainWindow.closeEvent e Database.fechar | O fechamento da janela aguarda o backup em andamento, mas não demonstra encerramento de todos os workers. fechar faz commit antes de close e captura exceções; um commit que falha pode impedir o close. Não liberar concessão enquanto a conexão possa estar aberta. |
| main_qt.main | Verifica licença e inicia splash/janela; Database nasce na MainWindow. Uma pré-checagem temporária na entrada não substitui a concessão por conexão. A manutenção deve ter entrada própria, sem GUI, Firebase ou licença. |
| criar_backup/_abrir_leitura e _BackupThread | O serviço abre a origem operacional e fecha suas conexões com closing; staging é arquivo separado. A futura integração deve manter concessão operacional na origem durante toda a conexão, mesmo se a conexão principal da UI existir. O lock do catálogo de backups resolve outro problema e não substitui esta barreira. |
| validar_backup e restauracao_sqlite._estrutura | Abrem somente artefatos candidatos independentes, não o destino. Preservar esse contrato; a barreira operacional não transforma um arquivo arbitrário em artefato estável. |
| sync_outbox | Recebe conexão do chamador; não abre conexões. A concessão deve pertencer à fábrica/dono da conexão durante toda a transação, sem alterar a semântica de commit/ACK da outbox. |
| tools/migrar_sqlite_para_postgres.main | Abre SQLite diretamente antes de conectar PostgreSQL e antes do try/finally principal. Falha no connect PostgreSQL pode deixar a conexão SQLite fora desse finally. A ferramenta deverá usar a concessão da origem e garantir fechamento em todas as falhas; não executar migração nesta missão. |

A busca estática em src/ e tools/ encontrou sqlite3.connect em Database, serviço
de backup, pré-validação e ferramenta de migração. MainWindow e os dois workers
são os chamadores operacionais de Database identificados. Demais abas/diálogos
recebem a instância. Essa análise não descobre programas externos ou plugins
dinâmicos e não comprova ausência de outros clientes no computador.

### Protocolo e namespace estável

O banco é um caminho absoluto explícito. Validadores recusam parent traversal,
UNC, symlinks/reparse points, arquivos não regulares e hard links. No Windows,
nomes de dispositivos, ADS e componentes ambíguos também são recusados; volumes
precisam ser reportados como DRIVE_FIXED antes das consultas lstat. Resolve-se o
caminho canônico após validar os componentes, revalidando-o sem aceitar links.
O banco pode ainda não existir, mas seu diretório precisa existir.

Na revisão do PR #54, nomes reservados são comparados com os.path.normcase,
tanto na entrada validada quanto após resolver/revalidar o caminho canônico.
A comparação ignora caixa no Windows e mantém a distinção de caixa no Linux;
não reescreve o caminho usado para abrir o recurso. Variantes do sufixo reservado
no Windows são recusadas pelas três APIs sem criar outra coordenação ou alterar
o arquivo existente. Há teste real de aliases no Windows e teste específico de
nomes distintos no Linux.

Para banco.db, o nome reservado é banco.db.seagbh-access-v1.lock no mesmo
diretório. Todos os clientes devem usar a mesma derivação e versão do protocolo.
O arquivo de coordenação é separado do .db, WAL, SHM, journal e marcador legado.
Pode ser criado vazio; nunca é truncado, escrito ou removido pelo módulo. Sua
existência/conteúdo não indica processo vivo. Não apagar/recriar após crash.

Cada aquisição abre um recurso independente e não herdável por exec, confere
arquivo regular, único hard link e identidade caminho/handle antes e depois do
lock. Uso operacional mantém lock compartilhado; manutenção mantém exclusivo.
Múltiplos operacionais coexistem. Exclusivo só é obtido sem participantes ativos
e, uma vez obtido, recusa novas concessões compartilhadas e exclusivas.

| Plataforma | Implementação e limites |
| --- | --- |
| Windows | ctypes da biblioteca padrão chama CreateFileW/LockFileEx/UnlockFileEx. Handle síncrono, não herdável, compartilhamento READ/WRITE sem DELETE e abertura do reparse point final. Um byte é bloqueado, mesmo além do EOF; não exige marcador. FAIL_IMMEDIATELY e EXCLUSIVE_LOCK distinguem contenção de sucesso. Somente ERROR_LOCK_VIOLATION é retentável. Sem FILE_SHARE_DELETE, a remoção/renomeação do arquivo aberto foi recusada no ensaio local. A DACL é herdada do diretório; não há alegação de ACL privada estabelecida pelo módulo. |
| Linux/POSIX | fcntl.flock com LOCK_SH/LOCK_EX e LOCK_NB, O_NOFOLLOW/O_CLOEXEC, criação 0600. A implementação é habilitada para Linux, não presume semântica igual nos demais Unix. flock é advisory e pertence à open file description; unlink/rename por outro processo pode separar namespaces. Diretório controlado e proibição de remover o arquivo são requisitos. A evidência de execução Linux deve vir dos logs do job correspondente, separada da validação local Windows. |

Não há upgrade/downgrade, FIFO ou prioridade de manutenção pendente. Chegadas
contínuas de clientes compartilhados podem causar expiração da manutenção.
Não reutilizar o mesmo handle para aquisições de tipos diferentes. Não duplicar
descritores nem fazer fork com concessões ativas como estratégia operacional.

### APIs, estados, prazos e encerramento

| API | Contrato |
| --- | --- |
| caminho_coordenacao(backend, banco), argumentos nomeados | Somente normaliza/consulta metadados e deriva o nome; não cria arquivo ou diretório. |
| adquirir_uso_operacional(backend, banco, prazo=0) | Adquire compartilhado real, antes de conectar SQLite, retornando Concessao. |
| adquirir_manutencao(backend, banco, prazo=0) | Adquire exclusivo cooperativo real; não inspeciona ou modifica o banco. |
| Concessao.consultar_estado() | Retorna somente ATIVA, LIBERADA, HERDADA ou ERRO_LIBERACAO conhecidos localmente, sem sondar outros processos ou afirmar exclusividade global. |
| Concessao.fechar / with | Libera lock e fecha recurso deterministamente. O chamador precisa fechar todas as suas conexões antes. Fechamento normal é idempotente; reentrada de concessão liberada é recusada. |

prazo deve ser int/float finito não negativo, excluindo bool. A conversão para
float trata overflow antes de consultar caminhos, abrir arquivos ou chamar APIs
nativas; entradas inválidas produzem argumentos/prazo_invalido nas duas APIs.
Inteiros que excedem a representação finita não são aceitos. Zero faz uma tentativa
e retorna ocupado diante de contenção. Prazo positivo usa monotonic, tentativas
nativas não bloqueantes e espera de no máximo 25 ms entre tentativas; ao expirar,
retorna prazo_excedido. O prazo não interrompe syscalls de filesystem bloqueadas.
Não há fallback para PID, flag, WAL ausente ou BEGIN EXCLUSIVE.

ErroBarreira expõe fase/código/tipo da causa e errno/winerror, sem mensagem bruta
ou caminho privado. Acesso negado e erro de sistema não produzem concessão.
Falhas de aquisição fecham o descritor; falhas de unlock também tentam close e
propagam liberacao_falhou. Se close falhar, a liberação não é considerada provada
e não se repete close de um número de fd possivelmente reutilizado. Se o corpo
do with e a liberação falharem, o erro de liberação pode prevalecer.

Concessao mantém o descritor/handle até fechar e não permite cópia/pickle. O
construtor público e um objeto sem emissão são recusados. Isso não é uma
fronteira de segurança dentro de Python: acesso deliberado a atributos privados
pode adulterar objetos. Nenhum executor deve aceitar um objeto fornecido pelo
chamador como prova de exclusividade. ATIVA não é comprovação global nem futura.

Uma concessão abandonada tenta fechar o descritor e emite ResourceWarning;
esse caminho não substitui with/finally. Após término abrupto, o kernel libera
locks dos handles encerrados; no Windows pode haver atraso, que deve continuar
produzindo recusa até a aquisição real. Não apagar o arquivo para forçar sucesso.

No Linux, fork/dup podem manter referências ao mesmo flock e atrasar a liberação
após morte do dono original. Um objeto herdado retorna HERDADA; fechar no filho
apenas fecha sua referência, sem executar LOCK_UN sobre o lock do pai. Concessões
já emitidas não entram no saneamento de temporários descrito a seguir: exigem
fechamento explícito no filho. Não há proteção contra filhos não cooperativos.

### Investigação adicional do PR #54: fork durante aquisição

O teste de concessão já emitida não cobre o fd local de _adquirir entre abrir e
transferir sua propriedade para Concessao. O_CLOEXEC e set_inheritable(False)
impedem herança por exec, mas não fecham a cópia criada por fork. Pela semântica
Linux de flock, o filho pode manter uma referência à mesma open file description,
inclusive se o pai adquirir o lock depois do fork e morrer sem LOCK_UN.

O teste test_fork_durante_aquisicao_nao_prolonga_lock_apos_crash_do_pai usa
um processo pai sintético que abre o fd numa thread, pausada por eventos na
conferência anterior ou posterior ao lock nativo. A outra thread executa fork;
pipes confirmam a identidade herdada e a continuação da aquisição pelo pai.
O teste encerra o pai por SIGKILL, observa a tentativa de manutenção independente,
manda o filho fechar somente seu fd (nunca LOCK_UN), verifica a liberação real
e confirma o encerramento do filho por EOF. Há watchdogs, sem sleeps arbitrários.
O teste exige ausência de herança residual e de lock órfão; não aprova o bug como
comportamento esperado.

O commit diagnóstico 0645d4d76cf358a67d003dc350b921ec0d378bc9 confirmou o defeito
nas duas variantes em Ubuntu 24.04/Python 3.12.16, na
[execução 44767017831](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44767017831):
fd_temporario_herdado=True; apos_SIGKILL=ocupado; apos_fecho_filho=adquirida.
O teste original e suas expectativas foram preservados para validar a correção.

A correção registra descritores temporários e usa os.register_at_fork no Linux.
O callback before adquire o mesmo mutex usado nas transições de propriedade;
after_in_parent o libera. after_in_child substitui o mutex herdado por um novo,
esvazia o registro e fecha somente as cópias temporárias com os.close. Nunca
executa flock/LOCK_UN no filho, nem abre bancos ou altera arquivos/sidecars.

| Transição | Proteção e propriedade |
| --- | --- |
| Abertura e registro | api.abrir e inclusão no conjunto ocorrem sob o mesmo mutex; fork aguarda ambos. Se registrar falhar, o fd é fechado ainda sob essa proteção. |
| Conferências, aquisição nativa e espera | O fd permanece registrado, mas o mutex está livre; fork pode ocorrer antes ou depois do lock e o filho fecha sua cópia. Não se mantém um mutex global durante tentativas ou prazo de contenção. |
| Emissão | Construção de Concessao e retirada do registro são serializadas com fork. Depois dessa fronteira vale o contrato de concessão já emitida. |
| Falha antes da emissão | Retirada do registro e close são serializados com fork. O finally só fecha descritores ainda registrados; não fecha um recurso já transferido. |
| Erro de close | Não repetir close de um número potencialmente reutilizado. O processo registra incerteza e recusa novas aquisições antes de caminhos/APIs nativas. No callback, erros são retidos nesse estado, pois exceções de callbacks não impedem os.fork. Isso não comprova liberação: uma referência não fechada pode persistir até o processo encerrar. |

O teste adicional test_fork_aguarda_abertura_e_registro_atomicos_antes_de_copiar_fds
pausa a abertura nativa antes de retornar ao registro e observa o callback before
aguardar a inclusão. Depois exige as mesmas garantias de ausência de referência
herdada e aquisição independente após SIGKILL do pai, com o filho ainda vivo.
Há também testes de exceções nas fronteiras, erro de registro/close, reutilização
real de número de fd, mutex novo no filho e ciclos sem vazamento de descritores.
O teste anterior de concessão emitida continua exigindo que fechar no filho não
desbloqueie o pai. Os testes de prazo e alias Windows permanecem preservados.

O alcance é os.fork do CPython que executa os callbacks registrados, com uso
cooperativo do módulo. Não cobre fork nativo de extensões que ignore os hooks
PyOS_BeforeFork/PyOS_AfterFork, dup externo, manipulação dos atributos internos,
recarga do módulo durante aquisições ou uso reentrante da barreira/fork dentro
de seus callbacks, handlers de sinais ou finalizadores. Outros callbacks não
podem adquirir a barreira enquanto seu before mantém o mutex. A abertura e o
close nativos ainda podem bloquear por filesystem: o prazo não os interrompe.
O protocolo não torna fork multithread genericamente seguro; CPython 3.12 emite
DeprecationWarning nesse cenário. Preferir spawn/exec para processos da aplicação.

O ambiente local continua Windows/Python 3.14.3, sem WSL/Docker e sem Python 3.12.
Testes de callbacks isolados no Windows não validam fork nativo. A confirmação
da correção nas duas variantes e na fronteira de abertura depende dos novos logs
Linux por SHA, sem reutilizar a execução diagnóstica como aprovação. O PR #54
foi colocado em Draft antes do novo push para evitar revisão paga automática;
as CIs offline existentes continuam elegíveis, sem mudanças nos workflows.

### Evidências locais e limitações

tests/unit/test_barreira_sqlite.py usa processos Popen independentes, pipes JSON
e confirmação de contenção pelo resultado da API real antes de liberar o dono.
Não usa sleeps nos testes para adivinhar a aquisição. Cobre compartilhamento,
exclusão, disputa de manutenção, prazo, liberação normal/exceção/kill, identidade
estável, permissões reais e simuladas, erros nativos e recursos fechados.
Também mantém duas concessões compartilhadas no mesmo processo, fecha uma em
outra thread e confirma que a restante continua recusando manutenção local e
em subprocesso. Fechar novamente a primeira não libera o recurso da segunda.

Os bancos dos testes são temporários, criados sem DDL; sidecars sintéticos são
comparados por hash/mtime. O teste de cliente não cooperativo abre SQLite em
outro processo e consegue ler durante a manutenção: evidencia expressamente
que a barreira não estabelece exclusividade global. O teste de GC captura o
ResourceWarning esperado e confirma que o lock não permanece abandonado.

Execução local inicial: Windows/Python 3.14.3, sem runtime Linux ou Python 3.12.
WSL não está instalado; nenhuma dependência foi instalada. O novo workflow
SQLite Maintenance Barrier Regression usa Ubuntu 24.04 e Windows Server 2022,
Python 3.12 verificado explicitamente e venv sem pip ou dependências adicionais.
Executa barreira, preflight e backup com -B e ResourceWarning como erro, diretório
temporário próprio, prazo de 180s para a suíte e timeout de job de oito minutos.
TimeoutExpired e retorno não zero provocam falha; os testes têm prazos próprios
para respostas e encerramento dos subprocessos. O trigger é pull_request para
main, com filtros de caminhos; a única permissão é contents: read. Checkout e
setup-python estão fixados por SHA, sem credenciais persistidas, LFS ou submódulos.
Não há instalação de pacotes, secrets ou chamadas a serviços da aplicação.
A preparação do runner/Actions não constitui isolamento de rede do SO.
Resultados remotos devem ser verificados por SHA publicado e logs de cada job;
a definição YAML, sozinha, não comprova execução. Relatar skips de symlink e
plataforma separadamente dos testes reais.

A barreira pressupõe filesystem local com locks nativos funcionais e diretório
controlado. UNC/drives remotos identificáveis são recusados, mas não há detector
geral de NFS/SMB/FUSE/mounts remotos no Linux nem certificação do volume Windows.
Aliases, mounts, ACLs, falhas de disco/energia e namespace adulterado exigem
homologação própria. Conferir inodes não elimina TOCTOU; no POSIX a remoção
hostil após a conferência não é impedida por flock. Não há garantia de progresso
ou restauração segura em filesystem de rede.

### Plano transversal e critérios para a próxima etapa

1. Aprovar/homologar o backend nas duas plataformas em Python 3.12 e decidir
   filesystems suportados, ACLs e namespace único do arquivo estável. Se a pasta
   da instalação não permitir sua criação/acesso R/W, falhar sem fallback nem
   migração automática de diretórios.
2. Integrar a aquisição por conexão em Database antes de qualquer marcador,
   connect, PRAGMA ou DDL. Limpar conexão parcial e concessão em falhas de init.
   Não usar cache/flag compartilhada por processo como substituto do lock real.
3. Refatorar fechar com tratamento explícito de commit/rollback/close; liberar
   concessão só quando todas as conexões protegidas estiverem encerradas.
   Não transformar falha de fechamento em sucesso nem liberar antes do close.
4. Aplicar o mesmo ciclo nos workers de dashboard/atrasos e nas conexões da
   origem de backup, com try/finally dentro da thread e sinais de erro seguros.
   Parar timers, impedir novos workers e aguardar os ativos no encerramento,
   sem QThread.terminate como solução de exclusão.
5. Integrar ferramentas que abrem SQLite diretamente, incluindo a migração;
   testes de múltiplas instâncias devem usar processos e falhas em cada fronteira
   entre adquirir, conectar, DDL e fechar. Ferramentas só leitoras também precisam
   participar, mesmo que não tenham uma conexão da UI.
6. Prover entrada independente de manutenção com caminhos explícitos, sem
   main_qt/Database/licença/configuração pessoal. Manter exclusivo durante toda
   a futura operação e recusar novas aberturas cooperativas. Presença de arquivo
   residual após crash não impede aquisição se o kernel realmente a permitir.
7. Antes de permitir substituição, exigir ainda contrato de esquema, resgate
   incluindo WAL, staging validado, diário persistente/barreira de reinício,
   protocolo pós-crash e fronteira de reabertura. Lock liberado por crash não
   prova conclusão de uma recuperação interrompida.
8. Identificar clientes externos não cooperativos. Se não houver isolamento
   administrativo verificável, recusar substituição. Uma concessão exclusiva,
   sozinha, nunca basta como prova contra esses clientes.

Concluir a integração e seus testes exige missão própria. Esta etapa não altera
o resultado de exclusividade do preflight, não habilita restauração e não torna
a ferramenta apta a substituir arquivos de banco.

### 04D2B2 / 04D2B4 — sessão SQLite protegida, piloto isolado

O módulo `src/core/conexao_sqlite_protegida.py` oferece
`abrir_conexao_operacional(banco=..., prazo=...)`. A fábrica constrói e registra
o dono antes de adquirir a concessão compartilhada. Abre SQLite com o caminho
canônico da concessão, timeout de 30 s, transações legadas explícitas na
configuração do driver e cache de statements desativado. Preserva a afinidade
de thread padrão do driver. A sessão recusa chamadas em um PID diferente e
a fábrica não executa PRAGMA ou DDL de inicialização.

O contexto devolve a própria sessão. `executar(sql, parametros=())` executa
uma instrução e devolve uma lista de tuplas, com o cursor fechado antes da
entrega. A sessão oferece `commit()`, `rollback()` e `fechar()`. O
fechamento não faz commit implícito. A sessão não pode ser copiada, serializada
ou usada de outra thread. Operações reentrantes e contextos aninhados são
recusados. A interface não fornece a conexão ou o cursor nativo, iteradores
do driver, BLOB incremental, Backup API ou registro de callbacks externos.
O authorizer recusa `ATTACH` e a abertura interna usada por `VACUUM` e
`VACUUM INTO`. O escopo é SQL de aplicação confiável, com resultados que
caibam em memória. Essa interface não é um sandbox para SQL ou código Python.

A sessão fica referenciada até fechar cursor, conexão e concessão, nessa
ordem. Uma exceção na abertura passa pelo mesmo cleanup. Erros de fechamento
conservam o recurso incerto, invalidam novas operações e deixam a sessão
registrada. Se a sessão foi entregue e a thread dona continua disponível,
ela pode tentar `fechar()` novamente. Uma abertura que falhe também no
cleanup pode reter a concessão sem entregar a sessão. Abandono e término da
thread dona exigem tratamento no ciclo de vida da aplicação. O piloto não
fornece auto-reparo nem sincronização de finalizadores no shutdown do
interpretador. Use encerramento explícito e processos novos por spawn/exec,
sem transportar sessões ou forks com sessões abertas.

O sucesso de `sqlite3_close_v2()` pode representar fechamento lógico com
recursos pendentes. Nos testes, um cursor nativo parcialmente consumido
mantém um checkpoint WAL ocupado mesmo após `Connection.close()`. O teste
do adaptador injeta falha antes de fechar um cursor real e verifica, em
processo independente, que o checkpoint segue ocupado e a concessão impede
manutenção. Após fechar o cursor e antes de liberar a concessão, o checkpoint
volta a funcionar. Os demais testes cobrem falhas injetadas na construção,
no registro, na abertura, na preparação/entrega e no fechamento. Sondas
independentes e eventos verificam contenção, afinidade de thread e liberação.
A matriz offline usa Python 3.12 em Ubuntu e Windows com dados sintéticos.
[Documentação do SQLite sobre fechamento](https://www.sqlite.org/c3ref/close.html).

**Limite de integração.** A conclusão é restrita aos recursos acessíveis
pela interface do piloto. Database, workers, backup, preflight e migração
continuam com ciclos próprios. A integração futura deve provar que todos os
donos fecham suas sessões antes de concluir o encerramento e que o conjunto
de operações necessário cabe na interface sem devolver handles nativos. A
barreira não exclui clientes externos. A restauração segue bloqueada.

A primeira rodada da CI identificou um auxiliar de teste iniciado por
`testar_`, que o `unittest` executava como caso isolado. A correção
renomeou-o para `verificar_ocupado`, adicionou regressão para sessões
abandonadas em GC e confirmou a matriz: 163 casos por plataforma na barreira,
mais 103 casos por plataforma no preflight. A primeira revisão Kimi não
detectou o problema de descoberta. Na repetição, o gate de qualidade
reteve uma resposta por citação curta, sem publicar um parecer inconclusivo.

### 04D2B5 — Database com SQLite protegido em homologação

`Database(banco_sqlite_protegido=caminho_absoluto)` é a seleção explícita
para testes e homologação interna. Nesse modo, a fábrica usa somente o caminho
fornecido e a identidade canônica da concessão. A obtenção do lock compartilhado
antecede o connect, os PRAGMAs e todo o DDL. O modo protegido não cria o marcador
legado `.lock`. `Database()` mantém a seleção de backend e o fluxo anterior,
inclusive para PostgreSQL. GUI e workers ainda chamam `Database()`.

O cursor de compatibilidade do piloto é uma fachada sobre linhas materializadas
e o ID capturado antes de fechar o cursor SQLite. Ele oferece as operações
consumidas pelos métodos de negócio e valida a thread dona também ao entregar
resultados já materializados. O cursor e a conexão nativos ficam dentro da
sessão. BLOB incremental, Backup API e conexões brutas continuam fora do
contrato. As consultas do modo protegido materializam todo o resultado em
memória, inclusive quando o chamador usa somente `fetchone()`.

A inicialização mantém o DDL original e o parser nativo de `executescript`.
Na configuração legada do driver, `executescript` pode confirmar uma transação
pendente antes do script. A API de inicialização dedicada recusa essa situação.
O script atual começa sem transação e preserva a aplicação de cada DDL conforme
o SQLite. Uma falha do script pode deixar tabelas anteriores ao erro criadas.
A migração de `eventos.local_evento` e `eventos.notas` é distinta: no piloto,
usa `BEGIN IMMEDIATE` para serializar a inspeção e aplicar as colunas numa
transação. Uma falha reverte a migração e é propagada. A ativação de FKs e do
WAL é conferida antes de iniciar o esquema.
[Semântica transacional do driver Python 3.12](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Cursor.executescript).

Os métodos de escrita continuam confirmando suas unidades de negócio. No
piloto, falha de execução ou de commit provoca rollback. Se rollback falhar,
a fachada tenta fechar a sessão e deixa o erro encadeado. `Database.fechar()`
no modo protegido delega ao fechamento SQLite, que reverte transações
pendentes. O fechamento não confirma escritas. Um erro de cursor ou conexão
que impeça o fechamento conserva a concessão e recusa novos usos. A thread
dona deve concluir o fechamento antes de terminar. Se ela desaparecer com
sessão aberta, o piloto pode reter a proteção durante o restante do processo.
A futura coordenação da GUI deve tratar esse estado explicitamente.

Os testes com arquivos temporários verificam esquema novo e legado, CRUD,
IDs, reversão de operações compostas, erros nas fronteiras de abertura e
fechamento e contenção real em processos independentes. A regressão PostgreSQL
usa um driver simulado. A mudança prepara a próxima etapa de ciclo de vida
dos workers e da GUI. O comportamento padrão e o bloqueio da restauração
continuam como antes.

## Proposta 04D2B — integração e recuperação operacionais

O [inventário 04D2B3](inventario-conexoes-sqlite.md) detalha os acessos
operacionais, os limites do piloto de conexão do PR #62 e a sequência de
integração até a validação de quiescência.

### Barreira de manutenção e clientes concorrentes

Criar ferramenta separada, com caminhos/backend explícitos, sem importar a
entrada GUI, ler licença ou instanciar Database. Iniciar com a aplicação fechada,
adquirindo um lock estável de manutenção independente dos arquivos substituíveis.

Antes de qualquer substituição, aprovar alteração transversal para que todas as
instâncias, workers e ferramentas participem da mesma barreira antes de abrir
SQLite/DDL, mantendo a proteção durante toda a vida útil das conexões. Um
arquivo sinalizador, flag booleana, lista de PIDs ou lock legado em modo w não
estabelece essa garantia.

A inicialização deve recusar banco com manutenção ou diário de operação pendente.
Não apagar um arquivo de lock em uso e recriá-lo em outro inode: participantes
poderiam passar a bloquear objetos diferentes. Testar a corrida entre duas
instâncias e a retomada após crash.

No POSIX, locks como flock são cooperativos. No Windows, aprovar um protocolo de
handles, compartilhamento e locks equivalente. BEGIN EXCLUSIVE em WAL, sozinho,
não elimina leitores. Clientes externos que ignorem a barreira exigem isolamento
administrativo verificável ou recusa da operação. Sucesso em uma consulta ou
tentativa de lock não prova ausência de todos os acessos.

### Compatibilidade, WAL e resgate

Aprovar contrato real de esquema e política para versões históricas, constraints,
objetos adicionais, conteúdo e regras de negócio. Migração, se necessária, deve
ser projetada separadamente no staging com testes de upgrade/falha/rollback.
Não executar Database no original para descobrir compatibilidade.

Sob exclusividade real, considerar commits no WAL como parte do estado atual.
Não copiar somente o .db nem remover WAL para facilitar a troca. Planejar
snapshot pela SQLite Backup API, com recovery explicitamente autorizado quando
necessário. O serviço de criação existente pode ser avaliado para gerar resgate
com retenção desabilitada em pasta independente da retenção comum.

Validar integridade, esquema, hash e conteúdo esperado do resgate. Sincronizar
arquivo/metadados e registrar resgate durável antes da substituição. Usar
identidade de operação, acesso restrito e retenção própria. Não entregar sua
pasta ao catálogo comum nem excluir o resgate automaticamente após sucesso.

### Preparação e publicação

Revalidar candidato, caminhos, identidades e condições sob proteção aprovada.
O hash do plano é uma referência diagnóstica, sem autorização reaproveitável.
Preparar arquivo privado com nome exclusivo no mesmo filesystem do destino,
validar e sincronizar o substituto antes da publicação.

Após resgate durável, aprovar o procedimento de checkpoint/recovery e fechamento
de todas as conexões do original. Exigir uma coorte SQLite quiescente antes de
separar os arquivos. Não remover WAL antigo isoladamente nem deixá-lo associado
ao banco novo.

No POSIX, rename pode trocar o nome enquanto outro processo mantém o inode
antigo aberto. No Windows, handles e compartilhamento podem impedir a troca.
os.replace substitui um arquivo; não torna .db/-wal/-shm uma transação única.
Trocas entre volumes também não têm essa atomicidade.

Uma opção a homologar é colocar a coorte antiga em quarentena por operação,
registrando cada movimento sob barreira persistente, e só então publicar o
arquivo novo independente. Não implementar essa sequência sem protocolo de
crash testado. Se as garantias não puderem ser estabelecidas, abortar antes de
alterar o original. A política de fsync de diretório no Windows e a durabilidade
do filesystem exigem validação específica; testes de processo não comprovam
proteção contra falha de energia.

### Diário persistente e falhas

O diário deve registrar somente metadados mínimos: operação, caminhos controlados,
identidades/hashes e fases. Não conter linhas do banco, SQL privado ou credenciais.
Persistir intenções antes de cada mudança e conclusões depois. A inicialização
deve falhar de forma segura se houver diário pendente.

| Fase proposta | Recuperação a projetar e testar |
| --- | --- |
| Inspeção/proteção | Sem mutação do original; erro encerra com diagnóstico. |
| Resgate durável | Original preservado; staging incompleto isolado para inspeção. |
| Quarentena parcial | Manter barreira; identificar os arquivos realmente movidos sem adivinhar pelo nome. |
| Novo banco publicado, aplicação bloqueada | Validar identidade, integridade e independência; decidir conclusão ou recuperação assistida pelo resgate/coorte íntegra. |
| Reabertura autorizada | Registrar fronteira persistente que impeça rollback automático; o novo banco pode receber escritas. |
| Falha após possível reabertura | Preservar ambos os estados; nunca repor o antigo sobre dados novos. |

Testar crash em cada fronteira, falha de disco/permissão, publicação parcial,
lock residual, concorrência e reinicialização. A retomada deve reconhecer o
estado efetivo e não repetir cegamente movimentos. Um diário não torna os
três arquivos uma operação atômica.

### CI da barreira e decisões do supervisor

sqlite-barreira-ci.yml cobre a matriz stdlib sem PyQt6, incluindo preflight e
backup. Os filtros cobrem módulo, testes, documento, serviços SQLite relacionados
e o próprio workflow. As demais CIs existentes não foram alteradas.
Symlinks/junctions reais devem ser testados onde houver privilégio; skips são
diferentes de testes de reparse point simulados.

Aprovações necessárias: barreira transversal de inicialização/conexões; clientes
não cooperativos e filesystem suportado; compatibilidade/migrações; pasta, ACL e
retenção do resgate; protocolo de quarentena/publicação/diário; fronteira de
reabertura; recuperação assistida; integração e empacotamento da ferramenta.

Publicar a pré-validação não autoriza essas mudanças e não habilita a UI.

## 04D2K5 — executor restrito a bancos sintéticos

O [executor](../src/core/executor_restauracao_sqlite.py) oferece três operações.
O chamador cria um diretório temporário dedicado com
`criar_ambiente_restauracao_teste()`, prepara os bancos de origem e destino
sintéticos e fornece à função `restaurar_sqlite_teste` os caminhos absolutos
de origem, destino e do novo arquivo de resgate. As três árvores precisam
ser disjuntas, com diretórios já existentes abaixo da raiz criada.
`recuperar_restauracao_teste` recebe o mesmo ambiente, destino e resgate.
Preserve o diretório e seu identificador para retomar um ensaio interrompido.

### Escopo e compatibilidade

- A raiz fica diretamente no diretório temporário do sistema e é verificada por
  uma marca criada junto com o ambiente. Cada arquivo precisa ter a marca
  SQLite `APPLICATION_ID_TESTE`, gravada pelo teste que o criou. Os caminhos
  passam pelos validadores existentes, incluindo recusa de links, aliases,
  volume Windows não fixo e arquivos fora da raiz. O contrato pressupõe
  árvores de teste privadas e estáveis.
- Sob nova concessão exclusiva do destino, o executor revalida origem, destino
  e condições do preflight. A compatibilidade exige integridade física e FKs,
  o `sqlite_schema` exato do DDL SQLite atual, `user_version=0` e as classes
  de valores, datas e estados usados pelo código atual. Isso inclui as
  constraints, chaves e índices implícitos do DDL. A prova é intencionalmente
  estreita. Esquemas históricos, objetos extras e formatações equivalentes do
  DDL ficam fora deste ensaio. O preflight continua consultivo.
- A concessão impede a substituição enquanto um participante cooperativo
  mantém o destino aberto. Origem e artefatos preparados também precisam
  permanecer fechados e estáveis. A execução recusa `-wal`, `-shm`,
  `-journal`, marcadores legados no destino e cabeçalho em WAL sem
  auxiliares. Os arquivos auxiliares são preservados. O ensaio inclui um WAL
  real com commit pendente de checkpoint. [SQLite e arquivos ativos](https://sqlite.org/howtocorrupt.html)

### Ordem de execução e retomada

O resgate é criado exclusivamente, como cópia byte a byte do banco antigo
independente. O executor faz o mesmo para o substituto em arquivo irmão do
destino. Ambos passam por validação e comparação de SHA-256. A intenção registra
a revisão do formato, os hashes antigo e novo e o caminho relativo do resgate.
O diário é publicado antes da troca. Após nova comparação dos bytes e ausência
de auxiliares, o executor publica o substituto com uma única troca de nome,
confirma o hash e encerra o diário. O resgate permanece disponível.

| Resultado | Significado |
| --- | --- |
| `sucesso` | O novo conteúdo foi publicado ou reconhecido na retomada. O resgate foi validado |
| `recusada` | A verificação de caminhos, compatibilidade ou exclusividade impediu a tentativa |
| `falha_recuperavel` | O hash antigo foi confirmado. Caso a intenção já exista, a retomada verifica os artefatos e encerra o diário |
| `intervencao_manual` | O estado final ficou incerto ou uma validação impediu encerrar o diário com segurança |

A retomada adquire sua própria concessão. Com resgate íntegro e diário válido,
ela reconhece o banco antigo ou o novo pelo hash, remove apenas seu substituto
verificado que restou e encerra o diário. A retomada nunca grava o banco
de destino. Se ele estiver ausente, for um terceiro estado ou o resgate divergir,
preserva as evidências. Preparação órfã sem diário exige inspeção manual.
Depois da remoção do diário, uma chamada de retomada sem artefatos retorna
`sem_diario`. Uma falha antes da intenção pode deixar resgate incompleto.
Use `resgate_validado` para distinguir esse caso e um caminho de resgate novo
para outra tentativa. Mantenha outros consumidores do banco de teste fechados
enquanto houver uma operação interrompida.

### Persistência e evidência

- O conteúdo de resgate, substituto e diário passa por `fsync`. No Linux,
  a publicação usa `os.replace` no mesmo diretório e sincroniza os diretórios
  de resgate e destino nos pontos do protocolo. No Windows, a troca usa
  `MoveFileExW` com `REPLACE_EXISTING | WRITE_THROUGH` sem permitir cópia entre
  volumes. A sincronização portável de diretório permanece uma limitação no
  Windows. [Linux fsync](https://man7.org/linux/man-pages/man2/fsync.2.html) · [Windows MoveFileExW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw)
- A suíte usa bancos temporários e falhas injetadas de escrita e sincronização.
  Um subprocesso sai abruptamente imediatamente antes e depois da troca. Isso
  exercita a sobrevivência do diário e a retomada após morte do processo. Falha
  de energia, falha do SO, perda de metadados e comportamento de cache e mídia
  continuam sem validação experimental. Atomicidade do nome e durabilidade são
  propriedades distintas.
- Os testes estão em `tests.unit.test_restauracao_sqlite`, já incluído nas
  matrizes Windows e Linux de Python 3.12 da pré-validação e da barreira.

Para habilitar a restauração na interface, a etapa operacional precisa
coordenar todas as conexões e a inicialização com barreira e diário pendente,
incluindo caminhos legados, workers de backup e ferramentas externas.
Também precisa validar versões históricas e o procedimento com a coorte WAL,
definir permissões e retenção do resgate e verificar recuperação nas plataformas
e filesystems que serão suportados.

## Referências primárias

- [SQLite URI e mode=ro/immutable](https://www.sqlite.org/uri.html).
- [SQLite WAL: sidecars, leitores e checkpoints](https://www.sqlite.org/wal.html).
- [SQLite locking: rollback journal e locks](https://www.sqlite.org/lockingv3.html).
- [Python 3.12: os, permissões e replace](https://docs.python.org/3.12/library/os.html).
- [Windows: LockFileEx, locks compartilhados/exclusivos e encerramento](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex).
- [Windows: CreateFileW e compartilhamento de handles](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew).
- [Windows: UnlockFileEx e intervalo de liberação](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-unlockfileex).
- [Python 3.12: fcntl.flock](https://docs.python.org/3.12/library/fcntl.html).
- [Linux: flock(2), open file descriptions e limitações](https://manpages.debian.org/bookworm/manpages-dev/flock.2.en.html).
- [Linux: fork(2) e descritores herdados](https://manpages.debian.org/bookworm/manpages-dev/fork.2.en.html).
- [Python 3.12: os.register_at_fork](https://docs.python.org/3.12/library/os.html#os.register_at_fork).
