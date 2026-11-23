# Histórico administrativo privado

O módulo `src/core/historico_licencas.py` usa um SQLite independente para registros ativos e excluídos. A classe recebe um diretório absoluto explícito. O resolvedor indica `%LOCALAPPDATA%\SEAGBH\GerenciamentoClientes` no Windows e `~/.local/share/SEAGBH/GerenciamentoClientes` no Linux. O resolvedor e o construtor não abrem arquivos. Uma leitura de armazenamento ausente, incompatível ou corrompido retorna um erro com código interno. Uma lista vazia indica um grupo sem registros em um armazenamento já importado.

## Proteção e integridade

- A criação no Windows usa uma DACL NTFS protegida com acesso do operador e do sistema. O módulo verifica dono, permissões dos ancestrais que poderiam permitir substituição e permissões de cada banco e journal. O operador, SYSTEM e administradores locais continuam dentro da fronteira de confiança do sistema. Um volume de rede ou sem suporte comprovado é recusado.
- No Linux, o módulo exige diretório `0700` e arquivos `0600` do usuário efetivo, verifica os ancestrais e aceita os sistemas de arquivos locais reconhecidos em `/proc/self/mountinfo`. Links e pontos de reanálise são recusados nos caminhos de armazenamento. O módulo não corrige permissões existentes.
- Cada operação abre e fecha sua própria conexão. O banco tem identificador de aplicação e versão de esquema. Escritores usam `BEGIN IMMEDIATE`, journal de rollback e `synchronous=EXTRA`. O mesmo ID só pode existir uma vez. A movimentação entre os grupos confirma em uma transação, e uma gravação baseada em registro alterado por outra instância recebe erro. [Configuração de durabilidade do SQLite](https://www.sqlite.org/pragma.html#pragma_synchronous)
- Essas medidas protegem o acesso pelo sistema operacional. Os bancos contêm dados em claro no perfil do operador. Testes de interrupção do processo validam a recuperação do SQLite nesse cenário. Perda de energia, restauração do perfil e recuperação dos dados reais exigem homologação própria.

## Importação e recuperação em homologação

`migrar_fixtures` recebe explicitamente os bytes de dois JSONs sintéticos. O módulo não localiza nem abre os JSONs legados. A importação valida a estrutura, a presença dos campos usados pela ferramenta, IDs repetidos e estados incompatíveis. Os campos adicionais, a ordem e os valores são preservados. Um erro interrompe a importação inteira para análise.

A cópia `originais.sqlite3` armazena os dois fluxos de bytes exatos, em uma transação SQLite própria confirmada antes da importação. O SQLite principal valida integridade, contagem e equivalência antes de confirmar a marca de importação. Se o processo parar entre as duas confirmações, `retomar_fixtures_da_copia` repete a importação a partir da cópia verificada. Repetir a mesma importação preserva alterações feitas depois dela. Origens divergentes ou um destino incompatível exigem resolução explícita. Se apenas a cópia sobreviver, a rotina recusa recriar o banco principal, pois a cópia inicial contém os JSONs de entrada e pode estar desatualizada.

## Integração controlada

A homologação injeta `HistoricoLicencas` na janela administrativa. A janela exige sessão autorizada antes de ler o armazenamento. O caminho privado não recorre aos JSONs quando faltam arquivos, quando há perda de permissões ou quando a importação ainda está pendente. O ponto de entrada continua usando o caminho legado até uma ativação aprovada.

Em exclusão e restauração, a janela confirma o Firebase antes de iniciar a movimentação SQLite. A confirmação remota e a transação local são independentes. Se o Firebase confirmar e o SQLite falhar, a interface informa a falha e pede conferência do estado remoto antes de repetir. O bloqueio SQLite coordena os escritores locais.

### Roteiro de ativação

1. Aprovar uma sessão de migração com o operador e conferir o diretório privado, suas ACLs e o destino dos backups. Definir uma janela sem instâncias administrativas concorrentes.
2. Homologar o procedimento em uma cópia sintética. Depois de autorização específica, gerar uma cópia privada das duas origens, resolver conflitos e conferir os totais, a integridade e a equivalência antes de direcionar a ferramenta ao SQLite.
3. Confirmar cópia, exportação, movimentação e falhas com a sessão autorizada. Em caso de falha, suspender o uso administrativo e preservar os originais e as duas bases privadas até a decisão do operador.
4. Planejar separadamente backup periódico do SQLite, remoção segura do rastreamento dos JSONs e tratamento de cópias históricas públicas.
