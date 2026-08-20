# Migração para PostgreSQL

Este projeto agora suporta dois bancos:
- `sqlite` (padrão atual)
- `postgres` (multiusuário)

## 1) Pré-requisitos

1. PostgreSQL instalado e em execução.
2. Banco criado (ex.: `seagbh`).
3. Dependência Python instalada:

```powershell
pip install "psycopg[binary]"
```

## 2) Configurar conexão

Edite [.env](.env) e preencha:

- `SEAGBH_DB_ENGINE=postgres`
- `SEAGBH_DB_HOST`
- `SEAGBH_DB_PORT`
- `SEAGBH_DB_NAME`
- `SEAGBH_DB_USER`
- `SEAGBH_DB_PASSWORD`

Observação: o sistema e os scripts em `tools/` carregam automaticamente o arquivo `.env`.

Teste a conexão antes da migração:

```powershell
python tools/testar_conexao_postgres.py
```

## 3) Migrar dados do SQLite

Com o app fechado, execute:

```powershell
python tools/migrar_sqlite_para_postgres.py --sqlite-path .\src\seaghb.db --truncate
```

O script:
- cria o schema no PostgreSQL se necessário;
- copia dados preservando IDs;
- ajusta sequências (`SERIAL`) para continuar incrementando corretamente.

## 4) Validar

1. Abra o sistema com `SEAGBH_DB_ENGINE=postgres`.
2. Confira telas principais: Equipamentos, Eventos, Manutenção e Relatórios.
3. Faça um teste de escrita (cadastro + consulta + retorno).

## 5) Rollback rápido

Se precisar voltar imediatamente:
- troque `SEAGBH_DB_ENGINE=sqlite` no `.env`;
- reinicie o sistema.
