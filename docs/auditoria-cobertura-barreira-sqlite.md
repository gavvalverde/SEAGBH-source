# Cobertura da barreira SQLite e diagnóstico passivo de manutenção

## Estado verificado após o PR #81

Base: `main` em `a67746a3a274de6c136c5d3c5f12a9518cb81e09`.
Esta auditoria considera somente o código rastreado. Não investiga bancos,
instalações, processos, backups ou configurações reais. Não prova exclusividade
contra aplicações externas nem versões antigas.

| Entrada | Comportamento observado | Lacuna até operação real |
| --- | --- | --- |
| `src/core/database.py` | `Database(banco_sqlite_protegido=...)` entra na sessão SQLite com concessão; `Database()` padrão ainda usa `sqlite3.connect` diretamente. | Não é seguro supor que todas as instalações adotam a sessão protegida. |
| `src/ui/main_window.py`, dashboard e atrasos | A UI de homologação propaga o caminho protegido aos workers e aguarda fechamento; na execução padrão não recebe caminho protegido. | Aprovar e testar a ativação transversal antes de manutenção real. |
| `src/core/backup_sqlite.py` | `criar_backup(..., protegido=True)` adquire concessão compartilhada da origem; o modo legado permanece disponível. | Cobrir todos os chamadores e instalações do backup. |
| `src/core/restauracao_sqlite.py` | Pré-validação consultiva, sem autorização de execução. | Compatibilidade global, isolamento e estabilidade contínua não comprovados. |
| `src/core/executor_restauracao_sqlite.py` | Execução e reconciliação restritas à raiz temporária marcada como sintética. | Não converter diretamente em executor operacional. |
| `tools/migrar_sqlite_para_postgres.py` | Abre SQLite com `sqlite3.connect(args.sqlite_path)` fora da barreira cooperativa. | Corrigir em PR independente, com rollback e isolamento; não executar a ferramenta para auditar. |
| Clientes externos e versões implantadas | Não inventariados. | Exigir isolamento administrativo verificável ou recusar substituição. |

## Ferramenta de diagnóstico passivo

`tools/inspecionar_manutencao_sqlite.py` exige `--destino` absoluto explícito.
Ela utiliza `caminho_coordenacao` para validar a identidade do destino e
consulta somente `lstat` do próprio destino e de seus nomes auxiliares conhecidos.
Não usa `sqlite3.connect`, não abre conteúdo, não cria arquivos nem adquire
concessão. Não importa `Database`, PyQt, licença, Firebase ou `.env`.

O resultado JSON expõe apenas estados genéricos, sem caminhos, SQL ou conteúdo.
`restauracao_autorizada` é **sempre falso**; `exclusividade` e `compatibilidade`
permanecem `nao_comprovada`. Um resultado `inspecionado` significa apenas que
as consultas pontuais de metadados foram concluídas. A ausência de WAL,
auxiliares ou lock não comprova que o banco esteja fechado.

Execução **somente em fixture sintética** na homologação:

```text
python -B tools/inspecionar_manutencao_sqlite.py --destino CAMINHO_ABSOLUTO_DE_FIXTURE_SINTETICA.db
python -B -m unittest -v tests.unit.test_inspecao_manutencao_sqlite
```

A ferramenta não é opção de recuperação nem é chamada pela GUI. Não invocá-la
sobre dados operacionais reais sem autorização específica.

## Próximas condições de aceite

1. Revisar todos os pontos de `sqlite3.connect`, construtores de `Database`,
   caminhos de fechamento e ferramentas auxiliares após cada alteração relevante.
2. Converter os caminhos legados para participação cooperativa em PRs pequenos,
   sem alterar o PostgreSQL ou o modo padrão antes da homologação.
3. Validar processos independentes Windows/Linux, inclusive cliente externo sem
   concessão, arquivos WAL/SHM, interrupção e reinício com diário pendente.
4. Projetar entrada independente de manutenção com autenticação/autorização,
   exclusividade real, verificação dos clientes e política de recuperação.
5. Somente após aprovação específica, projetar resgate incluindo WAL, protocolo
   de publicação/quarentena, diário e fronteira de reabertura. A GUI permanece
   com restauração bloqueada até homologação operacional separada.

Esta etapa não modifica dados, lock, arquivo de coordenação, banco, esquemas,
backups, interfaces nem os mecanismos de licenciamento.
