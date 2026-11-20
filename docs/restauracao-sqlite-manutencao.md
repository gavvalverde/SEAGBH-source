# Restauração SQLite: pré-validação e proposta de manutenção — 04D2A

## Estado entregue e contratos observados

src/core/restauracao_sqlite.py somente inspeciona e planeja. Não há executor,
migração, publicação ou limpeza. A UI permanece bloqueada pela 04D1.
Nenhum workflow foi alterado.

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
antes de qualquer execução futura, que não existe nesta entrega.

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

## Proposta 04D2B — dependente de aprovação, não implementada

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

### CI futura e decisões do supervisor

Preferir CI stdlib própria ou ampliar SQLite Backup Offline Regression, com
Python 3.12 em Ubuntu e Windows, venv sem dependências adicionais, -B e
ResourceWarning como erro. Filtros devem cobrir módulo, testes, backup_sqlite,
fixture reutilizada e workflow. A suíte pura não deve depender de PyQt6.
Symlinks/junctions reais devem ser testados onde houver privilégio; skips são
diferentes de testes de reparse point simulados.

Aprovações necessárias: barreira transversal de inicialização/conexões; clientes
não cooperativos e filesystem suportado; compatibilidade/migrações; pasta, ACL e
retenção do resgate; protocolo de quarentena/publicação/diário; fronteira de
reabertura; recuperação assistida; integração e empacotamento da ferramenta.

Publicar a pré-validação não autoriza essas mudanças e não habilita a UI.

## Referências primárias

- [SQLite URI e mode=ro/immutable](https://www.sqlite.org/uri.html).
- [SQLite WAL: sidecars, leitores e checkpoints](https://www.sqlite.org/wal.html).
- [SQLite locking: rollback journal e locks](https://www.sqlite.org/lockingv3.html).
- [Python 3.12: os, permissões e replace](https://docs.python.org/3.12/library/os.html).
