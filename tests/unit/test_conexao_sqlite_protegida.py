"""Testes reais e isolados da conexão SQLite protegida (stdlib, sem Database)."""

from contextlib import closing
import copy
import gc
import json
from pathlib import Path
import pickle
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import weakref

from src.core import conexao_sqlite_protegida as protegido
from src.core.barreira_sqlite import ErroBarreira, adquirir_manutencao


_SONDA = """
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
from src.core.barreira_sqlite import ErroBarreira, adquirir_manutencao

banco = Path(sys.argv[1])
if sys.argv[2] == "checkpoint":
    with closing(sqlite3.connect(banco, timeout=0)) as conexao:
        with closing(conexao.execute("PRAGMA wal_checkpoint(TRUNCATE)")) as cursor:
            print(json.dumps(cursor.fetchone()))
else:
    try:
        with adquirir_manutencao(backend="sqlite", banco=banco, prazo=0):
            print(json.dumps("adquirida"))
    except ErroBarreira as erro:
        print(json.dumps(erro.codigo))
"""


class ConexaoSQLiteProtegidaTests(unittest.TestCase):
    def setUp(self):
        temporario = tempfile.TemporaryDirectory(prefix="seagbh-conexao-protegida-")
        self.addCleanup(temporario.cleanup)
        self.addCleanup(self.assertEqual, protegido._SESSOES_ABERTAS,
                        protegido._SESSOES_ABERTAS.copy())
        self.raiz = Path(temporario.name).resolve()
        self.banco = self.raiz / "sintetico.db"

    def manutencao(self):
        return adquirir_manutencao(backend="sqlite", banco=self.banco, prazo=0)

    def verificar_ocupado(self):
        with self.assertRaises(ErroBarreira) as erro:
            self.manutencao()
        self.assertEqual(erro.exception.codigo, "ocupado")

    def sondar(self, acao="manutencao"):
        resultado = subprocess.run(
            [sys.executable, "-B", "-W", "error::ResourceWarning", "-c", _SONDA,
             str(self.banco), acao],
            check=True, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(resultado.stderr, "")
        return json.loads(resultado.stdout)

    def preparar_wal(self):
        escritora = sqlite3.connect(self.banco)
        # Mantém WAL com frames reais durante as sondas independentes.
        self.addCleanup(escritora.close)
        with closing(escritora.execute("PRAGMA journal_mode=WAL")) as cursor:
            self.assertEqual(cursor.fetchone(), ("wal",))
        with closing(escritora.executescript(
            "CREATE TABLE registros (id INTEGER); INSERT INTO registros VALUES (1), (2);"
        )):
            pass

    def test_bloqueio_antes_de_connect_e_liberacao_apos_close(self):
        original = sqlite3.connect
        chamados = []

        def verificar_ordenacao(caminho, **kwargs):
            chamados.append(Path(caminho))
            self.assertTrue(caminho.is_absolute())
            self.verificar_ocupado()
            return original(caminho, **kwargs)

        with patch.object(protegido.sqlite3, "connect", side_effect=verificar_ordenacao):
            with protegido.abrir_conexao_operacional(banco=self.banco) as conn:
                self.assertIsInstance(conn, protegido.ConexaoSQLiteProtegida)
                conn.executar("CREATE TABLE registros (id INTEGER PRIMARY KEY)")
                conn.executar("INSERT INTO registros VALUES (7)")
                conn.commit()
                self.verificar_ocupado()
        self.assertEqual(chamados, [self.banco])
        with self.manutencao():
            with closing(original(self.banco)) as conn:
                self.assertEqual(conn.execute("SELECT id FROM registros").fetchone(), (7,))

    def test_duas_conexoes_compartilhadas_so_liberam_apos_ambos_close(self):
        primeira = protegido.abrir_conexao_operacional(banco=self.banco)
        self.addCleanup(primeira.fechar)
        segunda = protegido.abrir_conexao_operacional(banco=self.banco)
        self.addCleanup(segunda.fechar)
        primeira.executar("CREATE TABLE registros (id INTEGER)")
        primeira.executar("INSERT INTO registros VALUES (7)")
        self.assertEqual(primeira.executar("SELECT id FROM registros"), [(7,)])
        self.assertEqual(segunda.executar("SELECT id FROM registros"), [])
        primeira.commit()
        self.assertEqual(segunda.executar("SELECT id FROM registros"), [(7,)])
        self.verificar_ocupado()
        primeira.fechar()
        self.verificar_ocupado()
        segunda.fechar()
        with self.manutencao():
            pass

    def test_falha_em_connect_libera_concessao_sem_abrir_banco(self):
        with patch.object(protegido.sqlite3, "connect",
                          side_effect=sqlite3.OperationalError("falha sintetica")) as connect:
            with self.assertRaises(sqlite3.OperationalError):
                protegido.abrir_conexao_operacional(banco=self.banco)
        connect.assert_called_once()
        self.assertFalse(self.banco.exists())
        with self.manutencao():
            pass

    def test_manutencao_ativa_recusa_conectar_antes_de_io_sqlite(self):
        with self.manutencao():
            with patch.object(protegido.sqlite3, "connect") as connect:
                with self.assertRaises(ErroBarreira) as erro:
                    protegido.abrir_conexao_operacional(banco=self.banco)
            self.assertEqual(erro.exception.codigo, "ocupado")
            connect.assert_not_called()
            self.assertFalse(self.banco.exists())

    def test_excecao_no_corpo_reverte_pendentes_e_libera_bloqueio(self):
        original = sqlite3.connect
        with self.assertRaisesRegex(ValueError, "sintetica"):
            with protegido.abrir_conexao_operacional(banco=self.banco) as conn:
                conn.executar("CREATE TABLE registros (id INTEGER)")
                conn.executar("INSERT INTO registros VALUES (9)")
                raise ValueError("falha sintetica")
        with self.manutencao():
            pass
        with closing(original(self.banco)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM registros").fetchone(), (0,))

    def test_nao_faz_commit_automatico_no_fechamento(self):
        with protegido.abrir_conexao_operacional(banco=self.banco) as conn:
            conn.executar("CREATE TABLE registros (id INTEGER)")
            conn.executar("INSERT INTO registros VALUES (12)")
        with closing(sqlite3.connect(self.banco)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM registros").fetchone(), (0,))

    def test_fechar_idempotente_e_conexao_inutilizavel_apos_fechamento(self):
        sessao = protegido.abrir_conexao_operacional(banco=self.banco)
        self.addCleanup(sessao.fechar)
        with sessao as acesso:
            self.assertEqual(acesso.executar("SELECT 1"), [(1,)])
        sessao.fechar()
        sessao.fechar()
        with self.assertRaisesRegex(RuntimeError, "conexao_protegida_encerrada"):
            acesso.executar("SELECT 1")
        with self.manutencao():
            pass

    def test_close_falho_retem_concessao_ate_recuperacao_explicita(self):
        class FalhaFechamento(sqlite3.Connection):
            falhar = True

            def close(self):
                if self.falhar:
                    raise sqlite3.OperationalError("close falhou")
                super().close()

        falso = sqlite3.connect(self.banco, factory=FalhaFechamento)
        with patch.object(protegido.sqlite3, "connect", return_value=falso):
            sessao = protegido.abrir_conexao_operacional(banco=self.banco)
        self.addCleanup(sessao.fechar)
        self.addCleanup(setattr, falso, "falhar", False)
        with self.assertRaises(sqlite3.OperationalError):
            sessao.fechar()
        self.assertIn(sessao, protegido._SESSOES_ABERTAS)
        self.verificar_ocupado()
        self.assertEqual(self.sondar(), "ocupado")
        with self.assertRaisesRegex(RuntimeError, "conexao_protegida_encerrada"):
            sessao.executar("SELECT 1")
        falso.falhar = False
        sessao.fechar()
        self.assertNotIn(sessao, protegido._SESSOES_ABERTAS)
        self.assertEqual(self.sondar(), "adquirida")

    def test_gc_nao_libera_concessao_de_sessao_abandonada(self):
        sessao = protegido.abrir_conexao_operacional(banco=self.banco)
        ref = weakref.ref(sessao)
        self.addCleanup(lambda: ref() and ref().fechar())
        self.assertEqual(sessao.executar("SELECT 1"), [(1,)])
        del sessao
        gc.collect()
        self.assertIsNotNone(ref())
        self.assertIn(ref(), protegido._SESSOES_ABERTAS)
        self.assertEqual(ref().executar("SELECT 1"), [(1,)])
        self.verificar_ocupado()
        self.assertEqual(self.sondar(), "ocupado")
        ref().fechar()
        self.assertNotIn(ref(), protegido._SESSOES_ABERTAS)
        with self.manutencao():
            pass

    def test_recusa_caminho_nao_absoluto_sem_conexao(self):
        with patch.object(protegido.sqlite3, "connect") as connect:
            with self.assertRaises(ErroBarreira) as erro:
                protegido.abrir_conexao_operacional(banco=Path("relativo.db"))
        self.assertEqual(erro.exception.codigo, "caminho_inseguro")
        connect.assert_not_called()

    def test_nao_permite_fabricar_sessao_sem_concessao_emitida(self):
        with self.assertRaisesRegex(RuntimeError, "conexao_protegida_nao_emitida"):
            protegido.ConexaoSQLiteProtegida()

    def test_falha_de_construcao_antecede_aquisicao_e_connect(self):
        antes = protegido._SESSOES_ABERTAS.copy()
        with patch.object(protegido.ConexaoSQLiteProtegida, "__init__",
                          side_effect=MemoryError("sessão sintética")), \
             patch.object(protegido, "adquirir_uso_operacional") as adquirir, \
             patch.object(protegido.sqlite3, "connect") as connect:
            with self.assertRaises(MemoryError):
                protegido.abrir_conexao_operacional(banco=self.banco)
        adquirir.assert_not_called()
        connect.assert_not_called()
        self.assertEqual(protegido._SESSOES_ABERTAS, antes)
        self.assertEqual(self.sondar(), "adquirida")

    def test_falha_de_registro_antes_e_depois_da_insercao(self):
        registrar = protegido._registrar
        antes = protegido._SESSOES_ABERTAS.copy()
        for inserir in (False, True):
            with self.subTest(inserir=inserir):
                def falhar(sessao):
                    if inserir:
                        registrar(sessao)
                    raise MemoryError("registro sintético")

                with patch.object(protegido, "_registrar", side_effect=falhar), \
                     patch.object(protegido, "adquirir_uso_operacional") as adquirir, \
                     patch.object(protegido.sqlite3, "connect") as connect:
                    with self.assertRaises(MemoryError):
                        protegido.abrir_conexao_operacional(banco=self.banco)
                adquirir.assert_not_called()
                connect.assert_not_called()
                self.assertEqual(protegido._SESSOES_ABERTAS, antes)
        self.assertEqual(self.sondar(), "adquirida")

    def test_falha_apos_connect_preserva_protecao_ate_fechar(self):
        teste = self
        original = sqlite3.connect
        preparar = protegido.ConexaoSQLiteProtegida._abrir
        antes = protegido._SESSOES_ABERTAS.copy()
        fechamentos = []

        class Observada(sqlite3.Connection):
            def close(self):
                fechamentos.append(teste.sondar())
                super().close()

        def falhar_antes_da_entrega(sessao, banco, prazo):
            preparar(sessao, banco, prazo)
            raise KeyboardInterrupt("entrega sintética")

        with patch.object(protegido.sqlite3, "connect",
                          side_effect=lambda *a, **kw: original(*a, factory=Observada, **kw)), \
             patch.object(protegido.ConexaoSQLiteProtegida, "_abrir", falhar_antes_da_entrega):
            with self.assertRaises(KeyboardInterrupt):
                protegido.abrir_conexao_operacional(banco=self.banco)
        self.assertEqual(fechamentos, ["ocupado"])
        self.assertEqual(protegido._SESSOES_ABERTAS, antes)
        self.assertEqual(self.sondar(), "adquirida")

    def test_falha_de_preparacao_e_close_retem_sessao_sem_entrega(self):
        original = sqlite3.connect
        antes = protegido._SESSOES_ABERTAS.copy()

        class Falha(sqlite3.Connection):
            fechar_possivel = False

            def set_authorizer(self, callback):
                raise ValueError("preparação sintética")

            def close(self):
                if not self.fechar_possivel:
                    raise sqlite3.OperationalError("close sintético")
                super().close()

        conexoes = []

        def conectar(*args, **kwargs):
            self.assertEqual(self.sondar(), "ocupado")
            self.assertEqual(len(protegido._SESSOES_ABERTAS - antes), 1)
            conexao = original(*args, factory=Falha, **kwargs)
            conexoes.append(conexao)
            return conexao

        with patch.object(protegido.sqlite3, "connect", side_effect=conectar):
            with self.assertRaises(protegido.FechamentoPendente) as erro:
                protegido.abrir_conexao_operacional(banco=self.banco)
        (sessao,) = protegido._SESSOES_ABERTAS - antes
        self.addCleanup(sessao.fechar)
        self.addCleanup(setattr, conexoes[0], "fechar_possivel", True)
        causa = erro.exception.__cause__
        self.assertIsInstance(causa, sqlite3.OperationalError)
        self.assertEqual(str(causa), "close sintético")
        self.assertIsInstance(causa.__context__, ValueError)
        self.assertEqual(self.sondar(), "ocupado")
        conexoes[0].fechar_possivel = True
        sessao.fechar()
        self.assertEqual(protegido._SESSOES_ABERTAS, antes)
        self.assertEqual(self.sondar(), "adquirida")

    def test_close_logico_nativo_com_cursor_parcial_ainda_impede_checkpoint(self):
        self.preparar_wal()
        with closing(sqlite3.connect(self.banco, cached_statements=0)) as conexao:
            cursor = conexao.execute("SELECT id FROM registros")
            try:
                self.assertEqual(next(cursor), (1,))
                conexao.close()
                self.assertEqual(self.sondar("checkpoint")[0], 1)
            finally:
                # O cursor tornou-se inutilizável, mas ainda possui o statement.
                del cursor
                gc.collect()
        self.assertEqual(self.sondar("checkpoint"), [0, 0, 0])

    def test_cursor_pendente_por_falha_retem_lock_ate_fechar_sqlite(self):
        self.preparar_wal()
        original = sqlite3.connect
        teste = self

        class CursorPendente(sqlite3.Cursor):
            falhar_close = True

            def fetchall(self):
                teste.assertEqual(next(self), (1,))
                raise ValueError("fetch sintético")

            def close(self):
                if self.falhar_close:
                    raise sqlite3.OperationalError("cursor sintético")
                super().close()

        class ConexaoObservada(sqlite3.Connection):
            def cursor(self):
                return super().cursor(factory=CursorPendente)

            def close(self):
                teste.assertEqual(teste.sondar("checkpoint"), [0, 0, 0])
                teste.assertEqual(teste.sondar(), "ocupado")
                super().close()

        with patch.object(protegido.sqlite3, "connect",
                          side_effect=lambda *a, **kw: original(*a, factory=ConexaoObservada, **kw)):
            sessao = protegido.abrir_conexao_operacional(banco=self.banco)
        self.addCleanup(sessao.fechar)
        try:
            with self.assertRaisesRegex(sqlite3.OperationalError, "cursor sintético") as erro:
                sessao.executar("SELECT id FROM registros")
            self.assertIsInstance(erro.exception.__context__, ValueError)
            with self.assertRaises(sqlite3.OperationalError):
                sessao.fechar()
            self.assertEqual(self.sondar("checkpoint")[0], 1)
            self.assertEqual(self.sondar(), "ocupado")
        finally:
            CursorPendente.falhar_close = False
        sessao.fechar()
        self.assertEqual(self.sondar(), "adquirida")

    def test_consulta_entrega_dados_destacados_sem_apis_de_handles(self):
        self.preparar_wal()
        with protegido.abrir_conexao_operacional(banco=self.banco) as sessao:
            dados = iter(sessao.executar("SELECT id FROM registros ORDER BY id"))
            self.assertEqual(next(dados), (1,))
            self.assertEqual(self.sondar("checkpoint"), [0, 0, 0])
            self.assertEqual(self.sondar(), "ocupado")
            for nome in ("conexao", "cursor", "blobopen", "backup", "executescript",
                         "create_function", "set_authorizer"):
                with self.subTest(nome=nome), self.assertRaises(AttributeError):
                    getattr(sessao, nome)
        self.assertEqual(next(dados), (2,))
        self.assertEqual(self.sondar(), "adquirida")

    def test_recusa_attach_e_vacuum_into_sem_abrir_outro_banco(self):
        outro = self.raiz / "outro.db"
        with protegido.abrir_conexao_operacional(banco=self.banco) as sessao:
            for sql in ("ATTACH DATABASE ? AS externo", "VACUUM INTO ?"):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                    sessao.executar(sql, (str(outro),))
            self.assertFalse(outro.exists())
            self.assertEqual(sessao.executar("SELECT 1"), [(1,)])

    def test_thread_dona_e_concorrencia_com_duas_sessoes(self):
        pronta = threading.Event()
        permitir_fechar = threading.Event()
        erros = []
        compartilhadas = []

        def cliente():
            try:
                with protegido.abrir_conexao_operacional(banco=self.banco) as sessao:
                    self.assertEqual(sessao.executar("SELECT 1"), [(1,)])
                    compartilhadas.append(sessao)
                    pronta.set()
                    if not permitir_fechar.wait(10):
                        raise AssertionError("thread dona não recebeu liberação")
            except BaseException as erro:
                erros.append(erro)
                pronta.set()

        thread = threading.Thread(target=cliente)
        thread.start()
        try:
            self.assertTrue(pronta.wait(10))
            self.assertFalse(erros)
            (outra,) = compartilhadas
            for chamada in (lambda: outra.executar("SELECT 1"), outra.commit,
                            outra.rollback, outra.__enter__, outra.fechar):
                with self.assertRaisesRegex(RuntimeError, "thread_incorreta"):
                    chamada()
            self.assertEqual(self.sondar(), "ocupado")
            with protegido.abrir_conexao_operacional(banco=self.banco) as sessao:
                self.assertEqual(sessao.executar("SELECT 2"), [(2,)])
                permitir_fechar.set()
                thread.join(10)
                self.assertFalse(thread.is_alive())
                self.assertFalse(erros)
                self.assertEqual(self.sondar(), "ocupado")
            self.assertEqual(self.sondar(), "adquirida")
        finally:
            permitir_fechar.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())

    def test_recusa_reentrada_durante_bind_de_parametro(self):
        teste = self
        with protegido.abrir_conexao_operacional(banco=self.banco) as sessao:
            class Parametro:
                def __conform__(self, protocolo):
                    for chamada in (sessao.fechar, sessao.commit, sessao.rollback):
                        with teste.assertRaisesRegex(RuntimeError, "operacao_ativa"):
                            chamada()
                    teste.assertEqual(teste.sondar(), "ocupado")
                    return 7

            self.assertEqual(sessao.executar("SELECT ?", (Parametro(),)), [(7,)])
            with self.assertRaisesRegex(RuntimeError, "contexto_ativo"):
                with sessao:
                    self.fail("contexto aninhado aceito")
        self.assertEqual(self.sondar(), "adquirida")

    def test_copia_e_serializacao_nao_duplicam_a_posse(self):
        with protegido.abrir_conexao_operacional(banco=self.banco) as sessao:
            for chamada in (copy.copy, copy.deepcopy, pickle.dumps):
                with self.assertRaisesRegex(TypeError, "nao_copiavel"):
                    chamada(sessao)
            self.verificar_ocupado()

    def test_resultado_materializado_conserva_id_da_instrucao(self):
        with protegido.abrir_conexao_operacional(banco=self.banco) as sessao:
            sessao.executar("CREATE TABLE registros (id INTEGER PRIMARY KEY, nome TEXT)")
            primeiro = sessao.executar_resultado("INSERT INTO registros(nome) VALUES (?)", ("a",))
            segundo = sessao.executar_resultado("INSERT INTO registros(nome) VALUES (?) RETURNING id",
                                                 ("b",))
            sessao.commit()
            linhas = sessao.executar("SELECT id, nome FROM registros ORDER BY id")
        self.assertEqual((primeiro.linhas, primeiro.lastrowid), ([], 1))
        self.assertEqual((segundo.linhas, segundo.lastrowid), ([(2,)], 2))
        self.assertEqual(linhas, [(1, "a"), (2, "b")])

    def test_script_usa_parser_nativo_sem_confirmar_transacao_pendente(self):
        with protegido.abrir_conexao_operacional(banco=self.banco) as sessao:
            sessao.executar_script_inicializacao("""
                CREATE TABLE registros (nome TEXT);
                CREATE TRIGGER observar AFTER INSERT ON registros
                BEGIN UPDATE registros SET nome = 'texto; com ponto e vírgula'; END;
            """)
            sessao.executar("INSERT INTO registros VALUES ('a')")
            with self.assertRaisesRegex(RuntimeError, "sqlite_script_com_transacao_ativa"):
                sessao.executar_script_inicializacao("CREATE TABLE indevida (id);")
            sessao.rollback()
        with closing(sqlite3.connect(self.banco)) as conexao:
            with closing(conexao.execute("SELECT nome FROM registros")) as cursor:
                self.assertEqual(cursor.fetchall(), [])
            with closing(conexao.execute("SELECT name FROM sqlite_schema WHERE name='indevida'")) as cursor:
                self.assertIsNone(cursor.fetchone())


if __name__ == "__main__":
    unittest.main()
