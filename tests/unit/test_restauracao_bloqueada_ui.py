"""Bloqueio da restauração legada com Qt real e bancos exclusivamente temporários."""

import ast
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
try:
    from PyQt6 import sip
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtGui import QAction
    from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QPushButton
except ImportError:
    raise unittest.SkipTest("PyQt6 indisponível: bloqueio Qt real não executado.")

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))
from ui import dialogs_utils as ui
from core import database


class BancoSemAcesso:
    def __init__(self, backend):
        self.backend = backend

    @property
    def conn(self):
        raise AssertionError("Conexão operacional proibida")

    @property
    def cursor(self):
        raise AssertionError("Cursor operacional proibido")


class RestauracaoBloqueadaUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="seagbh-restauracao-bloqueada-")
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.pasta = self.raiz / "Backups"
        self.pasta.mkdir()
        self.origem = self.pasta / "backup_sintetico.db"
        self.destino = self.raiz / "operacional_sintetico.db"
        self.conexoes = []
        for caminho, valor in ((self.origem, "origem"), (self.destino, "destino")):
            conn = sqlite3.connect(caminho)
            self.addCleanup(conn.close)
            self.conexoes.append(conn)
            self.assertEqual(conn.execute("PRAGMA journal_mode=WAL").fetchone(), ("wal",))
            conn.execute("CREATE TABLE sintetico (id INTEGER PRIMARY KEY, valor TEXT)")
            conn.execute("INSERT INTO sintetico VALUES (1, ?)", (valor,))
            conn.commit()

        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(ui, "_PASTA_BACKUPS", str(self.pasta)))
        self.io = {}
        for nome, objeto, atributo in (
            ("copy2", shutil, "copy2"),
            ("makedirs", ui.os, "makedirs"),
            ("connect", sqlite3, "connect"),
            ("Database_ui", ui, "Database"),
            ("Database_core", database, "Database"),
            ("criar_backup", ui, "criar_backup"),
        ):
            self.io[nome] = self.patches.enter_context(patch.object(
                objeto, atributo, side_effect=AssertionError(nome + " proibido neste fluxo")))
        self.get_path = self.patches.enter_context(patch.object(
            ui, "get_db_path", return_value=str(self.destino)))
        self.aviso = self.patches.enter_context(patch.object(ui.QMessageBox, "warning"))
        self.pergunta = self.patches.enter_context(patch.object(
            ui.QMessageBox, "question", return_value=ui.QMessageBox.StandardButton.Yes))
        self.info = self.patches.enter_context(patch.object(ui.QMessageBox, "information"))
        self.erro = self.patches.enter_context(patch.object(ui.QMessageBox, "critical"))
        self.widgets = []
        self.addCleanup(self._fechar_widgets)

    def _fechar_widgets(self):
        for widget in reversed(self.widgets):
            if not sip.isdeleted(widget):
                widget.close()
                widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _dialogo(self, parent=None):
        dialogo = ui.RestaurarBackupDialog(parent=parent)
        self.widgets.append(dialogo)
        dialogo.show()
        self.app.processEvents()
        return dialogo

    def _texto(self, dialogo):
        return " ".join(label.text() for label in dialogo.findChildren(QLabel))

    def _sem_operacoes(self):
        for operacao in self.io.values():
            operacao.assert_not_called()
        self.get_path.assert_not_called()
        self.pergunta.assert_not_called()
        self.info.assert_not_called()
        self.erro.assert_not_called()

    def _estado_arquivos(self):
        return {
            str(p.relative_to(self.raiz)): (
                p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()
            )
            for p in self.raiz.rglob("*") if p.is_file()
        }

    def _janela(self, backend):
        # Executa os métodos reais sem iniciar MainWindow, abas, timers ou atualizador.
        caminho = SRC / "ui" / "main_window.py"
        classe = next(n for n in ast.parse(caminho.read_text(encoding="utf-8")).body
                      if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
        nomes = {"_criar_menu", "_restaurar_backup"}
        metodos = [n for n in classe.body if isinstance(n, ast.FunctionDef) and n.name in nomes]
        namespace = {"QAction": QAction, "RestaurarBackupDialog": ui.RestaurarBackupDialog}
        exec(compile(ast.Module(body=metodos, type_ignores=[]), str(caminho), "exec"), namespace)
        tipo = type("JanelaSintetica", (QMainWindow,), {n: namespace[n] for n in nomes})
        janela = tipo()
        janela.db = BancoSemAcesso(backend)
        janela._sqlite_protegido = False
        for nome in ("_alternar_tema", "_abrir_auditoria", "_fazer_backup",
                     "_verificar_permissoes", "_verificar_atualizacao", "_checar_atrasos"):
            setattr(janela, nome, Mock())
        self.widgets.append(janela)
        return janela

    def test_dialogo_exibe_bloqueio_e_nao_executa_operacoes(self):
        dialogo = self._dialogo()
        self.assertTrue(dialogo.isVisible())
        self.assertIn("temporariamente indisponível", self._texto(dialogo))
        self.assertIn("recuperação em modo de manutenção", self._texto(dialogo))
        self.assertFalse(dialogo.lista.isEnabled())
        self.assertEqual(dialogo.lista.count(), 0)
        self.assertFalse(dialogo.btn_restaurar.isEnabled())
        self.aviso.assert_not_called()
        self._sem_operacoes()

    def test_abertura_e_carregamento_nao_criam_diretorio_ausente(self):
        ausente = self.raiz / "pasta-inexistente"
        with patch.object(ui, "_PASTA_BACKUPS", str(ausente)):
            dialogo = self._dialogo()
            dialogo._carregar_backups()
            dialogo._restaurar()
        self.assertFalse(ausente.exists())
        self.assertEqual(dialogo.lista.count(), 0)
        self._sem_operacoes()

    def test_chamada_direta_recusada_com_ou_sem_selecao(self):
        dialogo = self._dialogo()
        for nome in (None, self.origem.name, "../operacional_sintetico.db"):
            with self.subTest(selecao=nome):
                self.aviso.reset_mock()
                dialogo.lista.clear()
                if nome is not None:
                    dialogo.lista.addItem(nome)
                    dialogo.lista.setCurrentRow(0)
                dialogo._restaurar()
                self.aviso.assert_called_once()
                self.assertIn("temporariamente indisponível", self.aviso.call_args.args[2])
                self.assertIn("recuperação em modo de manutenção", self.aviso.call_args.args[2])
                self.assertTrue(dialogo.isVisible())
                self._sem_operacoes()

    def test_reabilitar_botao_nao_permite_copia(self):
        dialogo = self._dialogo()
        dialogo.lista.addItem(self.origem.name)
        dialogo.lista.setCurrentRow(0)
        dialogo.btn_restaurar.setEnabled(True)
        dialogo.btn_restaurar.click()
        self.aviso.assert_called_once()
        self._sem_operacoes()

    def test_bancos_wal_shm_permanecem_intactos(self):
        arquivos = [p for banco in (self.origem, self.destino)
                    for p in (banco, Path(str(banco) + "-wal"), Path(str(banco) + "-shm"))]
        self.assertTrue(all(p.is_file() for p in arquivos))
        antes = self._estado_arquivos()
        self.assertEqual(len(antes), 6)
        dialogo = self._dialogo()
        dialogo.lista.addItem(self.origem.name)
        dialogo.lista.setCurrentRow(0)
        dialogo._restaurar()
        dialogo._carregar_backups()
        dialogo.close()
        self.assertEqual(self._estado_arquivos(), antes)
        self.assertEqual(self.conexoes[1].execute("SELECT valor FROM sintetico").fetchall(), [("destino",)])
        self.assertEqual(self.conexoes[0].execute("SELECT valor FROM sintetico").fetchall(), [("origem",)])
        self._sem_operacoes()

    def test_transacao_pendente_nao_sofre_commit_rollback_ou_checkpoint(self):
        # O Windows impede leitura bruta de regiões SHM travadas por um escritor.
        # Os hashes dos seis arquivos são verificados separadamente, sem esse lock.
        principal = self.conexoes[1]
        principal.execute("INSERT INTO sintetico VALUES (2, 'pendente')")
        consultas = []
        principal.set_trace_callback(consultas.append)
        dialogo = self._dialogo()
        dialogo._restaurar()
        dialogo.close()
        self.assertEqual(consultas, [])
        principal.set_trace_callback(None)
        self.assertTrue(principal.in_transaction)
        self.assertEqual(principal.execute("SELECT valor FROM sintetico ORDER BY id").fetchall(),
                         [("destino",), ("pendente",)])
        self.assertEqual(self.conexoes[0].execute("SELECT valor FROM sintetico").fetchall(), [("origem",)])
        self._sem_operacoes()

    def test_menu_bloqueia_restauracao_e_preserva_demais_acoes_em_ambos_backends(self):
        for backend in ("sqlite", "postgres"):
            with self.subTest(backend=backend):
                janela = self._janela(backend)
                janela._criar_menu()
                acoes = janela.menuBar().actions()[0].menu().actions()
                restaurar = next(a for a in acoes if "Restaurar Backup" in a.text())
                self.assertIn("indisponível", restaurar.text())
                self.assertFalse(restaurar.isEnabled())
                for texto, handler in (("Tema", "_alternar_tema"), ("Histórico", "_abrir_auditoria"),
                                       ("Fazer Backup", "_fazer_backup"), ("Permissões", "_verificar_permissoes"),
                                       ("Atualizações", "_verificar_atualizacao"), ("Atrasos", "_checar_atrasos")):
                    acao = next(a for a in acoes if texto in a.text())
                    self.assertTrue(acao.isEnabled())
                    acao.trigger()
                    getattr(janela, handler).assert_called_once()
        self._sem_operacoes()

    def test_handler_direto_da_janela_permanece_bloqueado_em_ambos_backends(self):
        execucoes = []

        def examinar(dialogo):
            execucoes.append(dialogo)
            self.widgets.append(dialogo)
            self.assertIn("temporariamente indisponível", self._texto(dialogo))
            self.assertFalse(dialogo.btn_restaurar.isEnabled())
            dialogo.lista.addItem(self.origem.name)
            dialogo.lista.setCurrentRow(0)
            dialogo._restaurar()
            return 0

        for backend in ("sqlite", "postgres"):
            with self.subTest(backend=backend):
                self.aviso.reset_mock()
                execucoes.clear()
                janela = self._janela(backend)
                with patch.object(ui.RestaurarBackupDialog, "exec", new=examinar):
                    janela._restaurar_backup()
                self.assertEqual(len(execucoes), 1)
                self.aviso.assert_called_once()
                self._sem_operacoes()

    def test_dialogo_pode_ser_fechado_sem_efeitos_colaterais(self):
        antes = self._estado_arquivos()
        dialogo = self._dialogo()
        fechar = next(b for b in dialogo.findChildren(QPushButton) if b.text() == "Fechar")
        fechar.click()
        self.assertFalse(dialogo.isVisible())
        self.assertEqual(self._estado_arquivos(), antes)
        self._sem_operacoes()

    def test_auditoria_e_permissoes_continuam_disponiveis_com_dados_sinteticos(self):
        banco = Mock(spec=["listar_auditoria"])
        banco.listar_auditoria.return_value = [(1, "teste", 2, "acao", "sintetico", "2026-01-01")]
        auditoria = ui.AuditoriaDialog(banco)
        self.widgets.append(auditoria)
        self.assertEqual(auditoria.tabela.rowCount(), 1)
        self.assertEqual(auditoria.tabela.item(0, 4).text(), "sintetico")
        banco.listar_auditoria.assert_called_once_with(limite=500)
        with patch.object(ui, "APP_DIR", str(self.raiz)):
            permissoes = ui.PermissoesDialog()
            self.widgets.append(permissoes)
        self.assertIn("Banco de dados", self._texto(permissoes))
        self.assertIn("Pasta de backups", self._texto(permissoes))
        self.get_path.assert_called_once_with()
        for operacao in self.io.values():
            operacao.assert_not_called()


if __name__ == "__main__":
    unittest.main()
