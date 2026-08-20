"""
Teste rápido de conexão com PostgreSQL usando parâmetros do .env.
"""

from __future__ import annotations

import os
import sys

try:
    import psycopg  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Dependência ausente: instale 'psycopg[binary]'.") from exc


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


def main() -> int:
    _load_env_file()

    host = os.getenv("SEAGBH_DB_HOST", "127.0.0.1")
    port = int(os.getenv("SEAGBH_DB_PORT", "5432"))
    dbname = os.getenv("SEAGBH_DB_NAME", "seagbh")
    user = os.getenv("SEAGBH_DB_USER", "postgres")
    password = os.getenv("SEAGBH_DB_PASSWORD", "postgres")

    try:
        with psycopg.connect(
            host=host,
            port=port,
            dbname=dbname,
            user=user,
            password=password,
            connect_timeout=10,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), current_user")
                row = cur.fetchone()
                print(f"Conexão OK | database={row[0]} | user={row[1]}")
        return 0
    except Exception as exc:
        print(f"Falha na conexão PostgreSQL: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
