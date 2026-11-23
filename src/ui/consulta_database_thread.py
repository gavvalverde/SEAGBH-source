"""Propriedade de consultas em background e limpeza na thread do banco."""

import logging
from threading import Event

from PyQt6.QtCore import QThread, pyqtSignal

from core.conexao_sqlite_protegida import FechamentoPendente


class ThreadComFechamentoSQLite(QThread):
    fechamento_falhou = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._repetir_fechamento = Event()
        # Signal set ONLY while this worker is blocked after a close failure.
        # A first exit request should retry an ALREADY pending close, but
        # must not preauthorize a retry for a future failure.
        self._fechamento_pendente = Event()

    def repetir_fechamento_se_pendente(self):
        """Retry only when the owner thread is already awaiting a close retry."""
        if self._fechamento_pendente.is_set():
            self.repetir_fechamento()

    def repetir_fechamento(self):
        self._repetir_fechamento.set()

    def _aguardar_repeticao(self):
        logging.getLogger(__name__).error("Fechamento de banco em background pendente")
        # A janela pode receber outro closeEvent antes de processar o sinal.
        self._fechamento_pendente.set()
        try:
            self.fechamento_falhou.emit()
            self._repetir_fechamento.wait()
            self._repetir_fechamento.clear()
        finally:
            self._fechamento_pendente.clear()

    def _fechar_na_thread_dona(self, fechar, *, ja_pendente=False):
        falhou = ja_pendente
        if ja_pendente:
            self._aguardar_repeticao()
        while True:
            try:
                fechar()
                return falhou
            except BaseException:
                falhou = True
                self._aguardar_repeticao()


class ConsultaDatabaseThread(ThreadComFechamentoSQLite):
    pronto = pyqtSignal(object)
    falha = pyqtSignal()

    def __init__(self, abrir_banco, parent=None):
        super().__init__(parent)
        self._abrir_banco = abrir_banco

    def consultar(self, db):
        raise NotImplementedError

    def run(self):
        if self.isInterruptionRequested():
            return
        db = None
        fechar = None
        falhou = True
        dados = None
        try:
            db = self._abrir_banco()
            if not self.isInterruptionRequested():
                dados = self.consultar(db)
                falhou = False
        except FechamentoPendente as erro:
            fechar = erro.fechar
        except BaseException:
            logging.getLogger(__name__).warning("Falha em consulta de background")
        finally:
            if db is not None:
                def fechar():
                    db.fechar_para_gui(confirmar=False)
            if fechar is not None:
                falhou = self._fechar_na_thread_dona(fechar) or falhou

        if not self.isInterruptionRequested():
            if falhou:
                self.falha.emit()
            else:
                self.pronto.emit(dados)
