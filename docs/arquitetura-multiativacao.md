# SEAGBH | Multiativação V1: contratos e isolamento

Status: proposta técnica em implementação; nenhuma mudança no licenciamento
legado nem na produção do Firebase ou do Supabase nesta etapa.

## Identidades independentes

- Empresa: company_id (UUID imutável). É dona dos dados operacionais.
- Licença: license_id (UUID). Refere-se a uma empresa, plano e limite de
  dispositivos. Pode ser encerrada e substituída sem alterar company_id.
- Instalação: device_id (UUID persistente por instalação). Identifica uma
  instalação autorizada; sozinho NÃO comprova a posse do dispositivo.
- Ativação: activation_id (UUID). Relaciona empresa, licença, dispositivo
  e situação (ativa/revogada).
- Chave de ativação: credencial de apresentação inicial; NÃO é chave primária
  de empresa, identificador de banco ou credencial permanente de dados.

Exemplo: a empresa X usa licença L1 em três dispositivos. Ao aumentar o
limite de 3 para 4, um novo dispositivo pode solicitar a quarta ativação
usando a mesma chave vigente. Se a licença L1 terminar e a empresa voltar,
uma licença L2 pode manter o company_id e os dados da empresa.

## Escopo do primeiro PR

O módulo puro src/core/multiativacao_dominio.py oferece:
- Tipos imutáveis: Empresa, Licenca, Ativacao.
- Enumerações de estados e decisões.
- Avaliação determinística de uma vaga, incluindo reuso da mesma instalação,
  limite ocupado e ativação revogada.
- Rejeição de vínculos de outras empresas/licenças ou dados inconsistentes.

Os testes são executáveis localmente sem credenciais ou conexão:

    python -B -m unittest -v tests.unit.licenciamento.test_multiativacao_dominio

A avaliação de vaga não reserva uma vaga e não confere autenticidade da chave.
Não é seguro ligá-la diretamente à tela de ativação nem ao banco de dados.

## Arquitetura a implementar depois

1. Backend confiável com autenticação da solicitação, prova de posse do
   dispositivo e verificação da licença.
2. Reserva atômica de vaga, com idempotência para o mesmo dispositivo.
   Exigir exclusividade de ativação ativa por licença/dispositivo e impedir
   duas solicitações concorrentes de consumirem a última vaga.
3. Emissão de autorização assinada com chave assimétrica; chave privada
   somente no backend, chave pública no cliente.
4. Verificação no startup, renovação e política offline, revisando as 72 h
   atuais e comportamento sob relógio do sistema não confiável.
5. Isolamento de dados operacionais no Supabase por company_id com RLS
   baseada em identidade autenticada. O client não pode escolher um
   company_id arbitrário para acessar dados; nenhuma service_role no PC.
6. Sincronização entre SQLite local e Postgres em etapa separada, com regras
   para conflitos e restauração de backups individuais por empresa.
7. Administração de empresas, licenças, limites, dispositivos e auditoria.

## Restrições de segurança

- O segredo HMAC presente no atual src/core/licenca.py não é adequado para
  emitir novas credenciais assimétricas no cliente.
- O device_id é um identificador, não uma autenticação; futuramente uma
  chave criptográfica do dispositivo deve comprovar a posse da instalação.
- O backend deve controlar as mudanças de limite, revogação e reativação.
- Não gravar segredos administrativos do Firebase ou Supabase no executável.
- Não vincular dados do almoxarifado ao license_id ou ao device_id.
- Manter PRs pequenos, testes de concorrência, isolamento por tenant e
  migração do fluxo antigo antes de ativar o novo em produção.

## Arquitetura de dados: SQLite local + Supabase multiempresa

A V1 NÃO substituirá SQLite por conexão SQL direta ao Supabase. O desenho
pretendido é:
- SQLite: armazenamento operacional por instalação, útil mesmo sem internet.
- Supabase PostgreSQL: fonte central dos dados da empresa, com RLS e
  autorização por identidade autenticada.
- API/backend confiável: autenticação, associação a company_id e operações
  privilegiadas. Não distribuir senha PostgreSQL, service_role ou chave privada
  no instalador do Windows.
- Sincronizador futuro: outbox local durável, IDs estáveis, operações
  idempotentes, versionamento e resolução explícita de conflitos.

Não basta aceitar um company_id enviado pelo cliente. O backend deve validar
a associação da identidade autenticada com a empresa. Para tabelas globais,
chaves estrangeiras e unicidade devem considerar tenant; dados operacionais
devem incluir company_id e testes de isolamento entre empresas.

### Inventário da implementação atual (main em 2026-11-19)

| Arquivo | Uso atual | Tratamento previsto |
| --- | --- | --- |
| src/core/database.py | Acesso central a equipamentos, eventos, movimentações, manutenção e auditoria; cria SQLite e opcionalmente conecta diretamente a PostgreSQL | PRESERVAR. Separar acesso local, repositórios e sincronização progressivamente. Descontinuar o caminho SQL direto somente após substituição e testes |
| src/ui/main_window.py e abas | Dependem de Database e recebem a instância compartilhada | PRESERVAR. Migrar por interfaces estáveis |
| src/ui/dialogs_utils.py | Backup/restauração local por cópia de arquivo .db | PRESERVAR temporariamente. Substituir por backup consistente do SQLite via API nativa; depois implementar exportação/restauração por company_id no backend |
| tools/migrar_sqlite_para_postgres.py | Importação legado de SQLite em PostgreSQL direto; aceita opção destrutiva --truncate | LEGADO. Não usar como sincronizador Supabase multiempresa. Remover quando houver substituto ou quando a ausência de dependências estiver comprovada |
| tools/testar_conexao_postgres.py | Teste de conexão PostgreSQL direta, com configuração local | LEGADO. Não utilizar no instalador distribuído; remover após aposentadoria do modo direto |
| MIGRACAO_POSTGRES.md | Documenta o fluxo legado de conexão direta | Arquivar ou retirar somente após a nova arquitetura estar implementada |
| SEAGBH.spec | Contém caminhos absolutos de máquina local e inclui um seaghb.db de origem no empacotamento | Ajustar antes do novo build; não distribuir um banco de exemplo como estado operacional do cliente |

### Regras de migração e remoção segura

1. Não apagar src/core/database.py ou os diálogos antes de atualizar todos os
   chamadores, testes e configuração do empacotador.
2. Não apagar nenhum SQLite local, backup, histórico ou dado persistente do
   computador do desenvolvedor; GitHub não revela o conteúdo do disco local.
3. Ao revisar SEAGBH.spec, tornar caminhos portáveis e separar recursos
   empacotados de dados graváveis (preferencialmente pasta de dados do usuário).
4. O backup atual usa checkpoint WAL + cópia do arquivo; para backup com
   conexão ativa, preferir sqlite3.Connection.backup, validar integridade e
   impedir restauração por cópia enquanto a conexão está aberta.
5. Os IDs inteiros atuais podem colidir entre PCs; estabelecer UUID estável
   ou mapeamento versionado antes de sincronizar registros.
6. Implementar migrações SQL versionadas e testes de isolamento e backup
   restaurável antes de remover o modo PostgreSQL direto.
7. Nenhuma nova tabela Supabase, política RLS, migração remota ou exclusão
   destrutiva será feita neste PR de domínio.

### Backups no modelo futuro

O backup por empresa será uma exportação lógica consistente, restrita a
company_id, com referências e anexos correspondentes; não corresponde a um
dump nativo de todo o banco Supabase. A restauração exige autenticação,
validação de integridade, isolamento transacional, bloqueio de escritas da
empresa e reinicialização segura da sincronização das instalações.
