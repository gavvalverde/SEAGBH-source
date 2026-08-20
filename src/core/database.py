"""
Camada de acesso a dados — isola TODO o SQL do resto da aplicação.
Reutiliza o mesmo seaghb.db da versão Tkinter.
"""

import os
import sys
import sqlite3
from datetime import datetime

try:
    import psycopg  # type: ignore
except ImportError:
    psycopg = None


def _get_app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__)).replace("\\core", "").replace("/core", "")


def get_db_path() -> str:
    return os.path.join(_get_app_dir(), "seaghb.db")


APP_DIR = _get_app_dir()


def _load_env_file() -> None:
    """Carrega variáveis do arquivo .env (sem sobrescrever env já definido)."""
    env_path = os.path.join(os.path.dirname(APP_DIR), ".env")
    if not os.path.exists(env_path):
        return

    try:
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
    except OSError:
        # Falha de leitura do .env não deve impedir inicialização.
        pass


def _replace_qmark_placeholders(query: str) -> str:
    """Converte placeholders estilo SQLite (?) para psycopg (%s)."""
    out = []
    in_string = False
    for ch in query:
        if ch == "'":
            in_string = not in_string
        if ch == "?" and not in_string:
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


class _CompatCursor:
    """Cursor compatível entre SQLite e PostgreSQL (paramstyle + script)."""

    def __init__(self, raw_cursor, backend: str):
        self._cursor = raw_cursor
        self._backend = backend

    def execute(self, query: str, params=None):
        sql = _replace_qmark_placeholders(query) if self._backend == "postgres" else query
        if params is None:
            return self._cursor.execute(sql)
        return self._cursor.execute(sql, params)

    def executemany(self, query: str, seq_of_params):
        sql = _replace_qmark_placeholders(query) if self._backend == "postgres" else query
        return self._cursor.executemany(sql, seq_of_params)

    def executescript(self, script: str):
        if self._backend != "postgres":
            return self._cursor.executescript(script)
        for stmt in [s.strip() for s in script.split(";") if s.strip()]:
            self.execute(stmt)
        return None

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class Database:
    """Wrapper de dados com suporte a SQLite e PostgreSQL."""

    def __init__(self):
        """Inicializa conexão com banco de dados.
        
        Raises:
            Exception: Se não conseguir conectar ao banco de dados
        """
        import logging
        logger = logging.getLogger(__name__)

        _load_env_file()
        
        self.conn = None
        self.cursor = None
        self.backend = os.getenv("SEAGBH_DB_ENGINE", "sqlite").strip().lower()
        if self.backend in ("postgresql", "postgres"):
            self.backend = "postgres"
        else:
            self.backend = "sqlite"
        
        try:
            if self.backend == "postgres":
                if psycopg is None:
                    raise ImportError(
                        "Dependência ausente: instale 'psycopg[binary]' para usar PostgreSQL"
                    )

                self.conn = psycopg.connect(
                    host=os.getenv("SEAGBH_DB_HOST", "127.0.0.1"),
                    port=int(os.getenv("SEAGBH_DB_PORT", "5432")),
                    dbname=os.getenv("SEAGBH_DB_NAME", "seagbh"),
                    user=os.getenv("SEAGBH_DB_USER", "postgres"),
                    password=os.getenv("SEAGBH_DB_PASSWORD", "postgres"),
                    connect_timeout=int(os.getenv("SEAGBH_DB_TIMEOUT", "10")),
                )
                self.cursor = _CompatCursor(self.conn.cursor(), backend="postgres")
                logger.info("Banco PostgreSQL conectado com sucesso")
            else:
                db_path = get_db_path()
                lock_path = db_path + ".lock"

                # Tentar adquirir lock para detectar múltiplas instâncias
                try:
                    with open(lock_path, "w") as f:
                        f.write(str(os.getpid()))
                    logger.info(f"Lock adquirido: {lock_path}")
                except (OSError, IOError):
                    logger.warning("Possível múltipla instância do app executando")

                self.conn = sqlite3.connect(db_path, timeout=30)
                self.conn.execute("PRAGMA foreign_keys = ON")
                self.conn.execute("PRAGMA journal_mode = WAL")
                self.cursor = _CompatCursor(self.conn.cursor(), backend="sqlite")

            self._criar_tabelas()
            self._atualizar_estrutura()
        except Exception as e:
            logger.error(f"Falha ao inicializar banco de dados: {e}")
            raise

    # ── DDL ───────────────────────────────────────────────────────────────

    def _criar_tabelas(self):
        if self.backend == "postgres":
            self.cursor.executescript("""
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
            """)
        else:
            self.cursor.executescript("""
                CREATE TABLE IF NOT EXISTS equipamentos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    codigo_barras TEXT UNIQUE NOT NULL,
                    nome TEXT NOT NULL,
                    descricao TEXT,
                    localizacao TEXT,
                    data_cadastro TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS eventos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome_evento TEXT NOT NULL,
                    local_evento TEXT NOT NULL DEFAULT 'Não especificado',
                    responsavel TEXT NOT NULL,
                    data_inicio TEXT NOT NULL,
                    data_fim TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Agendado',
                    notas TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS movimentacoes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    evento_id INTEGER NOT NULL,
                    equipamento_id INTEGER NOT NULL,
                    data_movimentacao TEXT NOT NULL,
                    tipo TEXT CHECK(tipo IN ('saida', 'retorno')),
                    FOREIGN KEY(evento_id) REFERENCES eventos(id) ON DELETE CASCADE,
                    FOREIGN KEY(equipamento_id) REFERENCES equipamentos(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS manutencao (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    equipamento_id INTEGER NOT NULL,
                    data_saida TEXT NOT NULL,
                    local_manutencao TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Em manutenção',
                    FOREIGN KEY(equipamento_id) REFERENCES equipamentos(id)
                );

                CREATE TABLE IF NOT EXISTS auditoria (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tabela TEXT NOT NULL,
                    item_id INTEGER NOT NULL,
                    acao TEXT NOT NULL,
                    detalhes TEXT,
                    data TEXT NOT NULL,
                    usuario TEXT DEFAULT 'Sistema'
                );
            """)
        self.conn.commit()

    def _coluna_existe(self, tabela: str, coluna: str) -> bool:
        if self.backend == "postgres":
            self.cursor.execute(
                """SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public' AND table_name=? AND column_name=?""",
                (tabela, coluna),
            )
            return self.cursor.fetchone() is not None

        self.cursor.execute(f"PRAGMA table_info({tabela})")
        return any(row[1] == coluna for row in self.cursor.fetchall())

    def _atualizar_estrutura(self):
        """Adiciona colunas novas se não existirem (migração)."""
        for col, ddl in [
            ("local_evento", "ALTER TABLE eventos ADD COLUMN local_evento TEXT NOT NULL DEFAULT 'Não especificado'"),
            ("notas",        "ALTER TABLE eventos ADD COLUMN notas TEXT DEFAULT ''"),
        ]:
            if self._coluna_existe("eventos", col):
                continue
            try:
                self.cursor.execute(ddl)
            except Exception:
                pass
        self.conn.commit()

    # ── Eventos ───────────────────────────────────────────────────────────

    def listar_eventos(self, apenas_agendados=False) -> list:
        sql = """
            SELECT id, nome_evento, local_evento, responsavel,
                   data_inicio, data_fim, status
            FROM eventos
        """
        if apenas_agendados:
            sql += " WHERE status = 'Agendado'"
        sql += " ORDER BY id DESC"
        self.cursor.execute(sql)
        return self.cursor.fetchall()

    def criar_evento(self, nome, local, resp, inicio, fim, equipamentos_ids, notas=""):
        """
        Cria evento + movimentações de saída em uma única transação.
        Retorna o ID do novo evento.
        """
        try:
            self.cursor.execute("BEGIN")
            if self.backend == "postgres":
                self.cursor.execute(
                    """INSERT INTO eventos
                       (nome_evento, local_evento, responsavel, data_inicio, data_fim, status, notas)
                       VALUES (?, ?, ?, ?, ?, 'Agendado', ?) RETURNING id""",
                    (nome, local, resp, inicio, fim, notas),
                )
                evento_id = self.cursor.fetchone()[0]
            else:
                self.cursor.execute(
                    """INSERT INTO eventos
                       (nome_evento, local_evento, responsavel, data_inicio, data_fim, status, notas)
                       VALUES (?, ?, ?, ?, ?, 'Agendado', ?)""",
                    (nome, local, resp, inicio, fim, notas),
                )
                evento_id = self.cursor.lastrowid
            agora = datetime.now().strftime("%d/%m/%Y %H:%M")
            for eid in equipamentos_ids:
                self.cursor.execute(
                    """INSERT INTO movimentacoes
                       (evento_id, equipamento_id, data_movimentacao, tipo)
                       VALUES (?, ?, ?, 'saida')""",
                    (evento_id, eid, agora),
                )
            self.conn.commit()
            return evento_id
        except Exception:
            self.conn.rollback()
            raise

    def obter_evento(self, evento_id) -> dict | None:
        self.cursor.execute(
            "SELECT id, nome_evento, local_evento, responsavel, "
            "data_inicio, data_fim, status, notas FROM eventos WHERE id = ?",
            (evento_id,),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        keys = ["id", "nome_evento", "local_evento", "responsavel",
                "data_inicio", "data_fim", "status", "notas"]
        return dict(zip(keys, row))

    def atualizar_evento(self, evento_id, nome, local, resp, inicio, fim):
        self.cursor.execute(
            """UPDATE eventos SET nome_evento=?, local_evento=?,
               responsavel=?, data_inicio=?, data_fim=? WHERE id=?""",
            (nome, local, resp, inicio, fim, evento_id),
        )
        self.conn.commit()

    def remover_evento(self, evento_id):
        self.cursor.execute("DELETE FROM eventos WHERE id = ?", (evento_id,))
        self.conn.commit()

    def concluir_evento(self, evento_id):
        self.cursor.execute(
            "UPDATE eventos SET status = 'Concluído' WHERE id = ?",
            (evento_id,),
        )
        self.conn.commit()

    # ── Equipamentos ──────────────────────────────────────────────────────

    def listar_equipamentos(self, filtro="") -> list:
        if filtro:
            # Validação de tamanho para evitar queries gigantes (DoS)
            if len(filtro) > 255:
                raise ValueError("Filtro muito longo (máximo 255 caracteres)")
            
            like = f"%{filtro.lower()}%"
            self.cursor.execute(
                """SELECT id, codigo_barras, nome, localizacao, data_cadastro
                   FROM equipamentos
                   WHERE lower(codigo_barras) LIKE ?
                      OR lower(nome) LIKE ?
                      OR lower(localizacao) LIKE ?
                   ORDER BY id""",
                (like, like, like),
            )
        else:
            self.cursor.execute(
                "SELECT id, codigo_barras, nome, localizacao, data_cadastro "
                "FROM equipamentos ORDER BY id"
            )
        return self.cursor.fetchall()

    def cadastrar_equipamento(self, codigo, nome, descricao, localizacao):
        self.cursor.execute(
            """INSERT INTO equipamentos
               (codigo_barras, nome, descricao, localizacao, data_cadastro)
               VALUES (?, ?, ?, ?, ?)""",
            (codigo, nome, descricao, localizacao,
             datetime.now().strftime("%d/%m/%Y %H:%M")),
        )
        self.conn.commit()

    def obter_id_por_codigo(self, codigo: str) -> int | None:
        """Busca por texto exato ou equivalência numérica."""
        codigo = str(codigo).strip()
        self.cursor.execute(
            "SELECT id FROM equipamentos WHERE codigo_barras = ?", (codigo,)
        )
        row = self.cursor.fetchone()
        if not row:
            try:
                num = int(codigo)
                self.cursor.execute(
                    "SELECT id FROM equipamentos WHERE CAST(codigo_barras AS INTEGER) = ?",
                    (num,),
                )
                row = self.cursor.fetchone()
            except ValueError:
                pass
        return row[0] if row else None

    def esta_em_manutencao(self, equipamento_id: int) -> bool:
        self.cursor.execute(
            "SELECT status FROM manutencao WHERE equipamento_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (equipamento_id,),
        )
        row = self.cursor.fetchone()
        return bool(row and row[0] == "Em manutenção")

    # ── Movimentações ─────────────────────────────────────────────────────

    def equipamentos_do_evento(self, evento_id) -> list:
        self.cursor.execute(
            """SELECT eq.codigo_barras, eq.nome,
                      CASE WHEN EXISTS (
                          SELECT 1 FROM movimentacoes r
                          WHERE r.tipo='retorno'
                            AND r.equipamento_id = m.equipamento_id
                            AND r.evento_id = m.evento_id
                      ) THEN 'Devolvido' ELSE 'Pendente' END
               FROM movimentacoes m
               JOIN equipamentos eq ON m.equipamento_id = eq.id
               WHERE m.evento_id = ? AND m.tipo = 'saida'""",
            (evento_id,),
        )
        return self.cursor.fetchall()

    def pendentes_retorno(self, evento_id) -> list:
        self.cursor.execute(
            """SELECT eq.codigo_barras, eq.nome
               FROM movimentacoes m
               JOIN equipamentos eq ON m.equipamento_id = eq.id
               WHERE m.evento_id = ? AND m.tipo = 'saida'
                 AND NOT EXISTS (
                     SELECT 1 FROM movimentacoes r
                     WHERE r.evento_id = m.evento_id
                       AND r.equipamento_id = m.equipamento_id
                       AND r.tipo = 'retorno')""",
            (evento_id,),
        )
        return self.cursor.fetchall()

    def registrar_retorno(self, evento_id, codigo_barras):
        agora = datetime.now().strftime("%d/%m/%Y %H:%M")
        self.cursor.execute(
            """INSERT INTO movimentacoes
               (evento_id, equipamento_id, data_movimentacao, tipo)
               VALUES (?, (SELECT id FROM equipamentos WHERE codigo_barras = ?),
                       ?, 'retorno')""",
            (evento_id, codigo_barras, agora),
        )
        self.conn.commit()

    # ── Eventos atrasados ─────────────────────────────────────────────────

    def eventos_atrasados(self) -> list:
        self.cursor.execute("""
            SELECT e.id, e.nome_evento, e.data_fim, COUNT(*)
            FROM eventos e
            JOIN movimentacoes m ON m.evento_id = e.id AND m.tipo = 'saida'
            WHERE e.status = 'Agendado'
              AND NOT EXISTS (
                  SELECT 1
                  FROM movimentacoes r
                  WHERE r.evento_id = m.evento_id
                    AND r.equipamento_id = m.equipamento_id
                    AND r.tipo = 'retorno'
              )
            GROUP BY e.id, e.nome_evento, e.data_fim
        """)
        rows = self.cursor.fetchall()
        hoje = datetime.now().date()
        atrasados = []
        for row in rows:
            data_fim_raw = str(row[2] or "").strip()
            data_evento = None
            for fmt in ("%d/%m/%Y", "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"):
                try:
                    data_evento = datetime.strptime(data_fim_raw, fmt).date()
                    break
                except ValueError:
                    continue
            if data_evento and data_evento < hoje:
                atrasados.append(row)
        return atrasados

    # ── Auditoria ─────────────────────────────────────────────────────────

    # ── Equipamentos – extras ────────────────────────────────────────────

    def editar_equipamento(self, equip_id, nome, descricao, localizacao):
        self.cursor.execute(
            "UPDATE equipamentos SET nome=?, descricao=?, localizacao=? WHERE id=?",
            (nome, descricao, localizacao, equip_id),
        )
        self.conn.commit()

    def remover_equipamento(self, equip_id):
        """Remove equipamento e registros relacionados em cascata."""
        try:
            self.cursor.execute("BEGIN")
            self.cursor.execute("DELETE FROM movimentacoes WHERE equipamento_id=?", (equip_id,))
            self.cursor.execute("DELETE FROM manutencao WHERE equipamento_id=?", (equip_id,))
            self.cursor.execute("DELETE FROM equipamentos WHERE id=?", (equip_id,))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def obter_equipamento(self, equip_id) -> tuple | None:
        self.cursor.execute(
            "SELECT id, codigo_barras, nome, descricao, localizacao, data_cadastro "
            "FROM equipamentos WHERE id=?", (equip_id,),
        )
        return self.cursor.fetchone()

    def listar_equipamentos_disponiveis(self, filtro="") -> list:
        """Lista equipamentos que NÃO estão em manutenção."""
        if filtro:
            like = f"%{filtro.lower()}%"
            self.cursor.execute(
                """SELECT id, codigo_barras, nome, localizacao, data_cadastro
                   FROM equipamentos
                   WHERE id NOT IN (
                       SELECT equipamento_id FROM manutencao WHERE status='Em manutenção'
                   )
                   AND (lower(codigo_barras) LIKE ?
                     OR lower(nome) LIKE ?
                     OR lower(localizacao) LIKE ?)
                   ORDER BY id""",
                (like, like, like),
            )
        else:
            self.cursor.execute(
                """SELECT id, codigo_barras, nome, localizacao, data_cadastro
                   FROM equipamentos
                   WHERE id NOT IN (
                       SELECT equipamento_id FROM manutencao WHERE status='Em manutenção'
                   )
                   ORDER BY id"""
            )
        return self.cursor.fetchall()

    def verificar_pendencia_retorno(self, equip_id) -> tuple | None:
        """Retorna (tipo, evento_id) da última movimentação, ou None."""
        self.cursor.execute(
            "SELECT tipo, evento_id FROM movimentacoes "
            "WHERE equipamento_id=? ORDER BY id DESC LIMIT 1",
            (equip_id,),
        )
        return self.cursor.fetchone()

    def obter_nome_evento(self, evento_id) -> str:
        self.cursor.execute("SELECT nome_evento FROM eventos WHERE id=?", (evento_id,))
        row = self.cursor.fetchone()
        return row[0] if row else ""

    def obter_nome_equipamento(self, equip_id) -> str:
        self.cursor.execute("SELECT nome FROM equipamentos WHERE id=?", (equip_id,))
        row = self.cursor.fetchone()
        return row[0] if row else ""

    # ── Manutenção ────────────────────────────────────────────────────────

    def listar_manutencao(self) -> list:
        self.cursor.execute(
            """SELECT m.id, e.codigo_barras, e.nome,
                      m.data_saida, m.local_manutencao, m.status
               FROM manutencao m
               JOIN equipamentos e ON m.equipamento_id = e.id
               ORDER BY m.id DESC"""
        )
        return self.cursor.fetchall()

    def cadastrar_saida_manutencao(self, equip_id, local_manutencao):
        self.cursor.execute(
            """INSERT INTO manutencao
               (equipamento_id, data_saida, local_manutencao, status)
               VALUES (?, ?, ?, 'Em manutenção')""",
            (equip_id, datetime.now().strftime("%d/%m/%Y %H:%M"), local_manutencao),
        )
        self.conn.commit()

    def registrar_retorno_manutencao(self, manutencao_id):
        self.cursor.execute(
            "UPDATE manutencao SET status='Concluído' WHERE id=?", (manutencao_id,),
        )
        self.conn.commit()

    def obter_status_manutencao(self, manutencao_id) -> str | None:
        self.cursor.execute("SELECT status FROM manutencao WHERE id=?", (manutencao_id,))
        row = self.cursor.fetchone()
        return row[0] if row else None

    # ── Movimentações – edição de evento ──────────────────────────────────

    def adicionar_equipamento_evento(self, evento_id, equip_id):
        self.cursor.execute(
            """INSERT INTO movimentacoes
               (evento_id, equipamento_id, data_movimentacao, tipo)
               VALUES (?, ?, ?, 'saida')""",
            (evento_id, equip_id, datetime.now().strftime("%d/%m/%Y %H:%M")),
        )
        self.conn.commit()

    def remover_equipamento_evento(self, evento_id, equip_id):
        self.cursor.execute(
            "DELETE FROM movimentacoes WHERE evento_id=? AND equipamento_id=? AND tipo='saida'",
            (evento_id, equip_id),
        )
        self.cursor.execute(
            "DELETE FROM movimentacoes WHERE evento_id=? AND equipamento_id=? AND tipo='retorno'",
            (evento_id, equip_id),
        )
        self.conn.commit()

    def devolvidos_retorno(self, evento_id) -> list:
        self.cursor.execute(
            """SELECT eq.codigo_barras, eq.nome, r.data_movimentacao
               FROM movimentacoes r
               JOIN equipamentos eq ON r.equipamento_id = eq.id
               WHERE r.evento_id=? AND r.tipo='retorno'
               ORDER BY r.id""",
            (evento_id,),
        )
        return self.cursor.fetchall()

    def contar_pendentes(self, evento_id) -> int:
        self.cursor.execute(
            """SELECT COUNT(*) FROM movimentacoes
               WHERE evento_id=? AND tipo='saida'
                 AND NOT EXISTS (
                     SELECT 1 FROM movimentacoes r
                     WHERE r.evento_id=movimentacoes.evento_id
                       AND r.equipamento_id=movimentacoes.equipamento_id
                       AND r.tipo='retorno')""",
            (evento_id,),
        )
        return self.cursor.fetchone()[0]

    # ── Notas ─────────────────────────────────────────────────────────────

    def obter_notas(self, evento_id) -> str:
        self.cursor.execute("SELECT notas FROM eventos WHERE id=?", (evento_id,))
        row = self.cursor.fetchone()
        return (row[0] or "") if row else ""

    def salvar_notas(self, evento_id, conteudo):
        """Salva notas para um evento.
        
        Args:
            evento_id: ID do evento (obrigatório)
            conteudo: Texto das notas
        
        Raises:
            ValueError: Se evento_id for None
        """
        if evento_id is None:
            raise ValueError("evento_id não pode ser None")
        
        self.cursor.execute("UPDATE eventos SET notas=? WHERE id=?", (conteudo, evento_id))
        self.conn.commit()

    # ── Localização de equipamento ────────────────────────────────────────

    def localizar_equipamento(self, codigo) -> dict | None:
        """Busca onde o equipamento está (evento agendado ou manutenção)."""
        equip_id = self.obter_id_por_codigo(codigo)
        if equip_id is None:
            return None

        if self.esta_em_manutencao(equip_id):
            return {"tipo": "manutencao", "codigo": codigo}

        self.cursor.execute(
            """SELECT ev.nome_evento, ev.data_inicio, ev.local_evento,
                      ev.status, eq.nome, eq.codigo_barras, eq.descricao
               FROM movimentacoes m
               JOIN eventos ev ON m.evento_id = ev.id
               JOIN equipamentos eq ON m.equipamento_id = eq.id
               WHERE eq.codigo_barras=? AND m.tipo='saida'
               ORDER BY m.id DESC LIMIT 1""",
            (codigo,),
        )
        row = self.cursor.fetchone()
        if not row or row[3].lower() != "agendado":
            return None

        return {
            "tipo": "evento",
            "nome_evento": row[0],
            "data_inicio": row[1],
            "local_evento": row[2],
            "nome_equip": row[4],
            "codigo": row[5],
            "descricao": row[6],
        }

    # ── Relatórios ─────────────────────────────────────────────────────────

    def eventos_em_andamento(self) -> list:
        """Retorna (id, nome_evento, responsavel, data_inicio, data_fim) agendados."""
        self.cursor.execute(
            "SELECT id, nome_evento, responsavel, data_inicio, data_fim "
            "FROM eventos WHERE status='Agendado'"
        )
        return self.cursor.fetchall()

    def eventos_agendados_resumo(self) -> list:
        """Retorna (id, nome_evento) de eventos agendados ordenados por nome."""
        self.cursor.execute(
            "SELECT id, nome_evento FROM eventos WHERE status='Agendado' ORDER BY nome_evento"
        )
        return self.cursor.fetchall()

    def detalhes_evento_relatorio(self, evento_id) -> dict:
        self.cursor.execute(
            "SELECT nome_evento, responsavel, data_inicio, data_fim "
            "FROM eventos WHERE id=?", (evento_id,),
        )
        row = self.cursor.fetchone()
        if not row:
            raise ValueError(f"Evento {evento_id} não encontrado")
        return {"nome_evento": row[0], "responsavel": row[1],
                "data_inicio": row[2], "data_fim": row[3]}

    def equipamentos_evento_relatorio(self, evento_id) -> list:
        """Retorna [(codigo_barras, nome), ...] de saídas do evento."""
        self.cursor.execute(
            "SELECT eq.codigo_barras, eq.nome "
            "FROM movimentacoes m JOIN equipamentos eq ON m.equipamento_id=eq.id "
            "WHERE m.evento_id=? AND m.tipo='saida'",
            (evento_id,),
        )
        return self.cursor.fetchall()

    def todos_equipamentos_completos(self) -> list:
        """Retorna (id, codigo, nome, descricao, localizacao, data) completos."""
        self.cursor.execute(
            "SELECT id, codigo_barras, nome, descricao, localizacao, data_cadastro "
            "FROM equipamentos ORDER BY id"
        )
        return self.cursor.fetchall()

    def equipamentos_em_manutencao(self) -> list:
        """Retorna [(codigo, nome, local_manutencao, data_saida), ...]."""
        self.cursor.execute(
            "SELECT e.codigo_barras, e.nome, m.local_manutencao, m.data_saida "
            "FROM manutencao m JOIN equipamentos e ON m.equipamento_id=e.id "
            "WHERE m.status='Em manutenção'"
        )
        return self.cursor.fetchall()

    def eventos_detalhados_com_equip(self) -> list:
        """Retorna [(id, nome, resp, inicio, fim, equips_concat), ...] agendados."""
        if self.backend == "postgres":
            self.cursor.execute(
                "SELECT e.id, e.nome_evento, e.responsavel, e.data_inicio, e.data_fim,"
                " COALESCE(STRING_AGG(eq.nome || ' (' || eq.codigo_barras || ')', ', '), '')"
                " FROM eventos e"
                " LEFT JOIN movimentacoes m ON e.id=m.evento_id"
                " LEFT JOIN equipamentos eq ON m.equipamento_id=eq.id"
                " WHERE e.status='Agendado' GROUP BY e.id"
            )
        else:
            self.cursor.execute(
                "SELECT e.id, e.nome_evento, e.responsavel, e.data_inicio, e.data_fim,"
                " GROUP_CONCAT(eq.nome || ' (' || eq.codigo_barras || ')', ', ')"
                " FROM eventos e"
                " LEFT JOIN movimentacoes m ON e.id=m.evento_id"
                " LEFT JOIN equipamentos eq ON m.equipamento_id=eq.id"
                " WHERE e.status='Agendado' GROUP BY e.id"
            )
        return self.cursor.fetchall()

    # ── Auditoria ─────────────────────────────────────────────────────────

    def listar_auditoria(self, limite=500, offset=0) -> list:
        """Lista eventos de auditoria com limite e paginação.
        
        Args:
            limite: Máximo de registros a retornar (capped a 1000)
            offset: Começar a partir do índice
        """
        # Cap no limite para evitar DoS
        if limite > 1000:
            limite = 1000
        if offset < 0:
            offset = 0
        
        self.cursor.execute(
            "SELECT * FROM auditoria ORDER BY id DESC LIMIT ? OFFSET ?", 
            (limite, offset),
        )
        return self.cursor.fetchall()

    def registrar_auditoria(self, tabela, item_id, acao, detalhes=""):
        self.cursor.execute(
            """INSERT INTO auditoria (tabela, item_id, acao, detalhes, data)
               VALUES (?, ?, ?, ?, ?)""",
            (tabela, item_id, acao.upper(), detalhes,
             datetime.now().strftime("%d/%m/%Y %H:%M:%S")),
        )
        self.conn.commit()

    # ── Ciclo de vida ─────────────────────────────────────────────────────

    def fechar(self):
        try:
            self.conn.commit()
            self.conn.close()
        except Exception:
            pass
