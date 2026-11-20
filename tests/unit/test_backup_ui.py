"""Qt offscreen, dados sintéticos e serviços simulados; não inicia MainWindow."""

import ast
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
try:
    from PyQt6 import sip
    from PyQt6.QtCore import QCoreApplication, QEvent, QThread, QTimer, Qt
    from PyQt6.QtGui import QCloseEvent
    from PyQt6.QtTest import QSignalSpy, QTest
    from PyQt6.QtWidgets import QApplication, QMainWindow, QDialog
except ImportError:
    raise unittest.SkipTest("PyQt6 indisponível: integração Qt real não executada.")

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))
from ui import dialogs_utils as ui
from core.backup_sqlite import ResultadoRetencao, ValidacaoBackup
from tests.unit.test_backup_sqlite import DDL


class BancoSemConexao:
    def __init__(self, backend="sqlite"):
        self.backend = backend

    @property
    def conn(self):
        raise AssertionError("A integração não pode acessar a conexão principal.")

    @property
    def cursor(self):
        raise AssertionError("A integração não pode acessar o cursor principal.")


class BackupUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="seagbh-backup-ui-")
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name).resolve()
        self.origem = self.raiz / "sintetico.db"
        self.pasta = self.raiz / "Backups"
        with closing(sqlite3.connect(self.origem)) as conn:
            conn.executescript(DDL)
            conn.commit()
        self.dialogos = []
        self.janelas = []
        self.liberacoes = []
        self.get_path = self._patch(ui, "get_db_path", return_value=str(self.origem))
        self._patch(ui, "_PASTA_BACKUPS", str(self.pasta))
        self._patch(ui, "Database", side_effect=AssertionError("Database operacional proibido"))
        self._patch(ui.shutil, "copy2", side_effect=AssertionError("Cópia legada proibida"))
        self.info = self._patch(ui.QMessageBox, "information")
        self.aviso = self._patch(ui.QMessageBox, "warning")
        self.erro = self._patch(ui.QMessageBox, "critical")
        self.addCleanup(self._limpar_dialogos)

    def _patch(self, objeto, nome, *args, **kwargs):
        p = patch.object(objeto, nome, *args, **kwargs)
        valor = p.start()
        self.addCleanup(p.stop)
        return valor

    def _dialogo(self, backend="sqlite", parent=None, db=None):
        dialogo = ui.FazerBackupDialog(db or BancoSemConexao(backend), parent=parent)
        self.dialogos.append(dialogo)
        dialogo.show()
        return dialogo

    def _aguardar(self, condicao, prazo=5):
        fim = time.monotonic() + prazo
        while not condicao() and time.monotonic() < fim:
            self.app.processEvents()
            QTest.qWait(1)
        self.assertTrue(condicao(), "Evento Qt não concluído no prazo de teste.")

    def _limpar_dialogos(self):
        for liberar in self.liberacoes:
            liberar.set()
        for dialogo in self.dialogos:
            if sip.isdeleted(dialogo):
                continue
            if dialogo.em_andamento:
                dialogo._cancelar_backup()
                self._aguardar(lambda: not dialogo.em_andamento)
            dialogo.close()
            dialogo.deleteLater()
        for janela in self.janelas:
            janela.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _resultado(self, avisos=(), retencao=()):
        return ui.ResultadoBackup(
            caminho=self.pasta / "seagbh-sqlite-v1-teste.db", identificador="teste",
            criado_em=datetime.now(timezone.utc), tamanho=4096, sha256="a" * 64,
            validacao=ValidacaoBackup(4096, "a" * 64),
            retencao=ResultadoRetencao((), 1, 0, tuple(retencao)), avisos=tuple(avisos),
        )

    def _bloquear_servico(self, *, progresso=None, erro=None, cancelar=False, resultado=None):
        iniciado = threading.Event()
        liberar = threading.Event()
        self.liberacoes.append(liberar)

        def executar(**kwargs):
            iniciado.set()
            if progresso is not None:
                kwargs["progresso"](*progresso)
            if not liberar.wait(4):
                raise TimeoutError("Serviço sintético não liberado.")
            if erro is not None:
                raise erro
            if cancelar and kwargs["cancelar"]():
                raise ui.ErroBackup("snapshot", "cancelado")
            return resultado or self._resultado()

        servico = self._patch(ui, "criar_backup", side_effect=executar)
        return iniciado, liberar, servico

    def test_abertura_sqlite_nao_acessa_banco_ou_cria_diretorio(self):
        dialogo = self._dialogo()
        self.assertTrue(dialogo.btn_criar.isEnabled())
        self.assertFalse(dialogo.btn_cancelar.isEnabled())
        self.assertIn("Pronto", dialogo.status.text())
        self.get_path.assert_not_called()
        self.assertFalse(self.pasta.exists())
        self.assertFalse(dialogo.em_andamento)

    def test_postgres_recusado_sem_sqlite_arquivos_ou_worker(self):
        servico = self._patch(ui, "criar_backup")
        makedirs = self._patch(ui.os, "makedirs")
        dialogo = self._dialogo("postgres")
        dialogo._fazer_backup()
        self.assertFalse(dialogo.btn_criar.isEnabled())
        self.assertIn("somente instalações SQLite", dialogo.status.text())
        self.aviso.assert_called_once()
        servico.assert_not_called()
        makedirs.assert_not_called()
        self.get_path.assert_not_called()
        self.assertFalse(dialogo.em_andamento)

    def test_servico_recebe_caminhos_backend_e_retencao_padrao_fora_da_ui(self):
        threads = []

        def executar(**kwargs):
            threads.append(QThread.currentThread())
            self.assertEqual(kwargs["backend"], "sqlite")
            self.assertEqual(kwargs["origem"], str(self.origem))
            self.assertEqual(kwargs["pasta"], str(self.pasta))
            self.assertEqual(kwargs.get("manter", 10), 10)
            self.assertTrue(callable(kwargs["cancelar"]))
            return self._resultado()

        servico = self._patch(ui, "criar_backup", side_effect=executar)
        dialogo = self._dialogo()
        dialogo._fazer_backup()
        self._aguardar(lambda: not dialogo.em_andamento)
        servico.assert_called_once()
        self.assertNotEqual(threads, [self.app.thread()])
        self.assertTrue(self.pasta.is_dir())
        self.info.assert_called_once()
        self.assertEqual(dialogo.status.text(), "Backup concluído.")
        self.assertIn("criado e validado", self.info.call_args.args[2])

    def test_snapshot_real_wal_nao_usa_conexao_principal_ou_inclui_pendencias(self):
        with closing(sqlite3.connect(self.origem)) as principal:
            principal.execute("PRAGMA journal_mode=WAL")
            principal.execute("INSERT INTO equipamentos VALUES(2,'COD-2','Confirmado',NULL,NULL,'2026-01-01')")
            principal.commit()
            principal.execute("INSERT INTO equipamentos VALUES(3,'COD-3','Pendente',NULL,NULL,'2026-01-01')")
            consultas = []
            principal.set_trace_callback(consultas.append)
            dialogo = self._dialogo(db=types.SimpleNamespace(backend="sqlite", conn=principal))
            dialogo._fazer_backup()
            self._aguardar(lambda: not dialogo.em_andamento)
            self.assertEqual(consultas, [])
            principal.set_trace_callback(None)
            self.assertTrue(principal.in_transaction)
            self.assertEqual(principal.execute("PRAGMA journal_mode").fetchone(), ("wal",))
            arquivos = list(self.pasta.glob("*.db"))
            self.assertEqual(len(arquivos), 1)
            with closing(sqlite3.connect(arquivos[0])) as copia:
                self.assertEqual(copia.execute("SELECT COUNT(*) FROM equipamentos").fetchone(), (2,))
                self.assertEqual(copia.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            principal.rollback()
        self.info.assert_called_once()
        self.erro.assert_not_called()
        self.assertFalse(any(p.name.startswith(".seagbh") for p in self.pasta.iterdir()))

    def test_progresso_na_ui_e_event_loop_responsivo(self):
        iniciado, liberar, _ = self._bloquear_servico(progresso=(5, 10))
        dialogo = self._dialogo()
        threads_widgets = []
        texto_real = dialogo.status.setText

        def atualizar(texto):
            threads_widgets.append(QThread.currentThread())
            texto_real(texto)

        self._patch(dialogo.status, "setText", side_effect=atualizar)
        dialogo._fazer_backup()
        self._aguardar(lambda: iniciado.is_set() and dialogo.progresso.value() == 50)
        marcador = []
        QTimer.singleShot(0, lambda: marcador.append(True))
        self._aguardar(lambda: bool(marcador))
        self.assertTrue(dialogo.em_andamento)
        self.assertTrue(all(t == self.app.thread() for t in threads_widgets))
        self.assertEqual(dialogo.progresso.format(), "Snapshot: %p%")
        self.info.assert_not_called()
        liberar.set()
        self._aguardar(lambda: not dialogo.em_andamento)

    def test_progresso_desconhecido_ou_snapshot_copiado_nao_anuncia_cem_porcento(self):
        for progresso in ((0, 0), (1, 0), (None, None), (0, 10), (-1, 10), (12, 10)):
            with self.subTest(progresso=progresso):
                iniciado, liberar, _ = self._bloquear_servico(progresso=progresso)
                dialogo = self._dialogo()
                dialogo._fazer_backup()
                self._aguardar(lambda: iniciado.is_set() and "copiando páginas" in dialogo.status.text())
                self.assertEqual(dialogo.progresso.maximum(), 0)
                self.assertTrue(dialogo.em_andamento)
                liberar.set()
                self._aguardar(lambda: not dialogo.em_andamento)

    def test_duplo_clique_nao_inicia_segundo_worker(self):
        iniciado, liberar, servico = self._bloquear_servico()
        dialogo = self._dialogo()
        dialogo._fazer_backup()
        worker = dialogo._worker
        self._aguardar(iniciado.is_set)
        dialogo._fazer_backup()
        dialogo.btn_criar.click()
        self.assertIs(dialogo._worker, worker)
        self.assertFalse(dialogo.btn_criar.isEnabled())
        self.assertEqual(servico.call_count, 1)
        liberar.set()
        self._aguardar(lambda: not dialogo.em_andamento)

    def test_sucesso_com_avisos_das_duas_fontes(self):
        resultado = self._resultado(
            avisos=("sincronizacao_diretorio_nao_confirmada", "limpeza_staging_falhou"),
            retencao=("retencao_incompleta:erro_io", "candidato_nao_validado_preservado"),
        )
        self._patch(ui, "criar_backup", return_value=resultado)
        dialogo = self._dialogo()
        dialogo._fazer_backup()
        self._aguardar(lambda: not dialogo.em_andamento)
        texto = self.aviso.call_args.args[2]
        for esperado in ("criado e validado", "sincronização", "temporários", "backups antigos", "preservados"):
            self.assertIn(esperado, texto)
        self.assertEqual(dialogo.status.text(), "Backup concluído com avisos.")
        self.info.assert_not_called()
        self.erro.assert_not_called()

    def test_aviso_desconhecido_nao_mostra_texto_bruto(self):
        self._patch(ui, "criar_backup", return_value=self._resultado(avisos=("payload privado sintetico",)))
        dialogo = self._dialogo()
        dialogo._fazer_backup()
        self._aguardar(lambda: not dialogo.em_andamento)
        self.assertNotIn("payload privado", self.aviso.call_args.args[2])
        self.assertIn("etapa complementar", self.aviso.call_args.args[2])

    def test_erros_snapshot_validacao_publicacao_sao_sanitizados(self):
        for fase, codigo in (("snapshot", "operacao_falhou"), ("validacao", "integridade_fisica"),
                             ("publicacao", "operacao_falhou")):
            with self.subTest(fase=fase):
                erro = ui.ErroBackup(fase, codigo, OSError("SQL e dados privados sinteticos"))
                erro.avisos = ("limpeza_staging_falhou",)
                self._patch(ui, "criar_backup", side_effect=erro)
                dialogo = self._dialogo()
                dialogo._fazer_backup()
                self._aguardar(lambda: not dialogo.em_andamento)
                texto = self.erro.call_args.args[2]
                self.assertIn("não foi concluída", texto)
                self.assertIn("Etapa:", texto)
                self.assertIn("temporários", texto)
                self.assertNotIn("dados privados", texto)
                self.assertTrue(dialogo.btn_criar.isEnabled())
        self.info.assert_not_called()

    def test_erro_inesperado_no_worker_nao_vaza_excecao(self):
        self._patch(ui, "criar_backup", side_effect=RuntimeError("privado sintetico"))
        dialogo = self._dialogo()
        dialogo._fazer_backup()
        self._aguardar(lambda: not dialogo.em_andamento)
        self.assertNotIn("privado", self.erro.call_args.args[2])

    def test_falha_ao_iniciar_thread_libera_estado_e_filtro(self):
        servico = self._patch(ui, "criar_backup")
        self._patch(ui._BackupThread, "start", side_effect=RuntimeError("privado sintetico"))
        dialogo = self._dialogo()
        dialogo._fazer_backup()
        self.assertFalse(dialogo.em_andamento)
        self.assertTrue(dialogo.btn_criar.isEnabled())
        self.assertFalse(dialogo.btn_cancelar.isEnabled())
        self.assertNotIn("privado", self.erro.call_args.args[2])
        servico.assert_not_called()
        dialogo.close()
        self.assertFalse(dialogo.isVisible())

    def test_erro_ao_criar_diretorio_nao_chama_servico(self):
        servico = self._patch(ui, "criar_backup")
        self._patch(ui.os, "makedirs", side_effect=PermissionError("privado sintetico"))
        dialogo = self._dialogo()
        dialogo._fazer_backup()
        self._aguardar(lambda: not dialogo.em_andamento)
        servico.assert_not_called()
        self.assertIn("pasta de backups", self.erro.call_args.args[2])
        self.assertNotIn("privado", self.erro.call_args.args[2])

    def test_worker_cancelado_antes_de_iniciar_nao_cria_pasta(self):
        servico = self._patch(ui, "criar_backup")
        worker = ui._BackupThread("sqlite", str(self.origem), str(self.pasta))
        worker.cancelar()
        worker.start()
        self._aguardar(worker.isFinished)
        self.assertEqual(worker.erro.codigo, "cancelado")
        self.assertFalse(self.pasta.exists())
        servico.assert_not_called()
        worker.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_cancelamento_solicitado_nao_e_anunciado_como_concluido(self):
        iniciado, liberar, _ = self._bloquear_servico(cancelar=True)
        dialogo = self._dialogo()
        dialogo._fazer_backup()
        self._aguardar(iniciado.is_set)
        dialogo.btn_cancelar.click()
        self.assertTrue(dialogo.em_andamento)
        self.assertIn("Cancelamento solicitado", dialogo.status.text())
        self.assertIn("podem demorar", dialogo.status.text())
        self.assertFalse(dialogo.btn_cancelar.isEnabled())
        self.info.assert_not_called()
        liberar.set()
        self._aguardar(lambda: not dialogo.em_andamento)
        self.assertEqual(dialogo.status.text(), "Backup cancelado antes da publicação.")
        self.assertIn("antes da publicação", self.info.call_args.args[2])
        self.erro.assert_not_called()

    def test_cancelamento_tardio_preserva_mensagem_de_backup_publicado(self):
        iniciado, liberar, _ = self._bloquear_servico(
            resultado=self._resultado(retencao=("retencao_incompleta:cancelado",)))
        dialogo = self._dialogo()
        dialogo._fazer_backup()
        self._aguardar(iniciado.is_set)
        dialogo._cancelar_backup()
        liberar.set()
        self._aguardar(lambda: not dialogo.em_andamento)
        texto = self.aviso.call_args.args[2]
        self.assertIn("criado e validado", texto)
        self.assertIn("publicado apesar do pedido", texto)
        self.assertIn("backups antigos não foi concluída", texto)
        self.assertNotIn("antes da publicação", texto)
        self.info.assert_not_called()

    def test_cancelamento_com_falha_de_limpeza_nao_oculta_avisos(self):
        erro = ui.ErroBackup("snapshot", "cancelado")
        erro.avisos = ("limpeza_staging_falhou", "limpeza_lock_falhou")
        self._patch(ui, "criar_backup", side_effect=erro)
        dialogo = self._dialogo()
        dialogo._fazer_backup()
        self._aguardar(lambda: not dialogo.em_andamento)
        texto = self.aviso.call_args.args[2]
        self.assertIn("antes da publicação", texto)
        self.assertIn("temporários", texto)
        self.assertIn("controle de operação", texto)
        self.info.assert_not_called()

    def test_fechar_escape_accept_done_nao_destroem_worker_ativo(self):
        for acao in ("close", "escape", "reject", "accept", "done"):
            with self.subTest(acao=acao):
                iniciado, liberar, _ = self._bloquear_servico(cancelar=True)
                dialogo = self._dialogo()
                dialogo._fazer_backup()
                worker = dialogo._worker
                self._aguardar(iniciado.is_set)
                if acao == "escape":
                    QTest.keyClick(dialogo, Qt.Key.Key_Escape)
                elif acao == "done":
                    dialogo.done(QDialog.DialogCode.Accepted)
                else:
                    getattr(dialogo, acao)()
                self.assertTrue(dialogo.isVisible())
                self.assertTrue(dialogo.em_andamento)
                self.assertFalse(sip.isdeleted(worker))
                liberar.set()
                self._aguardar(lambda: not dialogo.em_andamento)
                self.assertFalse(dialogo.isVisible())

    def test_resultado_e_limpeza_somente_depois_de_finished(self):
        for erro in (None, ui.ErroBackup("snapshot", "operacao_falhou"),
                     ui.ErroBackup("snapshot", "cancelado")):
            with self.subTest(erro=erro):
                iniciado, liberar, _ = self._bloquear_servico(erro=erro)
                dialogo = self._dialogo()
                dialogo._fazer_backup()
                worker = dialogo._worker
                finalizado = QSignalSpy(worker.finished)
                self._aguardar(iniciado.is_set)
                self.assertEqual(len(finalizado), 0)
                liberar.set()
                self._aguardar(lambda: not dialogo.em_andamento)
                self.assertEqual(len(finalizado), 1)
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.assertTrue(sip.isdeleted(worker))

    def test_pedido_de_quit_aguarda_worker_sem_bloquear_ui(self):
        iniciado, liberar, _ = self._bloquear_servico(cancelar=True)
        sair = self._patch(QApplication, "quit")
        dialogo = self._dialogo()
        dialogo._fazer_backup()
        self._aguardar(iniciado.is_set)
        QCoreApplication.sendEvent(self.app, QEvent(QEvent.Type.Quit))
        self.assertTrue(dialogo._sair_pendente)
        self.assertTrue(dialogo.em_andamento)
        sair.assert_not_called()
        liberar.set()
        self._aguardar(lambda: not dialogo.em_andamento and sair.called)

    def test_quit_real_em_processo_qt_isolado_aguarda_cancelamento(self):
        programa = r'''
import os,sys,tempfile,threading
from pathlib import Path
from types import SimpleNamespace
os.environ['QT_QPA_PLATFORM']='offscreen'
sys.path.insert(0,sys.argv[1])
from PyQt6.QtCore import QTimer,QThread
from PyQt6.QtWidgets import QApplication
from ui import dialogs_utils as ui
app=QApplication([])
app.setQuitOnLastWindowClosed(False)
iniciado=threading.Event()
liberar=threading.Event()
estados=[]
def servico(**kwargs):
    iniciado.set()
    if not liberar.wait(3):
        raise RuntimeError('servico sintetico nao liberado')
    if kwargs['cancelar']():
        raise ui.ErroBackup('snapshot','cancelado')
    raise RuntimeError('pedido de quit nao solicitou cancelamento')
def pedir_quit():
    if iniciado.is_set():
        app.quit()
        QTimer.singleShot(30,liberar.set)
    else:
        QTimer.singleShot(1,pedir_quit)
with tempfile.TemporaryDirectory(prefix='seagbh-quit-test-') as tmp:
    ui._PASTA_BACKUPS=str(Path(tmp)/'Backups')
    ui.get_db_path=lambda:str(Path(tmp)/'sintetico.db')
    ui.criar_backup=servico
    ui.QMessageBox.information=lambda *args:None
    ui.QMessageBox.warning=lambda *args:None
    ui.QMessageBox.critical=lambda *args:None
    dialogo=ui.FazerBackupDialog(SimpleNamespace(backend='sqlite'))
    dialogo.show()
    app.aboutToQuit.connect(lambda:estados.append(dialogo.em_andamento))
    QTimer.singleShot(0,dialogo._fazer_backup)
    QTimer.singleShot(1,pedir_quit)
    QTimer.singleShot(5000,lambda:app.exit(92))
    codigo=app.exec()
    assert codigo==0,codigo
    assert estados==[False],estados
    assert dialogo.status.text()=='Backup cancelado antes da publicação.'
    assert not dialogo.findChildren(QThread)
'''
        resultado = subprocess.run([sys.executable, "-B", "-c", programa, str(SRC)],
                                   capture_output=True, text=True, encoding="utf-8", timeout=8)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)

    def _janela_sintetica(self):
        # Exercita os métodos reais sem importar/iniciar MainWindow, tabs ou atualizador.
        caminho = SRC / "ui" / "main_window.py"
        classe = next(n for n in ast.parse(caminho.read_text(encoding="utf-8")).body
                      if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
        metodos = [n for n in classe.body if isinstance(n, ast.FunctionDef)
                   and n.name in {"_fazer_backup", "closeEvent"}]
        namespace = {"FazerBackupDialog": ui.FazerBackupDialog}
        exec(compile(ast.Module(body=metodos, type_ignores=[]), str(caminho), "exec"), namespace)
        tipo = type("JanelaSintetica", (QMainWindow,),
                    {nome: namespace[nome] for nome in ("_fazer_backup", "closeEvent")})
        janela = tipo()
        janela.db = BancoSemConexao()
        janela.db.fechar = Mock()
        self.janelas.append(janela)
        return janela

    def test_fechamento_da_janela_principal_recusado_ate_finalizar_worker(self):
        iniciado, liberar, _ = self._bloquear_servico(cancelar=True)
        janela = self._janela_sintetica()
        dialogo = self._dialogo(parent=janela)
        janela._backup_dialog = dialogo
        dialogo._fazer_backup()
        self._aguardar(iniciado.is_set)
        evento = QCloseEvent()
        janela.closeEvent(evento)
        self.assertFalse(evento.isAccepted())
        janela.db.fechar.assert_not_called()
        self.assertTrue(dialogo.em_andamento)
        liberar.set()
        self._aguardar(lambda: not dialogo.em_andamento)
        evento = QCloseEvent()
        janela.closeEvent(evento)
        self.assertTrue(evento.isAccepted())
        janela.db.fechar.assert_called_once()

    def test_chamador_modal_mantem_referencia_e_libera_apos_conclusao(self):
        _, liberar, _ = self._bloquear_servico(cancelar=True)
        janela = self._janela_sintetica()

        def iniciar():
            dialogo = janela._backup_dialog
            self.dialogos.append(dialogo)
            dialogo._fazer_backup()
            QTimer.singleShot(10, dialogo.close)
            QTimer.singleShot(30, liberar.set)

        QTimer.singleShot(0, iniciar)
        janela._fazer_backup()
        self.assertIsNone(janela._backup_dialog)
        janela.db.fechar.assert_not_called()

    def test_fluxo_backup_nao_contem_checkpoint_copia_legada_ou_terminacao(self):
        texto = (SRC / "ui" / "dialogs_utils.py").read_text(encoding="utf-8")
        arvore = ast.parse(texto)
        for classe in arvore.body:
            if isinstance(classe, ast.ClassDef) and classe.name in {"_BackupThread", "FazerBackupDialog"}:
                for no in ast.walk(classe):
                    if isinstance(no, ast.Attribute):
                        self.assertNotIn(no.attr, {"copy2", "conn", "cursor", "terminate", "wait"})
                    if isinstance(no, ast.Constant) and isinstance(no.value, str):
                        self.assertNotIn("wal_checkpoint", no.value)


if __name__ == "__main__":
    unittest.main()
