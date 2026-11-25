"""
Camada de acesso a dados — isola TODO o SQL do resto da aplicação.
Reutiliza o mesmo seaghb.db da versão Tkinter.
"""

import logging
import os
import sys
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .conexao_sqlite_protegida import FechamentoPendente
from .datas_eventos import ErroPeriodoEvento, validar_periodo_evento

try:
    import psycopg  # type: ignore
except ImportError:
    psycopg = None


class ErroMovimentacao(ValueError):
    """Operação recusada por uma regra de estoque, sem gravar alterações."""

    def __init__(self, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.codigo = codigo


@dataclass(frozen=True)
class ResultadoRetorno:
    registrado: bool
    evento_concluido: bool


@dataclass
class _EstadoEquipamento:
    id: int
    codigo: str
    nome: str
    descricao: str | None
    localizacao: str | None
    cadastro: str
    manutencoes: int
    movimentos: dict[int, tuple] = field(default_factory=dict)

    def validar_integridade(self) -> None:
        pendentes = 0
        for saidas, retornos, invalidos, status, *_ in self.movimentos.values():
            if (invalidos or saidas != 1 or retornos not in (0, 1)
                    or status not in ("Agendado", "Concluído")
                    or (not retornos and status != "Agendado")):
                break
            pendentes += not retornos
        else:
            if pendentes + self.manutencoes <= 1:
                return
        raise ErroMovimentacao(
            "estado_legado_inconsistente",
            "Há registros de saída, retorno ou manutenção inconsistentes para "
            "este equipamento. Revise o histórico antes de movimentá-lo.",
        )

    def saida_pendente(self) -> int | None:
        return next((evento for evento, mov in self.movimentos.items() if not mov[1]), None)

    def exigir_disponivel(self) -> None:
        if self.manutencoes:
            raise ErroMovimentacao("equipamento_indisponivel", "O equipamento está em manutenção.")
        if self.saida_pendente() is not None:
            raise ErroMovimentacao(
                "equipamento_indisponivel", "O equipamento tem uma saída pendente de retorno."
            )


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


class _AcessoSQLiteProtegido:
    """Adapta apenas os resultados e as operações usadas por Database."""

    def __init__(self, sessao):
        self._sessao = sessao
        self._linhas = iter(())
        self._lastrowid = None
        self._recuperacao_falhou = False

    def _operar(self, acao, *args):
        self._sessao.verificar_disponivel()
        self._linhas = iter(())
        self._lastrowid = None
        try:
            return acao(*args)
        except BaseException as erro:
            try:
                self._reverter_ou_fechar()
            except BaseException as falha:
                if isinstance(erro, Exception):
                    raise  # Mantém o contrato de erro encadeado do adaptador protegido.
                erro.add_note(f"Recuperação SQLite protegida falhou ({type(falha).__name__}).")
            raise

    def execute(self, query, params=None):
        resultado = self._operar(self._sessao.executar_resultado, query,
                                 () if params is None else params)
        self._linhas = iter(resultado.linhas)
        self._lastrowid = resultado.lastrowid

    def executescript(self, script):
        self._operar(self._sessao.executar_script_inicializacao, script)

    @property
    def lastrowid(self):
        self._sessao.verificar_disponivel()
        return self._lastrowid

    def fetchone(self):
        self._sessao.verificar_disponivel()
        return next(self._linhas, None)

    def fetchall(self):
        self._sessao.verificar_disponivel()
        return list(self._linhas)

    def commit(self):
        self._operar(self._sessao.commit)

    def recuperar(self):
        if not self._recuperacao_falhou:
            self._sessao.verificar_disponivel()
            self._reverter_ou_fechar()

    def _reverter_ou_fechar(self):
        """Reverte uma operação falha ou encerra a sessão se a reversão falhar."""
        try:
            self._sessao.rollback()
        except BaseException:
            self._recuperacao_falhou = True
            self.close()
            raise

    def close(self):
        self._sessao.fechar()


class _AcessoLegadoBloqueado:
    def __getattr__(self, atributo):
        raise RuntimeError("Conexão indisponível após falha de rollback; feche e reabra o banco.")


_ACESSO_LEGADO_BLOQUEADO = _AcessoLegadoBloqueado()


class Database:
    """Wrapper de dados com suporte a SQLite e PostgreSQL."""

    def __init__(self, *, banco_sqlite_protegido: str | Path | None = None):
        """Inicializa conexão com banco de dados.

        Um caminho explícito seleciona o SQLite protegido para homologação.
        
        Raises:
            Exception: Se não conseguir conectar ao banco de dados
        """
        self._sqlite_protegido = banco_sqlite_protegido is not None
        self.banco_backup_protegido: Path | None = None
        self._cursor_pendente = None
        self._conexao_pendente = None
        if self._sqlite_protegido:
            self._inicializar_sqlite_protegido(banco_sqlite_protegido)
            return

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
        except BaseException:
            try:
                self.fechar_para_gui(confirmar=False)
            except BaseException as erro_fechamento:
                raise FechamentoPendente(self.fechar_para_gui) from erro_fechamento
            logger.error("Falha ao inicializar banco de dados")
            raise

    def _inicializar_sqlite_protegido(self, banco: str | Path) -> None:
        from .conexao_sqlite_protegida import abrir_conexao_operacional

        sessao = abrir_conexao_operacional(banco=banco)
        try:
            self.backend = "sqlite"
            self.conn = self.cursor = _AcessoSQLiteProtegido(sessao)
            self.cursor.execute("PRAGMA foreign_keys = ON")
            self.cursor.execute("PRAGMA foreign_keys")
            if self.cursor.fetchone() != (1,):
                raise RuntimeError("sqlite_protegido_foreign_keys_indisponivel")
            self.cursor.execute("PRAGMA journal_mode = WAL")
            if self.cursor.fetchone() != ("wal",):
                raise RuntimeError("sqlite_protegido_wal_indisponivel")
            self._criar_tabelas()
            self._atualizar_estrutura()
            self.banco_backup_protegido = sessao.banco
        except BaseException:
            try:
                sessao.fechar()
            except BaseException as erro_fechamento:
                raise FechamentoPendente(sessao.fechar) from erro_fechamento
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
        if self._sqlite_protegido:
            # Serializa a inspeção e as alterações entre inicializações.
            self.cursor.execute("BEGIN IMMEDIATE")
        for col, ddl in [
            ("local_evento", "ALTER TABLE eventos ADD COLUMN local_evento TEXT NOT NULL DEFAULT 'Não especificado'"),
            ("notas",        "ALTER TABLE eventos ADD COLUMN notas TEXT DEFAULT ''"),
        ]:
            if self._coluna_existe("eventos", col):
                continue
            try:
                self.cursor.execute(ddl)
            except Exception:
                if self._sqlite_protegido:
                    raise
        self.conn.commit()

    @contextmanager
    def _transacao_movimentacao(self):
        """Serializa escritas SQLite; no PostgreSQL, use os locks de linha abaixo."""
        if self.conn is _ACESSO_LEGADO_BLOQUEADO:
            raise RuntimeError("Conexão indisponível após falha de rollback; feche e reabra o banco.")
        try:
            if self.backend == "sqlite":
                # Reserva a vez de escrever ANTES de consultar disponibilidade.
                self.cursor.execute("BEGIN IMMEDIATE")
            else:
                # O driver inicia a transação implicitamente; leitores podem já tê-la
                # aberto. A releitura após o lock exige snapshots por instrução.
                self.cursor.execute("SHOW transaction_isolation")
                if self.cursor.fetchone() != ("read committed",):
                    raise ErroMovimentacao(
                        "transacao_isolamento_incompativel",
                        "Esta operação exige isolamento READ COMMITTED no PostgreSQL."
                    )
            yield
            self.conn.commit()
        except BaseException as erro:
            self._reverter_operacao_composta(erro)
            raise

    def _travar_evento(self, evento_id):
        lock = " FOR UPDATE" if self.backend == "postgres" else ""
        self.cursor.execute("SELECT status FROM eventos WHERE id = ?" + lock, (evento_id,))
        row = self.cursor.fetchone()
        if row is None:
            raise ErroMovimentacao("evento_inexistente", "O evento não existe mais.")
        return row[0]

    @staticmethod
    def _exigir_evento_aberto(status):
        if status != "Agendado":
            raise ErroMovimentacao("evento_encerrado", "O evento não está aberto para movimentação.")

    def _travar_equipamento(self, equipamento_id):
        if self.backend != "postgres":
            return  # BEGIN IMMEDIATE já protege a janela de checagem/escrita.
        self.cursor.execute("SELECT id FROM equipamentos WHERE id = ? FOR UPDATE", (equipamento_id,))
        if self.cursor.fetchone() is None:
            raise ErroMovimentacao("equipamento_inexistente", "O equipamento não existe mais.")

    def _consultar_estados(self, *, equipamento_id=None, codigo=None, filtro="") -> dict[int, _EstadoEquipamento]:
        """Uma só leitura para cadastro, movimentos, manutenção e localização."""
        if equipamento_id is not None:
            criterio, params = "WHERE id = ?", (equipamento_id,)
        elif codigo is not None:
            criterio, params = "WHERE codigo_barras = ?", (codigo,)
        elif filtro:
            criterio = "WHERE lower(codigo_barras) LIKE ? OR lower(nome) LIKE ? OR lower(localizacao) LIKE ?"
            params = (f"%{filtro.lower()}%",) * 3
        else:
            criterio, params = "", ()
        self.cursor.execute(
            f"""WITH itens AS (
                    SELECT id, codigo_barras, nome, descricao, localizacao, data_cadastro
                    FROM equipamentos {criterio}
                ), pares AS (
                    SELECT m.equipamento_id, m.evento_id,
                           SUM(CASE WHEN m.tipo = 'saida' THEN 1 ELSE 0 END) AS saidas,
                           SUM(CASE WHEN m.tipo = 'retorno' THEN 1 ELSE 0 END) AS retornos,
                           SUM(CASE WHEN m.tipo IN ('saida', 'retorno') THEN 0 ELSE 1 END)
                           + CASE WHEN MIN(CASE WHEN m.tipo = 'retorno' THEN m.id END)
                                     < MIN(CASE WHEN m.tipo = 'saida' THEN m.id END)
                                  THEN 1 ELSE 0 END AS invalidos
                    FROM movimentacoes m JOIN itens i ON i.id = m.equipamento_id
                    GROUP BY m.equipamento_id, m.evento_id
                ), ativos AS (
                    SELECT m.equipamento_id, COUNT(*) AS total
                    FROM manutencao m JOIN itens i ON i.id = m.equipamento_id
                    WHERE m.status = 'Em manutenção'
                    GROUP BY m.equipamento_id
                )
                SELECT i.id, i.codigo_barras, i.nome, i.descricao, i.localizacao,
                       i.data_cadastro, COALESCE(a.total, 0), p.evento_id, p.saidas,
                       p.retornos, p.invalidos, ev.status, ev.nome_evento,
                       ev.data_inicio, ev.local_evento
                FROM itens i
                LEFT JOIN ativos a ON a.equipamento_id = i.id
                LEFT JOIN pares p ON p.equipamento_id = i.id
                LEFT JOIN eventos ev ON ev.id = p.evento_id
                ORDER BY i.id""",
            params,
        )
        estados = {}
        for linha in self.cursor.fetchall():
            item = estados.get(linha[0])
            if item is None:
                item = estados[linha[0]] = _EstadoEquipamento(*linha[:7])
            if linha[7] is not None:
                item.movimentos[linha[7]] = linha[8:]
        return estados

    def _estado_para_escrita(self, equipamento_id) -> _EstadoEquipamento:
        self._travar_equipamento(equipamento_id)
        estado = self._consultar_estados(equipamento_id=equipamento_id).get(equipamento_id)
        if estado is None:
            raise ErroMovimentacao("equipamento_inexistente", "O equipamento não existe mais.")
        estado.validar_integridade()
        return estado

    def _equipamentos_para_saida(self, equipamentos_ids):
        ids = tuple(equipamentos_ids)
        if len(set(ids)) != len(ids):
            raise ErroMovimentacao("equipamento_duplicado", "O mesmo equipamento consta mais de uma vez.")
        # A ordem canônica evita inversão de locks entre eventos no PostgreSQL.
        for eid in sorted(ids):
            self._estado_para_escrita(eid).exigir_disponivel()
        return ids

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
        validar_periodo_evento(inicio, fim)
        with self._transacao_movimentacao():
            ids = self._equipamentos_para_saida(equipamentos_ids)
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
            for eid in ids:
                self.cursor.execute(
                    """INSERT INTO movimentacoes
                       (evento_id, equipamento_id, data_movimentacao, tipo)
                       VALUES (?, ?, ?, 'saida')""",
                    (evento_id, eid, agora),
                )
            return evento_id

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
        validar_periodo_evento(inicio, fim)
        try:
            self.cursor.execute(
                """UPDATE eventos SET nome_evento=?, local_evento=?,
                   responsavel=?, data_inicio=?, data_fim=? WHERE id=?""",
                (nome, local, resp, inicio, fim, evento_id),
            )
            self.conn.commit()
        except BaseException as erro:
            self._reverter_operacao_composta(erro)
            raise

    def remover_evento(self, evento_id):
        with self._transacao_movimentacao():
            self._travar_evento(evento_id)
            self.cursor.execute(
                "SELECT DISTINCT equipamento_id FROM movimentacoes WHERE evento_id = ? ORDER BY equipamento_id",
                (evento_id,),
            )
            for (equip_id,) in self.cursor.fetchall():
                self._estado_para_escrita(equip_id)
            self.cursor.execute("DELETE FROM eventos WHERE id = ?", (evento_id,))

    def _concluir_evento_validado(self, evento_id):
        self.cursor.execute(
            """SELECT equipamento_id FROM movimentacoes WHERE evento_id = ?
               GROUP BY equipamento_id
               HAVING SUM(CASE WHEN tipo = 'saida' THEN 1 ELSE 0 END) <> 1
                   OR SUM(CASE WHEN tipo = 'retorno' THEN 1 ELSE 0 END) <> 1
                   OR SUM(CASE WHEN tipo IN ('saida', 'retorno') THEN 0 ELSE 1 END) > 0
                   OR MIN(CASE WHEN tipo = 'retorno' THEN id END)
                    < MIN(CASE WHEN tipo = 'saida' THEN id END)""",
            (evento_id,),
        )
        if self.cursor.fetchone() is not None:
            raise ErroMovimentacao(
                "estado_legado_inconsistente",
                "O evento não pode ser concluído enquanto houver movimentações pendentes ou inconsistentes.",
            )
        self.cursor.execute("UPDATE eventos SET status = 'Concluído' WHERE id = ?", (evento_id,))

    def concluir_evento(self, evento_id):
        with self._transacao_movimentacao():
            status = self._travar_evento(evento_id)
            if status != "Concluído":
                self._exigir_evento_aberto(status)
                self._concluir_evento_validado(evento_id)

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
        """Busca o código de barras como texto exato, incluindo zeros à esquerda."""
        self.cursor.execute(
            "SELECT id FROM equipamentos WHERE codigo_barras = ?", (str(codigo).strip(),)
        )
        row = self.cursor.fetchone()
        return row[0] if row else None

    def esta_em_manutencao(self, equipamento_id: int) -> bool:
        estado = self._consultar_estados(equipamento_id=equipamento_id).get(equipamento_id)
        if estado is None:
            return False
        estado.validar_integridade()
        return estado.manutencoes > 0

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

    def registrar_retorno(self, evento_id, codigo_barras) -> ResultadoRetorno:
        with self._transacao_movimentacao():
            status = self._travar_evento(evento_id)
            equip_id = self.obter_id_por_codigo(codigo_barras)
            if equip_id is None:
                raise ErroMovimentacao("equipamento_inexistente", "Código de barras não cadastrado.")
            estado = self._estado_para_escrita(equip_id)
            if estado.codigo != str(codigo_barras).strip():
                raise ErroMovimentacao("equipamento_inexistente", "Código de barras não cadastrado.")
            movimento = estado.movimentos.get(evento_id)
            if movimento is None:
                raise ErroMovimentacao("saida_nao_pendente", "Este código não possui saída neste evento.")
            if movimento[1]:
                return ResultadoRetorno(False, status == "Concluído")
            self._exigir_evento_aberto(status)
            agora = datetime.now().strftime("%d/%m/%Y %H:%M")
            self.cursor.execute(
                """INSERT INTO movimentacoes (evento_id, equipamento_id, data_movimentacao, tipo)
                   VALUES (?, ?, ?, 'retorno')""",
                (evento_id, equip_id, agora),
            )
            concluido = self.contar_pendentes(evento_id) == 0
            if concluido:
                self._concluir_evento_validado(evento_id)
            return ResultadoRetorno(True, concluido)

    # ── Eventos atrasados ─────────────────────────────────────────────────

    def eventos_atrasados(self) -> list:
        """Retorna somente atrasos com período válido e saída pendente."""
        atrasados, _ = self.classificar_eventos_com_pendencias()
        return atrasados

    def classificar_eventos_com_pendencias(self) -> tuple[list, list]:
        """Retorna atrasos confirmados e períodos ilegíveis em listas separadas."""
        self.cursor.execute("""
            SELECT e.id, e.nome_evento, e.data_fim, COUNT(*), e.data_inicio
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
            GROUP BY e.id, e.nome_evento, e.data_fim, e.data_inicio
            ORDER BY e.id
        """)
        agora = datetime.now()
        atrasados, invalidos = [], []
        for *resumo, inicio in self.cursor.fetchall():
            linha = tuple(resumo)
            try:
                periodo = validar_periodo_evento(inicio, linha[2])
            except ErroPeriodoEvento:
                invalidos.append(linha)
                continue
            if periodo.vencido_em(agora):
                atrasados.append(linha)
        return atrasados, invalidos

    # ── Auditoria ─────────────────────────────────────────────────────────

    # ── Equipamentos – extras ────────────────────────────────────────────

    def editar_equipamento(self, equip_id, nome, descricao, localizacao):
        self.cursor.execute(
            "UPDATE equipamentos SET nome=?, descricao=?, localizacao=? WHERE id=?",
            (nome, descricao, localizacao, equip_id),
        )
        self.conn.commit()

    def remover_equipamento(self, equip_id):
        """Preserva a política legada de exclusão para itens não ambíguos."""
        with self._transacao_movimentacao():
            self._estado_para_escrita(equip_id)
            self.cursor.execute("DELETE FROM movimentacoes WHERE equipamento_id=?", (equip_id,))
            self.cursor.execute("DELETE FROM manutencao WHERE equipamento_id=?", (equip_id,))
            self.cursor.execute("DELETE FROM equipamentos WHERE id=?", (equip_id,))

    def obter_equipamento(self, equip_id) -> tuple | None:
        self.cursor.execute(
            "SELECT id, codigo_barras, nome, descricao, localizacao, data_cadastro "
            "FROM equipamentos WHERE id=?", (equip_id,),
        )
        return self.cursor.fetchone()

    def listar_equipamentos_disponiveis(self, filtro="") -> list:
        """Só inclui itens com disponibilidade comprovada e sem vínculo ativo."""
        disponiveis = []
        for estado in self._consultar_estados(filtro=filtro).values():
            try:
                estado.validar_integridade()
                estado.exigir_disponivel()
            except ErroMovimentacao:
                continue
            disponiveis.append((estado.id, estado.codigo, estado.nome, estado.localizacao, estado.cadastro))
        return disponiveis

    def verificar_pendencia_retorno(self, equip_id) -> tuple | None:
        estado = self._consultar_estados(equipamento_id=equip_id).get(equip_id)
        if estado is None:
            return None
        estado.validar_integridade()
        evento = estado.saida_pendente()
        return ("saida", evento) if evento is not None else None

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
        with self._transacao_movimentacao():
            self._estado_para_escrita(equip_id).exigir_disponivel()
            self.cursor.execute(
                """INSERT INTO manutencao
                   (equipamento_id, data_saida, local_manutencao, status)
                   VALUES (?, ?, ?, 'Em manutenção')""",
                (equip_id, datetime.now().strftime("%d/%m/%Y %H:%M"), local_manutencao),
            )

    def registrar_retorno_manutencao(self, manutencao_id):
        with self._transacao_movimentacao():
            self.cursor.execute("SELECT equipamento_id FROM manutencao WHERE id = ?", (manutencao_id,))
            row = self.cursor.fetchone()
            if row is None:
                raise ErroMovimentacao("manutencao_nao_pendente", "A manutenção não existe mais.")
            self._estado_para_escrita(row[0])
            self.cursor.execute("SELECT status FROM manutencao WHERE id = ?", (manutencao_id,))
            row = self.cursor.fetchone()
            if row is None or row[0] != "Em manutenção":
                raise ErroMovimentacao("manutencao_nao_pendente", "A manutenção não está pendente.")
            self.cursor.execute(
                "UPDATE manutencao SET status='Concluído' WHERE id=?", (manutencao_id,),
            )

    def obter_status_manutencao(self, manutencao_id) -> str | None:
        self.cursor.execute("SELECT status FROM manutencao WHERE id=?", (manutencao_id,))
        row = self.cursor.fetchone()
        return row[0] if row else None

    # ── Movimentações – edição de evento ──────────────────────────────────

    def adicionar_equipamento_evento(self, evento_id, equip_id):
        with self._transacao_movimentacao():
            self._exigir_evento_aberto(self._travar_evento(evento_id))
            estado = self._estado_para_escrita(equip_id)
            if evento_id in estado.movimentos:
                raise ErroMovimentacao(
                    "equipamento_duplicado", "O equipamento já tem movimentação neste evento."
                )
            estado.exigir_disponivel()
            self.cursor.execute(
                """INSERT INTO movimentacoes
                   (evento_id, equipamento_id, data_movimentacao, tipo)
                   VALUES (?, ?, ?, 'saida')""",
                (evento_id, equip_id, datetime.now().strftime("%d/%m/%Y %H:%M")),
            )

    def remover_equipamento_evento(self, evento_id, equip_id):
        """Edição explícita legada; mantém as remoções dentro de uma transação."""
        with self._transacao_movimentacao():
            self._travar_evento(evento_id)
            estado = self._estado_para_escrita(equip_id)
            if evento_id not in estado.movimentos:
                raise ErroMovimentacao("saida_nao_pendente", "O equipamento não consta neste evento.")
            self.cursor.execute(
                "DELETE FROM movimentacoes WHERE evento_id=? AND equipamento_id=? AND tipo='saida'",
                (evento_id, equip_id),
            )
            self.cursor.execute(
                "DELETE FROM movimentacoes WHERE evento_id=? AND equipamento_id=? AND tipo='retorno'",
                (evento_id, equip_id),
            )

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
        """Localiza apenas um vínculo ativo; não confunde o histórico com posição atual."""
        estados = self._consultar_estados(codigo=str(codigo).strip())
        estado = next(iter(estados.values()), None)
        if estado is None:
            return None
        estado.validar_integridade()
        if estado.manutencoes:
            return {"tipo": "manutencao", "codigo": estado.codigo}
        evento = estado.saida_pendente()
        if evento is None:
            return None
        _, _, _, _, nome_evento, inicio, local_evento = estado.movimentos[evento]
        return {
            "tipo": "evento",
            "nome_evento": nome_evento,
            "data_inicio": inicio,
            "local_evento": local_evento,
            "nome_equip": estado.nome,
            "codigo": estado.codigo,
            "descricao": estado.descricao,
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

    def _reverter_operacao_composta(self, erro: BaseException) -> None:
        if self._sqlite_protegido:
            try:
                self.conn.recuperar()
            except BaseException as falha:
                erro.add_note(f"Recuperação SQLite protegida falhou ({type(falha).__name__}).")
            return

        try:
            self.conn.rollback()
        except BaseException as falha:
            erro.add_note(f"Rollback falhou; conexão bloqueada ({type(falha).__name__}).")
            # Retira imediatamente os recursos dos caminhos normais de escrita.
            # Se o close falhar, só o fechamento pode acessar os objetos pendentes.
            self._cursor_pendente, self._conexao_pendente = self.cursor, self.conn
            self.cursor = self.conn = _ACESSO_LEGADO_BLOQUEADO
            try:
                self.fechar_para_gui(confirmar=False)
            except BaseException as fechamento:
                erro.add_note(f"Fechamento pendente ({type(fechamento).__name__}); tente fechar novamente.")

    def fechar(self):
        if self._sqlite_protegido:
            self.conn.close()  # Mantém a concessão se o fechamento falhar.
            return
        self.fechar_para_gui(confirmar=False)

    def fechar_para_gui(self, *, confirmar=True):
        """Libera recursos sem confirmar escritas, mesmo em uma nova tentativa.

        O argumento confirmar é preservado para chamadas existentes. Os métodos
        de escrita são responsáveis por seu próprio commit.
        """
        if self._sqlite_protegido:
            self.fechar()
            return

        if self.conn is not _ACESSO_LEGADO_BLOQUEADO:
            if self.cursor is not None:
                self._cursor_pendente, self.cursor = self.cursor, None
            if self.conn is not None:
                self._conexao_pendente, self.conn = self.conn, None

        erro_cursor = None
        if self._cursor_pendente is not None:
            try:
                self._cursor_pendente.close()
            except BaseException as erro:
                erro_cursor = erro
            else:
                self._cursor_pendente = None

        if self._conexao_pendente is not None:
            try:
                self._conexao_pendente.close()
            except BaseException as erro:
                if erro_cursor is not None:
                    raise erro from erro_cursor
                raise
            else:
                self._conexao_pendente = self._cursor_pendente = None
        if erro_cursor is not None:
            raise erro_cursor
