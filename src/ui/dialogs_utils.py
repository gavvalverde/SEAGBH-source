"""
Diálogos de Utilitários — PyQt6.
Auditoria, backup/restauração, verificação de permissões.
"""

import os
from pathlib import Path
from threading import Event

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QListWidget, QMessageBox, QProgressBar, QApplication,
)
from PyQt6.QtCore import Qt, QEvent, QTimer, pyqtSignal, pyqtSlot

from core.database import Database, APP_DIR, get_db_path
from core.backup_sqlite import criar_backup, ResultadoBackup, ErroBackup, FechamentoOrigemPendente
from ui.consulta_database_thread import ThreadComFechamentoSQLite


_PASTA_BACKUPS = os.path.join(APP_DIR, "Backups")
_MENSAGEM_RESTAURACAO_INDISPONIVEL = (
    "A restauração de backups está temporariamente indisponível. "
    "Ela será substituída por recuperação em modo de manutenção."
)


class AuditoriaDialog(QDialog):
    """Exibe o histórico de auditoria em uma tabela."""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📜 Histórico de Alterações (Auditoria)")
        self.resize(900, 550)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)

        registros = db.listar_auditoria(limite=500)

        self.tabela = QTableWidget(len(registros), 6)
        self.tabela.setHorizontalHeaderLabels(
            ["ID", "Tabela", "Item ID", "Ação", "Detalhes", "Data"]
        )
        self.tabela.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        self.tabela.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tabela.setAlternatingRowColors(True)

        for row, reg in enumerate(registros):
            for col, val in enumerate(reg):
                item = QTableWidgetItem(str(val) if val is not None else "")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.tabela.setItem(row, col, item)

        layout.addWidget(self.tabela, stretch=1)

        btn_fechar = QPushButton("Fechar")
        btn_fechar.clicked.connect(self.close)
        layout.addWidget(btn_fechar, alignment=Qt.AlignmentFlag.AlignRight)


class RestaurarBackupDialog(QDialog):
    """Informa o bloqueio da restauração legada, sem acessar arquivos ou bancos."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🔄 Restaurar Backup (indisponível)")
        self.resize(550, 420)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        aviso = QLabel(_MENSAGEM_RESTAURACAO_INDISPONIVEL)
        aviso.setWordWrap(True)
        layout.addWidget(aviso)

        self.lista = QListWidget()
        self._carregar_backups()
        layout.addWidget(self.lista, stretch=1)

        btn_layout = QHBoxLayout()
        self.btn_restaurar = QPushButton("Restauração indisponível")
        self.btn_restaurar.setEnabled(False)
        self.btn_restaurar.clicked.connect(self._restaurar)
        btn_layout.addWidget(self.btn_restaurar)

        btn_fechar = QPushButton("Fechar")
        btn_fechar.clicked.connect(self.close)
        btn_layout.addWidget(btn_fechar)

        layout.addLayout(btn_layout)

    def _carregar_backups(self):
        # Mantido para compatibilidade; o diálogo bloqueado não consulta o disco.
        self.lista.clear()
        self.lista.setEnabled(False)

    def _restaurar(self):
        # Bloqueio incondicional, inclusive em chamadas diretas ou botão reabilitado.
        QMessageBox.warning(
            self, "Restauração indisponível", _MENSAGEM_RESTAURACAO_INDISPONIVEL,
        )


class PermissoesDialog(QDialog):
    """Verifica permissões de escrita em pastas críticas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🛡️ Verificação de Permissões")
        self.resize(500, 300)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        caminhos = [
            ("Pasta do aplicativo", APP_DIR),
            ("Banco de dados", get_db_path()),
            ("Pasta de backups", _PASTA_BACKUPS),
            ("Pasta de relatórios", os.path.join(APP_DIR, "Relatórios")),
        ]

        for nome, caminho in caminhos:
            ok = os.access(caminho, os.W_OK) if os.path.exists(caminho) else False
            icone = "✅" if ok else "❌"
            status = "Gravável" if ok else "Sem permissão / não existe"
            lbl = QLabel(f"{icone}  <b>{nome}</b><br>"
                         f"<small>{caminho}</small><br>"
                         f"<i>{status}</i>")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setStyleSheet("padding: 8px;")
            layout.addWidget(lbl)

        layout.addStretch()

        btn = QPushButton("Fechar")
        btn.clicked.connect(self.close)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignRight)


class _BackupThread(ThreadComFechamentoSQLite):
    """Uma operação bloqueante, com conexões pertencentes ao serviço/worker.

    O objeto QThread pertence à UI; somente run() executa na thread secundária.
    Resultado/erro são lidos pela UI após finished. Não há event loop no worker.
    """

    progresso = pyqtSignal(object, object)  # restantes, total (sem limite de int Qt)

    def __init__(self, backend: str, origem: str | Path, pasta: str | Path,
                 parent=None, *, protegido=False):
        super().__init__(parent)
        self._backend = backend
        self._origem = origem
        self._pasta = pasta
        self._protegido = protegido
        self._cancelamento = Event()
        self.resultado: ResultadoBackup | None = None
        self.erro: ErroBackup | None = None

    def cancelar(self):
        self._cancelamento.set()

    def run(self):
        try:
            if self._backend != "sqlite":
                raise ErroBackup("argumentos", "backend_nao_suportado")
            if self._cancelamento.is_set():
                raise ErroBackup("preparacao", "cancelado")
            try:
                os.makedirs(self._pasta, exist_ok=True)
            except OSError as exc:
                raise ErroBackup("preparacao", "diretorio_inacessivel", exc) from None
            self.resultado = criar_backup(
                backend=self._backend, origem=self._origem, pasta=self._pasta,
                cancelar=self._cancelamento.is_set, progresso=self.progresso.emit,
                protegido=self._protegido,
            )
        except FechamentoOrigemPendente as exc:
            self.erro = exc
            self._fechar_na_thread_dona(exc.fechar, ja_pendente=True)
        except ErroBackup as exc:
            self.erro = exc
        except Exception as exc:
            self.erro = ErroBackup("preparacao", "falha_inesperada", exc)


class FazerBackupDialog(QDialog):
    """Backup seguro em background, sem usar a conexão da janela principal."""

    backup_finalizado = pyqtSignal()

    def __init__(self, db: Database, parent=None, *, quit_coordenado=False):
        super().__init__(parent)
        self._backend = db.backend
        self._origem_protegida = getattr(db, "banco_backup_protegido", None)
        self._worker: _BackupThread | None = None
        self._cancelamento_solicitado = False
        self._fechar_pendente = False
        self._sair_pendente = False
        self._quit_coordenado = quit_coordenado
        self._saida_da_janela = False
        self.setWindowTitle("💾 Fazer Backup")
        self.resize(540, 290)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        descricao = QLabel(
            "Crie uma cópia validada dos dados já salvos. "
            "Alterações ainda não confirmadas não fazem parte do backup."
        )
        descricao.setWordWrap(True)
        layout.addWidget(descricao)
        self.status = QLabel("Pronto para criar backup.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.progresso = QProgressBar()
        self.progresso.setRange(0, 100)
        self.progresso.setValue(0)
        self.progresso.setFormat("Aguardando")
        layout.addWidget(self.progresso)

        self.btn_criar = QPushButton("💾 Criar Backup Agora")
        self.btn_criar.setProperty("class", "primary")
        self.btn_criar.setMinimumHeight(40)
        self.btn_criar.clicked.connect(self._fazer_backup)
        layout.addWidget(self.btn_criar)
        botoes = QHBoxLayout()
        self.btn_cancelar = QPushButton("Solicitar cancelamento")
        self.btn_cancelar.setEnabled(False)
        self.btn_cancelar.clicked.connect(self._cancelar_backup)
        botoes.addWidget(self.btn_cancelar)
        self.btn_fechar = QPushButton("Fechar")
        self.btn_fechar.clicked.connect(self.close)
        botoes.addWidget(self.btn_fechar)
        layout.addLayout(botoes)
        if self._backend != "sqlite":
            self.btn_criar.setEnabled(False)
            self.status.setText("Esta funcionalidade de backup atende somente instalações SQLite.")

    @property
    def em_andamento(self):
        # Conserva a referência até o slot de finished processar o resultado.
        return self._worker is not None

    def _fazer_backup(self):
        if self.em_andamento or self._saida_da_janela:
            return
        if self._backend != "sqlite":
            QMessageBox.warning(self, "Backup indisponível",
                                "Esta funcionalidade atende somente instalações SQLite. "
                                "O backup PostgreSQL exige um procedimento próprio.")
            return
        try:
            protegido = self._origem_protegida is not None
            if protegido:
                origem, pasta = self._origem_protegida, self._origem_protegida.parent / "Backups"
            else:
                origem, pasta = get_db_path(), _PASTA_BACKUPS
            worker = _BackupThread(self._backend, origem, pasta, parent=self, protegido=protegido)
        except Exception:
            self._mostrar_erro(ErroBackup("preparacao", "falha_inesperada"))
            return
        self._worker = worker
        self._cancelamento_solicitado = False
        self._fechar_pendente = False
        self._sair_pendente = False
        self.btn_criar.setEnabled(False)
        self.btn_cancelar.setEnabled(True)
        self.status.setText("Backup em andamento: criando e validando a cópia…")
        self.progresso.setRange(0, 0)
        worker.progresso.connect(self._atualizar_progresso, Qt.ConnectionType.QueuedConnection)
        worker.fechamento_falhou.connect(self._fechamento_falhou, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._backup_finalizado, Qt.ConnectionType.QueuedConnection)
        # quit() pode ser solicitado por timers da aplicação durante o diálogo modal.
        if not self._quit_coordenado:
            QApplication.instance().installEventFilter(self)
        try:
            worker.start()
        except RuntimeError as exc:
            worker.erro = ErroBackup("preparacao", "falha_inesperada", exc)
            self._backup_finalizado()

    @pyqtSlot(object, object)
    def _atualizar_progresso(self, restantes, total):
        if not self.em_andamento or self._cancelamento_solicitado:
            return
        # São páginas do snapshot, não a porcentagem de toda a operação.
        if (type(total) is int and type(restantes) is int
                and total > 0 and 0 < restantes <= total):
            self.progresso.setRange(0, 100)
            self.progresso.setValue(min(99, (total - restantes) * 100 // total))
            self.progresso.setFormat("Snapshot: %p%")
        else:
            self.progresso.setRange(0, 0)
        self.status.setText("Backup em andamento: copiando páginas; a validação ainda será concluída.")

    def _cancelar_backup(self):
        if not self.em_andamento:
            return
        self._cancelamento_solicitado = True
        self._worker.cancelar()
        self.btn_cancelar.setEnabled(False)
        self.progresso.setRange(0, 0)
        self.status.setText(
            "Cancelamento solicitado. Aguarde a conclusão da operação. "
            "Operações de disco bloqueadas podem demorar para retornar; "
            "o backup pode já ter sido publicado."
        )

    def solicitar_fechamento(self):
        self._fechar_pendente = True
        self._cancelar_backup()
        if self._worker is not None:
            self._worker.repetir_fechamento_se_pendente()

    @pyqtSlot()
    def _fechamento_falhou(self):
        if self.em_andamento:
            self.btn_cancelar.setEnabled(False)
            self.btn_fechar.setText("Tentar fechar novamente")
            self.status.setText(
                "O fechamento do banco ficou pendente. Tente fechar novamente."
            )

    def preparar_saida(self):
        self._saida_da_janela = True
        if self.em_andamento:
            self.solicitar_fechamento()
        else:
            self.reject()

    def eventFilter(self, objeto, evento):
        if evento.type() == QEvent.Type.Quit and self.em_andamento:
            self._sair_pendente = True
            self.solicitar_fechamento()
            return True
        return super().eventFilter(objeto, evento)

    def closeEvent(self, evento):
        if self.em_andamento:
            self.solicitar_fechamento()
            evento.ignore()
        else:
            super().closeEvent(evento)

    def done(self, resultado):
        if self.em_andamento:
            self.solicitar_fechamento()
            return
        super().done(resultado)

    def accept(self):
        self.done(QDialog.DialogCode.Accepted)

    def reject(self):
        self.done(QDialog.DialogCode.Rejected)

    @pyqtSlot()
    def _backup_finalizado(self):
        worker = self._worker
        if worker is None:
            return
        if not worker.wait(0):
            QTimer.singleShot(10, self._backup_finalizado)
            return
        resultado, erro = worker.resultado, worker.erro
        self._worker = None
        worker.deleteLater()
        if not self._quit_coordenado:
            QApplication.instance().removeEventFilter(self)
        if self._saida_da_janela:
            self.reject()
            self.backup_finalizado.emit()
            return
        self.btn_criar.setEnabled(True)
        self.btn_cancelar.setEnabled(False)
        self.btn_fechar.setText("Fechar")
        self.progresso.setRange(0, 100)
        self.progresso.setValue(0)
        self.progresso.setFormat("Finalizado")
        if resultado is not None:
            avisos = list(resultado.avisos)
            if resultado.retencao is not None:
                avisos.extend(resultado.retencao.avisos)
            mensagens = self._mensagens_avisos(avisos)
            if self._cancelamento_solicitado:
                mensagens.append("O backup foi publicado apesar do pedido de cancelamento.")
            texto = f"Backup criado e validado:\n{resultado.caminho.name}"
            self.progresso.setValue(100)
            if mensagens:
                self.status.setText("Backup concluído com avisos.")
                QMessageBox.warning(self, "Backup criado com avisos", texto + "\n\n" + "\n".join(mensagens))
            else:
                self.status.setText("Backup concluído.")
                QMessageBox.information(self, "Backup concluído", texto)
        else:
            self._mostrar_erro(erro or ErroBackup("preparacao", "falha_inesperada"))
        if self._fechar_pendente:
            self.reject()
        if self._sair_pendente:
            QTimer.singleShot(0, QApplication.quit)
        self.backup_finalizado.emit()

    @staticmethod
    def _mensagens_avisos(avisos):
        mensagens = {
            "retencao_incompleta": "A limpeza dos backups antigos não foi concluída.",
            "retencao_nao_executada": "A limpeza dos backups antigos não foi executada.",
            "sincronizacao_diretorio_nao_confirmada": "A sincronização da pasta de backups não pôde ser confirmada.",
            "limpeza_staging_falhou": "A limpeza dos arquivos temporários não foi concluída.",
            "limpeza_lock_falhou": "A liberação do controle de operação não foi concluída; procure suporte antes de tentar novamente.",
            "lock_alterado_nao_removido": "O controle de operação mudou e foi preservado; procure suporte.",
            "candidato_nao_validado_preservado": "Arquivos que não puderam ser validados foram preservados.",
            "pos_publicacao_incompleta": "Uma etapa complementar após a publicação não foi concluída.",
        }
        return list(dict.fromkeys(mensagens.get(aviso.split(":", 1)[0],
                    "Uma etapa complementar não pôde ser concluída.") for aviso in avisos))

    def _mostrar_erro(self, erro):
        if erro.codigo == "cancelado":
            avisos = self._mensagens_avisos(erro.avisos)
            self.status.setText("Backup cancelado antes da publicação.")
            texto = "A criação foi cancelada antes da publicação de um backup."
            if avisos:
                self.status.setText("Backup cancelado antes da publicação, com avisos de limpeza.")
                QMessageBox.warning(self, "Backup cancelado com avisos", texto + "\n\n" + "\n".join(avisos))
            else:
                QMessageBox.information(self, "Backup cancelado", texto)
            return
        etapas = {"argumentos": "verificação dos caminhos", "preparacao": "preparação",
                  "barreira": "acesso ao banco", "fechamento": "fechamento do banco",
                  "exclusao": "controle de operação", "snapshot": "criação da cópia",
                  "validacao": "validação", "publicacao": "publicação", "retencao": "limpeza"}
        motivos = {
            "barreira_ocupado": "O banco está em manutenção. Tente novamente após a liberação.",
            "origem_fechamento_pendente": "O fechamento da origem falhou durante a operação.",
            "diretorio_inacessivel": "Não foi possível preparar a pasta de backups. Verifique o caminho e as permissões.",
            "pasta_ocupada": "Há uma operação ou controle residual na pasta. Não remova arquivos de controle sem orientação.",
            "prazo_excedido": "O tempo de espera foi excedido antes da publicação.",
            "integridade_fisica": "A cópia não passou na verificação de integridade física.",
            "integridade_referencial": "A cópia apresenta relacionamentos de dados inválidos.",
            "esquema_nao_reconhecido": "O banco não possui a estrutura mínima reconhecida do SEAGBH.",
            "origem_na_pasta_de_backups": "O banco de origem não pode estar na árvore da pasta de backups.",
            "backend_nao_suportado": "Esta funcionalidade atende somente instalações SQLite.",
        }
        motivo = motivos.get(erro.codigo,
            "Não foi possível concluir esta etapa. Verifique os caminhos, espaço disponível, "
            "permissões e suporte do destino a backups seguros; procure suporte se necessário.")
        texto = "A criação do backup não foi concluída.\nEtapa: " + etapas.get(erro.fase, "preparação") + ".\n" + motivo
        avisos = self._mensagens_avisos(erro.avisos)
        if avisos:
            texto += "\n\n" + "\n".join(avisos)
        self.status.setText("Falha: backup não concluído.")
        QMessageBox.critical(self, "Falha no backup", texto)
