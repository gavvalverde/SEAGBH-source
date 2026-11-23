# Historicos administrativos: bloqueio de novos vazamentos no Git

## Estado e fronteira desta etapa

A ferramenta administrativa ainda pode utilizar os arquivos legados `tools/historico_chaves.json` e `tools/historico_excluidos.json` enquanto nao houver migracao e ativacao explicitamente aprovadas do SQLite privado introduzido no PR #83. **Os dois JSONs estao rastreados no Git publico**, inclusive em revisoes anteriores. Este PR nao abre, exibe, edita, apaga ou migra seus conteudos e nao modifica regras Firebase, chaves nem licencas.

A protecao preventiva usa tres medidas:
1. `tools/.gitignore` evita inclusoes acidentais de novos arquivos de historico e copias ao Git. **Nao deixa de rastrear arquivos ja versionados.**
2. `tools/protecao_historicos_git.py` verifica exclusivamente metadados de `git ls-tree -rz HEAD`. Aceita apenas os dois blobs legados originais por seus identificadores Git. Qualquer alteracao desses blobs ou outro historico com nome reconhecido causa falha. O script nao abre dados e omite nomes no log de erro.
3. `historico-git-ci.yml` executa a verificacao em todos os PRs para `main` e nos pushes a `main`, com permissoes de leitura e sem credenciais persistidas. A configuracao de protecao de branch deve exigir `historico-git` como check obrigatorio, com bypass estritamente controlado, e revisao obrigatoria de mudancas no proprio workflow/validador. Como a protecao de branch e externa ao codigo, **a presenca do workflow sozinha nao bloqueia pushes diretos**.

O nome reconhecido pelo verificador e uma defesa pontual, nao substitui uma politica de classificacao de todos os artefatos sensiveis. A existencia e os OIDs dos dois blobs historicos sao publicos e nao revelam seu conteudo neste procedimento.

## Plano de retirada SEM perda das copias de trabalho

**Nao executar `git rm --cached`, `git filter-repo`, checkout de revisao que remove arquivos, exclusao de dados, alteracao de ACL real ou importacao operacional nesta etapa.** Uma remocao dos arquivos rastreados em um novo commit pode fazer `git pull` ou `git checkout` apagar arquivos locais nao modificados. O `.gitignore` nao impede esse efeito.

Antes de retirar os blobs do indice, obter aprovacao humana especifica e executar localmente, em computador autorizado:

1. Suspender a ferramenta administrativa e suas instancias concorrentes; inventariar caminhos, usuarios, backups e ACLs sem publicar dados.
2. Classificar os arquivos e commits historicos, sob acesso restrito, para determinar se ha registros ou chaves reais e quem pode ter tido acesso. Tratar como possivel exposicao historica; remover da revisao atual nao apaga o que ja ficou publico.
3. Fazer copias privadas verificadas e recuperar todos os registros dos arquivos legados, dos backups e das instalacoes pertinentes. Conferir contagens, duplicidade, integridade e equivalencia da importacao no SQLite privado. Preservar os bytes originais e documentar o rollback.
4. Confirmar armazenamento privado e permisos NTFS/POSIX, restauracao de backup, ausencia de historicos operacionais na arvore Git e testes de login/autorizacao.
5. Planejar a transicao de todas as maquinas/branches para evitar delecao involuntaria na atualizacao. Somente entao preparar PR separado para parar de rastrear os arquivos e retirar o fallback legado.
6. Avaliar separadamente reescrita do historico publico, forks, clones, caches, releases e eventual reemissao/revogacao de chaves afetadas. Reescrever Git invalida SHAs, exige coordenacao de clones e nao garante eliminacao de copias previamente obtidas.

## Limites

- Esta etapa **nao resolve exposicao anterior**, nao cifra historicos e nao ativa SQLite privado.
- A integridade do estado remoto Firebase e do SQLite local ainda requer reconciliacao apos falha entre as duas confirmacoes.
- O verificador identifica nomes conhecidos de historicos e bancos administrativos; arquivos renomeados para outros nomes podem escapar. Revisoes humanas, branch protection e inventario abrangente continuam obrigatorios.
- Os arquivos JSON existentes permanecem presentes no repositorio por seguranca operacional da transicao. Nao utilizar o gerenciador com dados reais ate a conclusao da avaliacao de exposicao e da migracao aprovada.
