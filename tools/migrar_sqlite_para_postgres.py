"""
Migra dados do SQLite local (seaghb.db) para PostgreSQL.

Uso (PowerShell):
python tools/migrar_sqlite_para_postgres.py --sqlite-path .\\src\\seaghb.db
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import Iterable

try:
    import psycopg  # type: ignore
except ImportError as exc:  # pragma: no cover - execução direta
    raise SystemExit(
        "Dependência ausente: instale 'psycopg[binary]' no ambiente atual."
    ) from exc


TABLES = [
    (
        "equipamentos",
        ["id", "codigo_barras", "nome", "descricao", "localizacao", "data_cadastro"],
    ),
    (
        "eventos",
        [
            "id",
            "nome_evento",
            "local_evento",
            "responsavel",
            "data_inicio",
            "data_fim",
            "status",
            "notas",
        ],
    ),
    (
        "movimentacoes",
        ["id", "evento_id", "equipamento_id", "data_movimentacao", "tipo"],
    ),
    (
        "manutencao",
        ["id", "equipamento_id", "data_saida", "local_manutencao", "status"],
    ),
    (
        "auditoria",
        ["id", "tabela", "item_id", "acao", "detalhes", "data", "usuario"],
    ),
]


def _load_env_file() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(root, ".env")
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _default_sqlite_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "src", "seaghb.db")


def _ensure_schema(pg_cur) -> None:
    ddl = """
    CREATE TABLE IF NOT EXISTS equipamentos (
        id SERIAL PRIMARY KEY,
        codigo_barras TEXT UNIQUE NOT NULL,
        nome TEXT NOT NULL,
        descricao TEXT,
        localizacao TEXT,
        data_cadastro TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS eventos (
        id SERIAL PRIMARY KEY,
        nome_evento TEXT NOT NULL,
        local_evento TEXT NOT NULL DEFAULT 'Não especificado',
        responsavel TEXT NOT NULL,
        data_inicio TEXT NOT NULL,
        data_fim TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Agendado',
        notas TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS movimentacoes (
        id SERIAL PRIMARY KEY,
        evento_id INTEGER NOT NULL REFERENCES eventos(id) ON DELETE CASCADE,
        equipamento_id INTEGER NOT NULL REFERENCES equipamentos(id) ON DELETE CASCADE,
        data_movimentacao TEXT NOT NULL,
        tipo TEXT CHECK(tipo IN ('saida', 'retorno'))
    );

    CREATE TABLE IF NOT EXISTS manutencao (
        id SERIAL PRIMARY KEY,
        equipamento_id INTEGER NOT NULL REFERENCES equipamentos(id),
        data_saida TEXT NOT NULL,
        local_manutencao TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Em manutenção'
    );

    CREATE TABLE IF NOT EXISTS auditoria (
        id SERIAL PRIMARY KEY,
        tabela TEXT NOT NULL,
        item_id INTEGER NOT NULL,
        acao TEXT NOT NULL,
        detalhes TEXT,
        data TEXT NOT NULL,
        usuario TEXT DEFAULT 'Sistema'
    );
    """

    for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
        pg_cur.execute(stmt)


def _rows_from_sqlite(sqlite_cur, table: str, columns: list[str]) -> list[tuple]:
    cols = ", ".join(columns)
    sqlite_cur.execute(f"SELECT {cols} FROM {table} ORDER BY id")
    return sqlite_cur.fetchall()


def _insert_rows(pg_cur, table: str, columns: list[str], rows: Iterable[tuple]) -> int:
    rows = list(rows)
    if not rows:
        return 0

    cols = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
    pg_cur.executemany(sql, rows)
    return len(rows)


def _reset_sequence(pg_cur, table: str) -> None:
    pg_cur.execute(
        """
        SELECT setval(
            pg_get_serial_sequence(%s, 'id'),
            COALESCE((SELECT MAX(id) FROM """ + table + """), 1),
            (SELECT COUNT(*) > 0 FROM """ + table + """)
        )
        """,
        (table,),
    )


def parse_args() -> argparse.Namespace:
    _load_env_file()
    parser = argparse.ArgumentParser(description="Migra dados SQLite -> PostgreSQL")
    parser.add_argument("--sqlite-path", default=_default_sqlite_path())
    parser.add_argument("--pg-host", default=os.getenv("SEAGBH_DB_HOST", "127.0.0.1"))
    parser.add_argument("--pg-port", type=int, default=int(os.getenv("SEAGBH_DB_PORT", "5432")))
    parser.add_argument("--pg-db", default=os.getenv("SEAGBH_DB_NAME", "seagbh"))
    parser.add_argument("--pg-user", default=os.getenv("SEAGBH_DB_USER", "postgres"))
    parser.add_argument("--pg-password", default=os.getenv("SEAGBH_DB_PASSWORD", "postgres"))
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Limpa tabelas de destino antes da carga (recomendado para primeira migração)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not os.path.exists(args.sqlite_path):
        print(f"SQLite não encontrado: {args.sqlite_path}")
        return 1

    sqlite_conn = sqlite3.connect(args.sqlite_path)
    sqlite_cur = sqlite_conn.cursor()

    pg_conn = psycopg.connect(
        host=args.pg_host,
        port=args.pg_port,
        dbname=args.pg_db,
        user=args.pg_user,
        password=args.pg_password,
        connect_timeout=10,
    )

    try:
        with pg_conn:
            with pg_conn.cursor() as pg_cur:
                _ensure_schema(pg_cur)

                if args.truncate:
                    pg_cur.execute(
                        "TRUNCATE TABLE movimentacoes, manutencao, auditoria, eventos, equipamentos RESTART IDENTITY CASCADE"
                    )

                total = 0
                for table, columns in TABLES:
                    rows = _rows_from_sqlite(sqlite_cur, table, columns)
                    inserted = _insert_rows(pg_cur, table, columns, rows)
                    _reset_sequence(pg_cur, table)
                    total += inserted
                    print(f"{table}: {inserted} registros")

                print(f"Migração concluída. Total de registros inseridos: {total}")
        return 0
    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    sys.exit(main())
