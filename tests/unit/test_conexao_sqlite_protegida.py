"""Testes reais e isolados da conexão SQLite protegida (stdlib, sem Database)."""

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.core import conexao_sqlite_protegida as protegido
from src.core.barreira_sqlite import ErroBarreira, adquirir_manutencao


class ConexaoSQLiteProtegidaTests(unittest.TestCase):
    def setUp(self):
        temporario = tempfile.TemporaryDirectory(prefix="seagbh-conexao-protegida-")
        self.addCleanup(temporario.cleanup)
        self.raiz = Path(temporario.name).resolve()
        self.banco = self.raiz / "sintetico.db"

    def manutencao(self):
        return adquirir_manutencao(backend="sqlite", banco=self.banco, prazo=0)

    def testar_ocupado(self):
        with self.assertRaises(ErroBarreira) as erro:
            self.manutencao()
        self.assertEqual(erro.exception.codigo, "ocupado")

    def test_bloqueio_antes_de_connect_e_liberacao_apos_close(self):
        original = sqlite3.connect
        chamados = []

        def verificar_ordenacao(caminho, **kwargs):
            chamados.append(Path(caminho))
            self.assertTrue(caminho.is_absolute())
            self.testar_ocupado()
            return original(caminho, **kwargs)

        with patch.object(protegido.sqlite3, "connect", side_effect=verificar_ordenacao):
            with protegido.abrir_conexao_operacional(banco=self.banco) as conn:
                self.assertIsInstance(conn, sqlite3.Connection)
                conn.execute("CREATE TABLE registros (id INTEGER PRIMARY KEY)")
                conn.execute("INSERT INTO registros VALUES (7)")
                conn.commit()
                self.testar_ocupado()
        self.assertEqual(chamados, [self.banco])
        with self.manutencao():
            with closing(original(self.banco)) as conn:
                self.assertEqual(conn.execute("SELECT id FROM registros").fetchone(), (7,))

    def test_duas_conexoes_compartilhadas_so_liberam_apos_ambos_close(self):
        primeira = protegido.abrir_conexao_operacional(banco=self.banco)
        self.addCleanup(primeira.fechar)
        segunda = protegido.abrir_conexao_operacional(banco=self.banco)
        self.addCleanup(segunda.fechar)
        self.assertIsNot(primeira.conexao, segunda.conexao)
        self.testar_ocupado()
        primeira.fechar()
        self.testar_ocupado()
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
                conn.execute("CREATE TABLE registros (id INTEGER)")
                conn.execute("INSERT INTO registros VALUES (9)")
                raise ValueError("falha sintetica")
        with self.manutencao():
            pass
        with closing(original(self.banco)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM registros").fetchone(), (0,))

    def test_nao_faz_commit_automatico_no_fechamento(self):
        with protegido.abrir_conexao_operacional(banco=self.banco) as conn:
            conn.execute("CREATE TABLE registros (id INTEGER)")
            conn.execute("INSERT INTO registros VALUES (12)")
        with closing(sqlite3.connect(self.banco)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM registros").fetchone(), (0,))

    def test_fechar_idempotente_e_conexao_inutilizavel_apos_fechamento(self):
        sessao = protegido.abrir_conexao_operacional(banco=self.banco)
        self.addCleanup(sessao.fechar)
        conexao = sessao.conexao
        sessao.fechar()
        sessao.fechar()
        with self.assertRaisesRegex(RuntimeError, "conexao_protegida_encerrada"):
            _ = sessao.conexao
        with self.assertRaises(sqlite3.ProgrammingError):
            conexao.execute("SELECT 1")
        with self.manutencao():
            pass

    def test_close_falho_retem_concessao_ate_recuperacao_explicita(self):
        class FalhaFechamento:
            falhar = True
            def close(self):
                if self.falhar:
                    raise sqlite3.OperationalError("close falhou")

        falso = FalhaFechamento()
        with patch.object(protegido.sqlite3, "connect", return_value=falso):
            sessao = protegido.abrir_conexao_operacional(banco=self.banco)
        self.addCleanup(sessao.fechar)
        with self.assertRaises(sqlite3.OperationalError):
            sessao.fechar()
        self.assertIn(sessao, protegido._FALHAS_FECHAMENTO)
        self.testar_ocupado()
        falso.falhar = False
        sessao.fechar()
        self.assertNotIn(sessao, protegido._FALHAS_FECHAMENTO)
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
            protegido.ConexaoSQLiteProtegida(None, None)


if __name__ == "__main__":
    unittest.main()
