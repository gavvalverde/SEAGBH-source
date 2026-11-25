"""Integração explícita de Database com SQLite temporário e barreira real."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from src.core import conexao_sqlite_protegida as protegido
from src.core import database
from src.core.barreira_sqlite import adquirir_manutencao


_SONDA = """
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

from src.core.barreira_sqlite import ErroBarreira, adquirir_manutencao
from src.core.database import Database

banco = Path(sys.argv[1])
acao = sys.argv[2]
try:
    if acao == "manutencao":
        with adquirir_manutencao(backend="sqlite", banco=banco, prazo=0):
            resultado = "livre"
    elif acao == "abrir":
        with patch.object(sqlite3, "connect", side_effect=AssertionError("connect antes do lock")):
            db = Database(banco_sqlite_protegido=banco)
            db.fechar()
        resultado = "aberto"
    else:
        with closing(sqlite3.connect(banco, timeout=0)) as conexao:
            with closing(conexao.execute("PRAGMA wal_checkpoint(TRUNCATE)")) as cursor:
                resultado = cursor.fetchone()
except ErroBarreira as erro:
    resultado = erro.codigo
print(json.dumps(resultado))
"""


class DatabaseSQLiteProtegidoTests(unittest.TestCase):
    def setUp(self):
        temporario = tempfile.TemporaryDirectory(prefix="seagbh-database-protegido-")
        self.addCleanup(temporario.cleanup)
        self.addCleanup(self.assertEqual, protegido._SESSOES_ABERTAS,
                        protegido._SESSOES_ABERTAS.copy())
        self.banco = Path(temporario.name).resolve() / "sintetico.db"
        self.conectar = sqlite3.connect

    def abrir(self):
        db = database.Database(banco_sqlite_protegido=self.banco)
        self.addCleanup(db.fechar)
        return db

    def sondar(self, acao="manutencao"):
        resultado = subprocess.run(
            [sys.executable, "-B", "-W", "error::ResourceWarning", "-c",
             _SONDA, str(self.banco), acao],
            capture_output=True, text=True, timeout=15, check=True,
        )
        self.assertEqual(resultado.stderr, "")
        return json.loads(resultado.stdout)

    def injetar_conexao(self, tipo):
        def conectar(caminho, **opcoes):
            self.assertEqual(caminho, self.banco)
            self.assertTrue(opcoes.get("check_same_thread", True))
            return self.conectar(caminho, factory=tipo, **opcoes)
        return patch.object(protegido.sqlite3, "connect", side_effect=conectar)

    def query_externa(self, sql):
        with closing(self.conectar(self.banco)) as conexao:
            with closing(conexao.execute(sql)) as cursor:
                return cursor.fetchall()

    def criar_legado(self):
        with closing(self.conectar(self.banco)) as conexao:
            with closing(conexao.executescript("""
                CREATE TABLE eventos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome_evento TEXT NOT NULL, responsavel TEXT NOT NULL,
                    data_inicio TEXT NOT NULL, data_fim TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Agendado'
                );
                INSERT INTO eventos VALUES (40, 'Legado', 'Equipe', '01/01/2000',
                                             '02/01/2000', 'Agendado');
            """)):
                pass

    def criar_evento(self, db, itens=()):
        return db.criar_evento("Evento", "Sala", "Equipe", "01/01/2000", "02/01/2000", itens)

    def test_novo_com_pragmas_crud_e_ids_sem_configuracao_pessoal(self):
        with patch.object(database, "_load_env_file") as carregar, \
             patch.object(database, "get_db_path") as caminho:
            db = self.abrir()
        carregar.assert_not_called()
        caminho.assert_not_called()
        self.assertEqual(db.backend, "sqlite")
        self.assertFalse(self.banco.with_suffix(".db.lock").exists())
        db.cursor.execute("PRAGMA foreign_keys")
        self.assertEqual(db.cursor.fetchone(), (1,))
        db.cursor.execute("PRAGMA journal_mode")
        self.assertEqual(db.cursor.fetchall(), [("wal",)])
        for nome in ("cursor", "connection", "_cursor", "backup", "blobopen"):
            self.assertFalse(hasattr(db.cursor, nome), nome)

        db.cadastrar_equipamento("EQ1", "Microfone", "", "Sala")
        equipamento = db.obter_id_por_codigo("EQ1")
        db.editar_equipamento(equipamento, "Microfone 2", "Seguro", "Sala B")
        self.assertEqual(db.obter_nome_equipamento(equipamento), "Microfone 2")
        db.cadastrar_equipamento("EQ2", "Mesa", "", "Sala")
        segundo_item = db.obter_id_por_codigo("EQ2")
        db.cadastrar_equipamento("EQ3", "Cabo", "", "Sala")
        terceiro_item = db.obter_id_por_codigo("EQ3")
        primeiro = self.criar_evento(db, [equipamento, segundo_item])
        segundo = self.criar_evento(db, [terceiro_item])
        self.assertEqual((primeiro, segundo), (1, 2))
        self.assertEqual(db.contar_pendentes(segundo), 1)
        self.assertEqual(db.contar_pendentes(primeiro), 2)
        self.assertEqual(self.query_externa(
            "SELECT evento_id, equipamento_id FROM movimentacoes ORDER BY id"),
            [(primeiro, equipamento), (primeiro, segundo_item), (segundo, terceiro_item)],
        )
        db.salvar_notas(segundo, "nota")
        self.assertEqual(db.obter_notas(segundo), "nota")
        db.remover_evento(primeiro)
        self.assertIsNone(db.obter_evento(primeiro))
        db.remover_equipamento(equipamento)
        self.assertIsNone(db.obter_equipamento(equipamento))
        self.assertEqual([e[0] for e in db.listar_eventos()], [segundo])

    def test_legado_migra_colunas_preserva_registro_e_sequencia(self):
        self.criar_legado()
        db = self.abrir()
        existente = db.obter_evento(40)
        self.assertEqual(existente["local_evento"], "Não especificado")
        self.assertEqual(existente["notas"], "")
        self.assertEqual(self.criar_evento(db), 41)
        db.fechar()
        db = self.abrir()
        self.assertEqual([e[0] for e in db.listar_eventos()], [41, 40])

    def test_ddl_legado_e_protegido_criam_mesmo_esquema(self):
        db = self.abrir()
        db.fechar()
        sql = "SELECT name, sql FROM sqlite_schema ORDER BY name"
        esperado = self.query_externa(sql)
        self.banco = self.banco.with_name("padrao.db")
        with patch.object(database, "_load_env_file") as carregar, \
             patch.object(database, "get_db_path", return_value=str(self.banco)), \
             patch.dict(os.environ, {"SEAGBH_DB_ENGINE": "sqlite"}, clear=True), \
             patch.object(protegido, "abrir_conexao_operacional") as fabrica:
            padrao = database.Database()
        self.addCleanup(padrao.fechar)
        carregar.assert_called_once_with()
        fabrica.assert_not_called()
        self.assertIsInstance(padrao.conn, sqlite3.Connection)
        self.assertTrue(self.banco.with_suffix(".db.lock").exists())
        self.assertEqual(self.query_externa(sql), esperado)

    def test_segundo_alter_falho_reverte_migracao_e_libera(self):
        self.criar_legado()

        class CursorFalho(sqlite3.Cursor):
            def execute(self, sql, params=()):
                if sql.startswith("ALTER TABLE eventos ADD COLUMN notas"):
                    raise sqlite3.OperationalError("alter sintético")
                return super().execute(sql, params)

        class Conexao(sqlite3.Connection):
            def cursor(self):
                return super().cursor(factory=CursorFalho)

        with self.injetar_conexao(Conexao):
            with self.assertRaisesRegex(sqlite3.OperationalError, "alter sintético"):
                self.abrir()
        colunas = {row[1] for row in self.query_externa("PRAGMA table_info(eventos)")}
        self.assertFalse({"local_evento", "notas"} & colunas)
        self.assertEqual(self.query_externa("SELECT id FROM eventos"), [(40,)])
        self.assertEqual(self.sondar(), "livre")

    def test_rollback_evento_com_falha_sqlite_ou_iterador_python(self):
        db = self.abrir()
        db.cadastrar_equipamento("EQ1", "Mesa", "", "")
        equipamento = db.obter_id_por_codigo("EQ1")

        def itens():
            yield equipamento
            raise ValueError("iterador sintético")

        db.cadastrar_equipamento("EQ2", "Cabo", "", "")
        segundo_item = db.obter_id_por_codigo("EQ2")
        db.cursor.execute(f"""
            CREATE TRIGGER impedir_segunda_saida BEFORE INSERT ON movimentacoes
            WHEN NEW.equipamento_id = {segundo_item} AND EXISTS (
                SELECT 1 FROM movimentacoes WHERE evento_id = NEW.evento_id
                AND equipamento_id = {equipamento} AND tipo = 'saida'
            )
            BEGIN SELECT RAISE(ABORT, 'falha sintética após a primeira saída'); END
        """)
        for valores, erro in (([equipamento, segundo_item], sqlite3.IntegrityError),
                              (itens(), ValueError)):
            with self.subTest(erro=erro.__name__), self.assertRaises(erro):
                self.criar_evento(db, valores)
            self.assertEqual(db.listar_eventos(), [])
            self.assertEqual(self.query_externa("SELECT id FROM movimentacoes"), [])
        self.assertIsInstance(self.criar_evento(db, [equipamento]), int)

    def test_falha_na_escrita_composta_implicita_reverte_primeiro_delete(self):
        db = self.abrir()
        db.cadastrar_equipamento("EQ1", "Mesa", "", "")
        equipamento = db.obter_id_por_codigo("EQ1")
        evento = self.criar_evento(db, [equipamento])
        db.registrar_retorno(evento, "EQ1")
        db.cursor.execute("""
            CREATE TRIGGER impedir_retorno BEFORE DELETE ON movimentacoes
            WHEN OLD.tipo='retorno' BEGIN SELECT RAISE(ABORT, 'falha sintética'); END
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            db.remover_equipamento_evento(evento, equipamento)
        self.assertEqual(self.query_externa("SELECT tipo FROM movimentacoes ORDER BY id"),
                         [("saida",), ("retorno",)])
        self.assertEqual(db.contar_pendentes(evento), 0)

    def test_falha_connect_acontece_sob_lock_e_libera_sem_fallback(self):
        def falhar(*args, **kwargs):
            self.assertEqual(self.sondar(), "ocupado")
            raise sqlite3.OperationalError("connect sintético")
        with patch.object(protegido.sqlite3, "connect", side_effect=falhar) as conectar:
            with self.assertRaisesRegex(sqlite3.OperationalError, "connect sintético"):
                self.abrir()
        conectar.assert_called_once()
        self.assertFalse(self.banco.exists())
        self.assertEqual(self.sondar(), "livre")

    def test_falhas_de_inicializacao_fecham_recursos_antes_de_liberar(self):
        teste = self
        for etapa in ("cursor", "pragma", "ddl", "commit"):
            with self.subTest(etapa=etapa):
                fechamentos = []
                self.banco = self.banco.with_name(etapa + ".db")

                class CursorFalho(sqlite3.Cursor):
                    def execute(self, sql, params=()):
                        if etapa == "pragma" and sql == "PRAGMA journal_mode = WAL":
                            raise sqlite3.OperationalError(etapa)
                        return super().execute(sql, params)

                    def executescript(self, sql):
                        if etapa == "ddl":
                            raise sqlite3.OperationalError(etapa)
                        return super().executescript(sql)

                class Conexao(sqlite3.Connection):
                    def cursor(self):
                        if etapa == "cursor":
                            raise sqlite3.OperationalError(etapa)
                        return super().cursor(factory=CursorFalho)

                    def commit(self):
                        if etapa == "commit":
                            raise sqlite3.OperationalError(etapa)
                        super().commit()

                    def close(self):
                        fechamentos.append(teste.sondar())
                        super().close()

                with self.injetar_conexao(Conexao):
                    with self.assertRaisesRegex(sqlite3.OperationalError, etapa):
                        self.abrir()
                self.assertEqual(fechamentos, ["ocupado"])
                self.assertEqual(self.sondar(), "livre")

    def test_commit_falho_reverte_escrita_e_close_nao_confirma_pendentes(self):
        class Conexao(sqlite3.Connection):
            falhar_commit = False

            def commit(self):
                if self.falhar_commit:
                    raise sqlite3.OperationalError("commit sintético")
                super().commit()

        with self.injetar_conexao(Conexao):
            db = self.abrir()
        Conexao.falhar_commit = True
        with self.assertRaisesRegex(sqlite3.OperationalError, "commit sintético"):
            db.cadastrar_equipamento("EQ1", "Mesa", "", "")
        self.assertIsNone(db.obter_id_por_codigo("EQ1"))
        self.assertEqual(self.sondar(), "ocupado")
        db.cursor.execute("INSERT INTO equipamentos (codigo_barras, nome, data_cadastro) "
                          "VALUES ('PENDENTE', 'Mesa', '01/01/2000')")
        db.fechar()
        self.assertEqual(self.query_externa("SELECT id FROM equipamentos"), [])
        self.assertEqual(self.sondar(), "livre")

    def test_rollback_e_close_falhos_invalidam_acesso_e_retem_lock(self):
        class Conexao(sqlite3.Connection):
            falhar = False

            def commit(self):
                if self.falhar:
                    raise sqlite3.OperationalError("commit sintético")
                super().commit()

            def rollback(self):
                if self.falhar:
                    raise sqlite3.OperationalError("rollback sintético")
                super().rollback()

            def close(self):
                if self.falhar:
                    raise sqlite3.OperationalError("close sintético")
                super().close()

        with self.injetar_conexao(Conexao):
            db = self.abrir()
        Conexao.falhar = True
        try:
            with self.assertRaisesRegex(sqlite3.OperationalError, "close sintético") as erro:
                db.cadastrar_equipamento("EQ1", "Mesa", "", "")
            self.assertEqual(str(erro.exception.__context__), "rollback sintético")
            self.assertEqual(str(erro.exception.__context__.__context__), "commit sintético")
            self.assertEqual(self.sondar(), "ocupado")
            with self.assertRaisesRegex(RuntimeError, "conexao_protegida_encerrada"):
                db.listar_equipamentos()
        finally:
            Conexao.falhar = False
        db.fechar()
        self.assertEqual(self.query_externa("SELECT id FROM equipamentos"), [])
        self.assertEqual(self.sondar(), "livre")

    def test_resultados_destacados_nao_retem_leitura_wal_ate_fechar(self):
        db = self.abrir()
        self.criar_evento(db)
        self.criar_evento(db)
        db.cursor.execute("SELECT id FROM eventos ORDER BY id")
        self.assertEqual(db.cursor.fetchone(), (1,))
        self.assertEqual(self.sondar("checkpoint"), [0, 0, 0])
        self.assertEqual(self.sondar(), "ocupado")
        self.assertEqual(db.cursor.fetchall(), [(2,)])
        db.fechar()
        db.fechar()
        self.assertEqual(self.sondar(), "livre")
        for comando in (db.cursor.fetchone, db.listar_eventos, db.conn.commit):
            with self.assertRaisesRegex(RuntimeError, "conexao_protegida_encerrada"):
                comando()

    def test_manutencao_exclusiva_impede_connect_em_outro_processo(self):
        with adquirir_manutencao(backend="sqlite", banco=self.banco):
            self.assertEqual(self.sondar("abrir"), "ocupado")
            self.assertFalse(self.banco.exists())
            self.assertFalse(self.banco.with_suffix(".db.lock").exists())

    def test_outra_thread_nao_executa_nem_invalida_a_sessao(self):
        db = self.abrir()
        erros = []

        def usar():
            for comando in (lambda: self.criar_evento(db), db.fechar, db.cursor.fetchall):
                try:
                    comando()
                except RuntimeError as erro:
                    erros.append(str(erro))

        thread = threading.Thread(target=usar)
        thread.start()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(erros, ["conexao_protegida_thread_incorreta"] * 3)
        self.assertEqual(self.sondar(), "ocupado")
        db.cadastrar_equipamento("EQ1", "Mesa", "", "")
        equipamento = db.obter_id_por_codigo("EQ1")
        with self.assertRaises(database.ErroMovimentacao) as erro:
            self.criar_evento(db, [equipamento, -1])
        self.assertEqual(erro.exception.codigo, "equipamento_inexistente")
        self.assertEqual(db.listar_eventos(), [])
        self.assertEqual(self.query_externa("SELECT id FROM movimentacoes"), [])
        self.assertIsInstance(self.criar_evento(db, [equipamento]), int)


class DatabasePostgresTests(unittest.TestCase):
    def test_caminho_padrao_preserva_ddl_placeholders_id_transacao_e_fechamento(self):
        driver = Mock()
        conexao = driver.connect.return_value
        cursor = conexao.cursor.return_value
        cursor.fetchone.side_effect = [
            (1,), (1,),  # duas verificações de coluna na inicialização
            ("read committed",), (7,), (8,), (73,),
            ("read committed",), (7,), (8,), (74,),
        ]
        estados = [
            (item, f"EQ{item}", "Mesa", "", "", "25/11/2026",
             0, None, None, None, None, None, None, None, None)
            for item in (7, 8)
        ]
        cursor.fetchall.side_effect = [
            [estados[0]], [estados[1]], [estados[0]], [estados[1]],
        ]

        with patch.object(database, "psycopg", driver), \
             patch.object(database, "_load_env_file"), \
             patch.dict(os.environ, {"SEAGBH_DB_ENGINE": "postgresql"}, clear=True), \
             patch.object(protegido, "abrir_conexao_operacional") as fabrica, \
             patch.object(sqlite3, "connect") as sqlite_connect:
            db = database.Database()
            self.assertIs(db.conn, conexao)
            self.assertEqual(db.backend, "postgres")
            self.assertEqual(db.criar_evento("Evento", "Sala", "Equipe", "i", "f", [8, 7]), 73)
            chamadas = cursor.execute.call_args_list
            sqls = [call.args[0] for call in chamadas]
            self.assertTrue(any("id SERIAL PRIMARY KEY" in sql for sql in sqls))
            self.assertTrue(any("RETURNING id" in sql and "%s" in sql for sql in sqls))
            self.assertTrue(any(call.args[1][:2] == (73, 7)
                                for call in chamadas if "INSERT INTO movimentacoes" in call.args[0]))
            isolamento = sqls.index("SHOW transaction_isolation")
            travas = [(i, call.args[1]) for i, call in enumerate(chamadas)
                      if "FOR UPDATE" in call.args[0]]
            estados_lidos = [(i, call.args[1]) for i, call in enumerate(chamadas)
                             if "WITH itens AS" in call.args[0]]
            self.assertEqual([params for _, params in travas], [(7,), (8,)])
            self.assertEqual([params for _, params in estados_lidos], [(7,), (8,)])
            self.assertLess(isolamento, travas[0][0])
            for (i_trava, _), (i_estado, _) in zip(travas, estados_lidos, strict=True):
                self.assertLess(i_trava, i_estado)
            self.assertEqual(conexao.commit.call_count, 3)

            def falhar_movimentacao(sql, *args):
                if "INSERT INTO movimentacoes" in sql:
                    raise RuntimeError("falha PostgreSQL sintética")

            cursor.execute.side_effect = falhar_movimentacao
            with self.assertRaisesRegex(RuntimeError, "falha PostgreSQL sintética"):
                db.criar_evento("Evento", "Sala", "Equipe", "i", "f", [8, 7])
            conexao.rollback.assert_called_once_with()
            self.assertEqual(conexao.commit.call_count, 3)
            db.fechar()
        driver.connect.assert_called_once()
        conexao.close.assert_called_once_with()
        self.assertEqual(conexao.commit.call_count, 4)
        fabrica.assert_not_called()
        sqlite_connect.assert_not_called()
