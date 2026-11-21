"""Qt offscreen, sessões sintéticas e uma sonda SQLite em processo independente."""

from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from threading import Event, get_ident
import time
import unittest
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
try:
    from PyQt6.QtCore import QCoreApplication, QEvent, QTimer
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
except ImportError:
    raise unittest.SkipTest("PyQt6 indisponível: encerramento Qt real não executado.")

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))
from core import atualizacao, conexao_sqlite_protegida as protegido, database
from ui import dialogs_utils, main_window
from tests.unit.test_database_sqlite_protegido import _SONDA


class BancoSintetico:
    backend = "sqlite"

    def __init__(self, registrar, *, consulta=None):
        self._registrar = registrar
        self._consulta = consulta
        self.dono = get_ident()
        registrar("abriu", self.dono)

    def _verificar_dono(self):
        if get_ident() != self.dono:
            raise AssertionError("Banco usado fora da thread dona")

    def todos_equipamentos_completos(self):
        return self._consultar([1, 2, 3])

    def eventos_em_andamento(self):
        return []

    def equipamentos_em_manutencao(self):
        return []

    def eventos_atrasados(self):
        return self._consultar([(7, "Evento sintético", "2000-01-01", 2)])

    def _consultar(self, resultado):
        self._verificar_dono()
        if self._consulta is not None:
            consultar, self._consulta = self._consulta, None
            consultar()
        return resultado

    def fechar_para_gui(self, *, confirmar=True):
        self._verificar_dono()
        self._registrar("fechou", self.dono)


class EncerramentoGUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        temporario = tempfile.TemporaryDirectory(prefix="seagbh-encerramento-")
        self.addCleanup(temporario.cleanup)
        self.raiz = Path(temporario.name).resolve()
        self.banco = self.raiz / "sintetico.db"
        self.gui = get_ident()
        self.eventos = []
        self.liberacoes = []
        self.janelas = []
        self.avisar = self.enterContext(patch.object(main_window.QMessageBox, "warning"))
        self.informar = self.enterContext(patch.object(main_window.QMessageBox, "information"))
        self.sair = self.enterContext(patch.object(QApplication, "quit"))
        self.addCleanup(self._limpar)

    def _registrar(self, evento, dono):
        self.eventos.append((evento, dono))

    def _aguardar(self, condicao, prazo=6):
        limite = time.monotonic() + prazo
        while not condicao() and time.monotonic() < limite:
            QTest.qWait(1)
        self.assertTrue(condicao(), "Evento Qt não concluído no prazo")

    def _bloqueio(self):
        iniciou, liberar = Event(), Event()
        self.liberacoes.append(liberar)

        def bloquear():
            iniciou.set()
            if not liberar.wait(8):
                raise TimeoutError("Bloqueio sintético não liberado")

        return iniciou, liberar, bloquear

    def _limpar(self):
        for liberar in self.liberacoes:
            liberar.set()
        for janela in self.janelas:
            janela.close()
            self._aguardar(lambda: janela.encerramento_confirmado)
            janela.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _janela(self, *, consulta=None, real=False):
        if real:
            janela = main_window.MainWindow(banco_sqlite_protegido=self.banco)
        else:
            def abrir():
                return BancoSintetico(self._registrar, consulta=consulta if get_ident() != self.gui else None)
            with patch.object(main_window, "Database", side_effect=abrir) as fabrica:
                janela = main_window.MainWindow()
            fabrica.assert_called_once_with()
        self.janelas.append(janela)
        janela.tab_dashboard._timer_inicial.stop()
        janela._timer_atrasos.stop()
        janela.show()
        return janela

    def _sondar_manutencao(self):
        resultado = subprocess.run(
            [sys.executable, "-B", "-W", "error::ResourceWarning", "-c",
             _SONDA, str(self.banco), "manutencao"],
            cwd=SRC.parent, capture_output=True, text=True, timeout=12, check=True,
        )
        self.assertEqual(resultado.stderr, "")
        return json.loads(resultado.stdout)

    def test_dashboard_entrega_resultado_apos_fechar_na_thread_dona(self):
        janela = self._janela()
        painel = janela.tab_dashboard
        entregar = painel._atualizar_com_dados
        eventos_na_entrega = []

        def verificar(dados):
            eventos_na_entrega.extend(self.eventos[-2:])
            entregar(dados)

        with patch.object(painel, "_atualizar_com_dados", side_effect=verificar) as renderizar:
            painel.atualizar()
            self._aguardar(lambda: not painel.consultando)
        renderizar.assert_called_once()
        abertura, fechamento = eventos_na_entrega
        self.assertNotEqual(abertura[1], self.gui)
        self.assertEqual(abertura, ("abriu", fechamento[1]))
        self.assertEqual(fechamento[0], "fechou")
        self.assertEqual(painel.card_equip["valor"].text(), "3")

    def test_falha_de_consulta_preserva_ultimo_resultado_e_fecha_banco(self):
        falhar = Event()

        def consultar():
            if falhar.is_set():
                raise RuntimeError("detalhe privado sintético")

        painel = self._janela(consulta=consultar).tab_dashboard
        painel.atualizar()
        self._aguardar(lambda: not painel.consultando)
        self.assertEqual(painel.card_equip["valor"].text(), "3")
        falhar.set()
        painel.atualizar()
        self._aguardar(lambda: not painel.consultando)
        self.assertEqual(painel.card_equip["valor"].text(), "3")
        self.assertEqual(painel._status_consulta.text(), "Não foi possível atualizar o painel.")
        self.assertEqual(self.eventos[-1], ("fechou", self.eventos[-2][1]))

    def test_atrasos_manual_e_automatico_fecham_banco_e_distinguem_apresentacao(self):
        janela = self._janela()
        janela._checar_atrasos_auto()
        self._aguardar(lambda: janela._atrasos_thread is None)
        self.assertIn("1 atraso", janela._btn_alerta_atrasos.text())
        self.avisar.assert_not_called()
        janela._checar_atrasos_auto()
        self.assertIsNone(janela._atrasos_thread)
        janela._checar_atrasos()
        self._aguardar(lambda: janela._atrasos_thread is None)
        self.avisar.assert_called_once()
        self.assertEqual([e for e, _ in self.eventos], ["abriu", "abriu", "fechou", "abriu", "fechou"])

    def test_um_worker_retarda_fechamento_e_ignora_resultado_enfileirado(self):
        iniciou, liberar, bloquear = self._bloqueio()
        janela = self._janela(consulta=bloquear)
        painel = janela.tab_dashboard
        painel.atualizar()
        self._aguardar(iniciou.is_set)
        worker = painel._carregador
        self.assertFalse(janela.close())
        QTimer.singleShot(0, painel.atualizar)
        QTimer.singleShot(0, janela._checar_atrasos_auto)
        QTimer.singleShot(0, lambda: worker.pronto.emit({"total_equip": 100}))
        janela._checar_atrasos()
        self.app.processEvents()
        self.assertIs(painel._carregador, worker)
        self.assertFalse(janela.encerramento_confirmado)
        self.assertIsNone(janela._atrasos_thread)
        self.assertEqual(painel.card_equip["valor"].text(), "0")
        self.assertFalse(janela.close())
        liberar.set()
        self._aguardar(lambda: janela.encerramento_confirmado)
        self.assertEqual(self.eventos[-1], ("fechou", self.gui))
        self.assertEqual(self.eventos.count(("fechou", self.gui)), 1)

    def test_dois_workers_ativos_aguardam_ambos_antes_do_fechamento(self):
        primeiro, libera_primeiro, bloquear_primeiro = self._bloqueio()
        segundo, libera_segundo, bloquear_segundo = self._bloqueio()

        def bloquear():
            (bloquear_segundo if primeiro.is_set() else bloquear_primeiro)()

        janela = self._janela(consulta=bloquear)
        janela.tab_dashboard.atualizar()
        self._aguardar(primeiro.is_set)
        janela._checar_atrasos()
        self._aguardar(segundo.is_set)
        janela.close()
        libera_primeiro.set()
        self._aguardar(lambda: not janela.tab_dashboard.consultando)
        self.assertFalse(janela.encerramento_confirmado)
        self.assertTrue(janela._atrasos_thread.isRunning())
        libera_segundo.set()
        self._aguardar(lambda: janela.encerramento_confirmado)
        self.assertEqual(self.eventos[-1], ("fechou", self.gui))

    def test_close_protegido_falho_retem_worker_e_concessao_ate_repetir_na_thread_dona(self):
        # Somente o worker abre SQLite neste caso, para que a sonda
        # identifique a concessão dele independentemente da janela.
        fabricar = main_window.Database

        def abrir_banco(**kwargs):
            if get_ident() == self.gui:
                return BancoSintetico(self._registrar)
            return fabricar(**kwargs)

        with patch.object(main_window, "Database", side_effect=abrir_banco):
            janela = self._janela(real=True)
        abrir = protegido.sqlite3.connect
        permitir_close = Event()
        self.liberacoes.append(permitir_close)
        tentou = Event()
        donos, fechamentos = [], []

        class Observada(sqlite3.Connection):
            def close(self):
                fechamentos.append(get_ident())
                tentou.set()
                if not permitir_close.is_set():
                    raise sqlite3.OperationalError("falha sintética de close")
                super().close()

        def conectar(*args, **kwargs):
            donos.append(get_ident())
            return abrir(*args, factory=Observada, **kwargs)

        with patch.object(protegido.sqlite3, "connect", side_effect=conectar):
            janela.tab_dashboard.atualizar()
            self._aguardar(tentou.is_set)
        worker = janela.tab_dashboard._carregador
        self.assertTrue(worker.isRunning())
        self.assertEqual(self._sondar_manutencao(), "ocupado")
        janela.close()
        self.assertFalse(janela.encerramento_confirmado)
        self.assertEqual(self._sondar_manutencao(), "ocupado")
        self._aguardar(lambda: "Solicite a saída" in janela.statusBar().currentMessage())
        permitir_close.set()
        janela.close()
        self._aguardar(lambda: janela.encerramento_confirmado)
        self.assertEqual(len(donos), 1)
        self.assertNotEqual(donos[0], self.gui)
        self.assertEqual(fechamentos, donos * 2)
        self.assertEqual(self._sondar_manutencao(), "livre")

    def test_close_falho_na_janela_bloqueia_quit_ate_novo_pedido(self):
        janela = self._janela(real=True)
        fechar = janela.db.fechar
        with patch.object(janela.db, "fechar", side_effect=[OSError("close"), None]) as falhar:
            QCoreApplication.sendEvent(self.app, QEvent(QEvent.Type.Quit))
            self._aguardar(lambda: janela._fechamento_principal_pendente)
            self.assertFalse(janela.encerramento_confirmado)
            self.sair.assert_not_called()
            self.assertEqual(self._sondar_manutencao(), "ocupado")
            self.assertEqual(falhar.call_count, 1)
        with patch.object(janela.db, "fechar", wraps=fechar) as repetir:
            janela.close()
            self._aguardar(lambda: janela.encerramento_confirmado)
            repetir.assert_called_once()
        self.assertEqual(self._sondar_manutencao(), "livre")

    def test_backup_protegido_recusa_antes_de_consultar_caminho_operacional(self):
        janela = self._janela(real=True)
        with patch.object(main_window, "FazerBackupDialog") as dialogo, \
             patch.object(dialogs_utils, "get_db_path") as caminho:
            janela._fazer_backup()
        dialogo.assert_not_called()
        caminho.assert_not_called()
        acoes = janela.menuBar().actions()[0].menu().actions()
        backup = next(acao for acao in acoes if "Fazer Backup" in acao.text())
        self.assertFalse(backup.isEnabled())

    def test_falha_na_construcao_da_interface_fecha_sessao_protegida(self):
        montar = main_window.MainWindow._montar_interface

        def falhar(janela):
            montar(janela)
            self.assertEqual(self._sondar_manutencao(), "ocupado")
            self.assertFalse(janela.tab_dashboard._timer_inicial.isActive())
            raise RuntimeError("interface sintética")

        with patch.object(main_window.MainWindow, "_montar_interface", falhar):
            with self.assertRaisesRegex(RuntimeError, "interface sintética"):
                main_window.MainWindow(banco_sqlite_protegido=self.banco)
        self.assertEqual(self._sondar_manutencao(), "livre")

    def test_falha_de_abertura_e_cleanup_entrega_recuperacao_ao_mesmo_dono(self):
        abrir = sqlite3.connect
        permitir = Event()
        self.liberacoes.append(permitir)

        class Observada(sqlite3.Connection):
            def set_authorizer(self, _):
                raise RuntimeError("preparação sintética")

            def close(self):
                if not permitir.is_set():
                    raise sqlite3.OperationalError("close sintético")
                super().close()

        with patch.object(protegido.sqlite3, "connect",
                          side_effect=lambda *a, **kw: abrir(*a, factory=Observada, **kw)):
            with self.assertRaises(protegido.FechamentoPendente) as erro:
                main_window.MainWindow(banco_sqlite_protegido=self.banco)
        try:
            self.assertEqual(self._sondar_manutencao(), "ocupado")
        finally:
            permitir.set()
            erro.exception.fechar()
        self.assertEqual(self._sondar_manutencao(), "livre")

    def test_download_cancelado_cooperativamente_descarta_dados_parciais(self):
        caminho = self.raiz / "download-sintetico.exe"
        cancelado = Event()
        resposta = BytesIO(b"x" * (64 * 1024 + 1))
        resposta.headers = {"Content-Length": str(len(resposta.getbuffer()))}
        with patch.object(atualizacao, "urlopen", return_value=resposta):
            sucesso = atualizacao.baixar_atualizacao(
                "https://example.invalid/update", str(caminho),
                progresso_cb=lambda *_: cancelado.set(), cancelado=cancelado.is_set,
            )
        self.assertFalse(sucesso)
        self.assertTrue(cancelado.is_set())
        self.assertTrue(resposta.closed)
        self.assertFalse(caminho.exists())

    def test_atualizacao_aguarda_worker_e_sessao_principal_antes_de_aplicar(self):
        iniciou, liberar, bloquear = self._bloqueio()
        janela = self._janela(consulta=bloquear)
        janela.tab_dashboard.atualizar()
        self._aguardar(iniciou.is_set)
        estado = []

        def aplicar(caminho):
            estado.extend([Path(caminho).parent, janela._dl_thread,
                           janela.tab_dashboard.consultando, self.eventos[-1]])

        with patch.object(tempfile, "gettempdir", return_value=str(self.raiz)), \
             patch.object(main_window, "baixar_atualizacao", return_value=True), \
             patch.object(main_window, "validar_aplicacao_atualizacao"), \
             patch.object(main_window, "aplicar_atualizacao", side_effect=aplicar) as instalar:
            janela._iniciar_download("https://example.invalid/update", "sintetico")
            self._aguardar(lambda: janela._encerrando)
            instalar.assert_not_called()
            liberar.set()
            self._aguardar(lambda: janela.encerramento_confirmado)
            instalar.assert_called_once()
            self._aguardar(lambda: self.sair.called)
        self.assertEqual(estado, [self.raiz, None, False, ("fechou", self.gui)])

    def test_database_legado_fecha_cursor_e_conexao_mesmo_com_commit_falho(self):
        db = database.Database.__new__(database.Database)
        db._sqlite_protegido = db._fechamento_gui_iniciado = False
        operacoes = Mock()
        db.conn, db.cursor = operacoes.conn, operacoes.cursor
        db.conn.commit.side_effect = sqlite3.OperationalError("commit sintético")
        with self.assertLogs("core.database", level="ERROR"):
            db.fechar_para_gui()
        self.assertEqual([c[0] for c in operacoes.mock_calls],
                         ["conn.commit", "cursor.close", "conn.close"])
        self.assertIsNone(db.conn)
        self.assertIsNone(db.cursor)

    def test_quit_e_retorno_forcado_do_loop_qt_usam_o_fluxo_seguro(self):
        codigo = r"""
import sys
from pathlib import Path
from threading import Event
from unittest.mock import patch
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
import main_qt
from ui.main_window import MainWindow

banco, modo = sys.argv[1:]
liberar = Event()
iniciou = Event()
janela = None

class Janela(MainWindow):
    def __init__(self, **kwargs):
        super().__init__(banco_sqlite_protegido=Path(banco), **kwargs)
        global janela
        janela = self
        abrir = self.tab_dashboard._abrir_banco

        def abrir_worker():
            db = abrir()
            consultar = db.todos_equipamentos_completos

            def aguardar():
                iniciou.set()
                if not liberar.wait(6):
                    raise TimeoutError("worker sintético")
                return consultar()

            db.todos_equipamentos_completos = aguardar
            return db

        self.tab_dashboard._abrir_banco = abrir_worker
        self.tab_dashboard.atualizar()
        QTimer.singleShot(1, sair_quando_iniciar)

def sair_quando_iniciar():
    if not iniciou.is_set():
        QTimer.singleShot(1, sair_quando_iniciar)
        return
    QTimer.singleShot(0, esperar_encerramento)
    if modo == "quit":
        QApplication.quit()
    else:
        QApplication.exit(7)

def esperar_encerramento():
    if not janela._encerrando:
        QTimer.singleShot(1, esperar_encerramento)
        return
    assert not janela.encerramento_confirmado and janela.tab_dashboard.consultando
    liberar.set()

with patch.object(main_qt, "licenca_ativa", return_value=(True, "", {})), \
     patch.object(main_qt, "MainWindow", Janela), \
     patch.object(main_qt, "SplashScreen", side_effect=lambda on_finished, **_: QTimer.singleShot(0, on_finished)):
    try:
        main_qt.main()
    except SystemExit as saida:
        assert saida.code == (0 if modo == "quit" else 7), saida.code
        assert janela.encerramento_confirmado
        assert liberar.is_set() and not janela.tab_dashboard.consultando
        print("encerramento confirmado")
"""
        for modo in ("quit", "exit"):
            with self.subTest(modo=modo):
                resultado = subprocess.run(
                    [sys.executable, "-B", "-W", "error::ResourceWarning", "-c",
                     codigo, str(self.banco), modo],
                    cwd=SRC, capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(resultado.returncode, 0, resultado.stderr)
                self.assertIn("encerramento confirmado", resultado.stdout)
                self.assertEqual(self._sondar_manutencao(), "livre")


if __name__ == "__main__":
    unittest.main()
