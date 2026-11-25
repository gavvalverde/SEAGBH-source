"""
Janela principal — sidebar + páginas empilhadas.
"""

import logging
import os
from functools import partial
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QStackedWidget, QPushButton, QLabel, QStatusBar,
    QMessageBox, QSizePolicy, QProgressDialog, QApplication, QDialog,
)
from PyQt6.QtCore import QEvent, Qt, QTimer, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction

from core.database import Database
from core.conexao_sqlite_protegida import FechamentoPendente
from ui.consulta_database_thread import ConsultaDatabaseThread
from ui.tab_dashboard import TabDashboard
from ui.tab_eventos import TabEventos
from ui.tab_equipamentos import TabEquipamentos
from ui.tab_manutencao import TabManutencao
from ui.dialogs_utils import (
    AuditoriaDialog, RestaurarBackupDialog,
    PermissoesDialog, FazerBackupDialog,
)
from core.versao import __version__
from core.atualizacao import (
    verificar_atualizacao, baixar_atualizacao, validar_aplicacao_atualizacao,
    aplicar_atualizacao,
)


class _DownloadThread(QThread):
    """Thread para baixar atualização sem travar a UI."""
    progresso = pyqtSignal(int, int)  # recebido, total

    def __init__(self, url, destino, sha256_esperado, parent=None):
        super().__init__(parent)
        self._url = url
        self.destino = destino
        self._sha256 = sha256_esperado
        self.sucesso = False
        self.cancelado = False

    def cancelar(self):
        self.cancelado = True
        self.requestInterruption()

    def run(self):
        try:
            self.sucesso = baixar_atualizacao(
                self._url, self.destino,
                progresso_cb=lambda r, t: self.progresso.emit(r, t),
                sha256_esperado=self._sha256,
                cancelado=self.isInterruptionRequested,
            )
        except BaseException:
            logging.getLogger(__name__).error("Falha inesperada no download da atualização")


class _AtrasosThread(ConsultaDatabaseThread):
    """Thread para verificar atrasos sem bloquear a interface."""
    def consultar(self, db):
        return db.classificar_eventos_com_pendencias()


def _resource(filename: str) -> str:
    """Caminho absoluto para um arquivo em ui/."""
    return os.path.join(os.path.dirname(__file__), filename)


class MainWindow(QMainWindow):
    """Janela principal do SEAGBH — versão PyQt6 com sidebar."""

    _ORIENTACAO_ATRASOS = "Menu: Utilitários > Checar Atrasos Agora."
    _FALHA_ATRASOS = "Não foi possível consultar os atrasos."

    _PAGINAS = [
        ("🏠  Painel",        0),
        ("📋  Eventos",       1),
        ("📦  Equipamentos",  2),
        ("🔧  Manutenção",    3),
        ("📊  Relatórios",    4),
    ]

    def __init__(self, alerta_vencimento: dict | None = None,
                 alerta_revogacao: dict | None = None, *,
                 banco_sqlite_protegido: str | Path | None = None):
        super().__init__()
        self.setWindowTitle("SEAGBH — Sistema de Almoxarifado")
        self.resize(1280, 720)

        # Info de alerta de pagamento (vindo de licenca_ativa)
        self._alerta_vencimento = alerta_vencimento

        # Info de alerta de revogação (grace period de 24h)
        self._alerta_revogacao = alerta_revogacao

        # Banco de dados
        self._sqlite_protegido = banco_sqlite_protegido is not None
        self._abrir_banco = (partial(Database, banco_sqlite_protegido=banco_sqlite_protegido)
                             if self._sqlite_protegido else Database)
        self._encerrando = False
        self.encerramento_confirmado = False
        self._sair_da_aplicacao = False
        self._fechamento_principal_pendente = False
        self._atualizacao_pendente = None
        self._backup_dialog = None
        self._dl_thread = None

        self.db = self._abrir_banco()
        try:
            self._montar_interface()
            QApplication.instance().installEventFilter(self)
            self._timer_atrasos.start(12000)
            self.tab_dashboard.agendar_carregamento_inicial()
        except BaseException:
            self._encerrando = True
            QApplication.instance().removeEventFilter(self)
            try:
                self.db.fechar_para_gui(confirmar=False)
            except BaseException as erro_fechamento:
                raise FechamentoPendente(partial(self.db.fechar_para_gui, confirmar=False)) from erro_fechamento
            raise

    def _montar_interface(self):
        # Tema (começa escuro)
        self._tema_escuro = True

        # ── Abas (carregamento lazy) ───────────────────────────────────────
        self.tab_dashboard = None
        self.tab_eventos = None
        self.tab_equipamentos = None
        self.tab_manutencao = None
        self.tab_relatorios = None
        self._atrasos_thread = None
        self._atrasos_auto_executado = False
        self._ultimo_modo_checar_atrasos = "manual"
        self._ultimo_atrasados = []

        # ── Layout central: sidebar + conteúdo ────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        layout_h = QHBoxLayout(central)
        layout_h.setContentsMargins(0, 0, 0, 0)
        layout_h.setSpacing(0)

        # ── Sidebar ───────────────────────────────────────────────────────
        self.sidebar = QWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(220)
        side_layout = QVBoxLayout(self.sidebar)
        side_layout.setContentsMargins(12, 20, 12, 20)
        side_layout.setSpacing(6)

        # Logo / título
        lbl_titulo = QLabel("SEAGBH")
        lbl_titulo.setObjectName("sidebar-title")
        lbl_titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side_layout.addWidget(lbl_titulo)

        lbl_versao = QLabel(f"v{__version__}")
        lbl_versao.setObjectName("sidebar-version")
        lbl_versao.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side_layout.addWidget(lbl_versao)

        side_layout.addSpacing(24)

        # Botões de navegação
        self._sidebar_btns: list[QPushButton] = []
        for texto, idx in self._PAGINAS:
            btn = QPushButton(texto)
            btn.setProperty("class", "sidebar-btn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, i=idx: self._ir_para_seguro(i))
            side_layout.addWidget(btn)
            self._sidebar_btns.append(btn)

        side_layout.addStretch()

        # Botão de atualização na sidebar
        self.btn_atualizar = QPushButton("🔄  Atualizações")
        self.btn_atualizar.setProperty("class", "sidebar-btn")
        self.btn_atualizar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_atualizar.clicked.connect(self._verificar_atualizacao)
        side_layout.addWidget(self.btn_atualizar)

        # Botão de tema na sidebar
        self.btn_tema = QPushButton("☀️  Tema Claro")
        self.btn_tema.setProperty("class", "sidebar-btn")
        self.btn_tema.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tema.clicked.connect(self._alternar_tema)
        side_layout.addWidget(self.btn_tema)

        layout_h.addWidget(self.sidebar)

        # ── Área de conteúdo (páginas empilhadas) ─────────────────────────
        self.stack = QStackedWidget()
        layout_h.addWidget(self.stack, stretch=1)

        # Cria abas vazias (carregadas sob demanda)
        for i in range(5):
            self.stack.addWidget(QWidget())  # Placeholder vazio

        # Aplica tema (antes de ativar a primeira página)
        self._aplicar_tema()

        # ── Status bar ────────────────────────────────────────────────────
        status = QStatusBar()
        status.addPermanentWidget(
            QLabel(f"By Gabriel do Amaral Valverde | SEAGBH v{__version__} (PyQt6)")
        )
        self._btn_alerta_atrasos = QPushButton("⚠️ Atrasos")
        self._btn_alerta_atrasos.setProperty("class", "sidebar-btn")
        self._btn_alerta_atrasos.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_alerta_atrasos.setVisible(False)
        self._btn_alerta_atrasos.clicked.connect(self._checar_atrasos)
        status.addPermanentWidget(self._btn_alerta_atrasos)
        self.setStatusBar(status)

        # ── Menu ──────────────────────────────────────────────────────────
        self._criar_menu()

        # ── Ativa o painel inicial (e carrega a primeira aba) ──────────────
        self._ir_para(0, atualizar=False)

        # ── Checagem automática de atrasos (segura) ──────────────────────
        # Dispara uma única vez após a inicialização completa para reduzir risco
        # de contenção no startup e evitar pop-up modal inesperado.
        self._timer_atrasos = QTimer(self)
        self._timer_atrasos.setSingleShot(True)
        self._timer_atrasos.timeout.connect(self._checar_atrasos_auto)

        # ── Alerta de vencimento de licença (2 s após abrir) ─────────────
        if self._alerta_vencimento:
            QTimer.singleShot(2000, self._mostrar_alerta_vencimento)

        # ── Alerta de revogação de licença (1,5 s após abrir) ───────────
        if self._alerta_revogacao:
            QTimer.singleShot(1500, self._mostrar_alerta_revogacao)

    # ── Navegação ─────────────────────────────────────────────────────────

    def _ir_para(self, indice: int, *, atualizar=True):
        """Troca a página e destaca o botão ativo na sidebar."""
        # Carrega a aba sob demanda se ainda não foi carregada
        if self._encerrando:
            return
        equipamentos_abertos = indice == 2 and self.tab_equipamentos is not None
        self._carregar_aba_se_necessario(indice)

        self.stack.setCurrentIndex(indice)
        for i, btn in enumerate(self._sidebar_btns):
            btn.setProperty("active", "true" if i == indice else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        # Atualiza dashboard ao voltar para ele
        if atualizar and indice == 0 and self.tab_dashboard:
            self.tab_dashboard.atualizar()
        elif atualizar and equipamentos_abertos:
            self.tab_equipamentos.atualizar_disponibilidade()

    def _ir_para_seguro(self, indice: int):
        """Wrapper de navegação para evitar queda da aplicação por exceções."""
        import logging
        logger = logging.getLogger(__name__)
        try:
            self._ir_para(indice)
        except KeyboardInterrupt:
            logger.warning(f"Navegação interrompida na aba {indice}")
        except Exception as e:
            logger.exception(f"Erro ao navegar para aba {indice}: {e}")

    def _carregar_aba_se_necessario(self, indice: int):
        """Carrega a aba sob demanda (lazy loading)."""
        if indice == 0 and self.tab_dashboard is None:
            self.tab_dashboard = TabDashboard(self.db, parent=self, abrir_banco=self._abrir_banco)
            self.tab_dashboard.consulta_finalizada.connect(self._continuar_encerramento)
            self.tab_dashboard.fechamento_falhou.connect(self._mostrar_fechamento_pendente)
            widget_antigo = self.stack.widget(0)
            self.stack.removeWidget(widget_antigo)
            self.stack.insertWidget(0, self.tab_dashboard)
        elif indice == 1 and self.tab_eventos is None:
            self.tab_eventos = TabEventos(self.db, parent=self)
            widget_antigo = self.stack.widget(1)
            self.stack.removeWidget(widget_antigo)
            self.stack.insertWidget(1, self.tab_eventos)
        elif indice == 2 and self.tab_equipamentos is None:
            self.tab_equipamentos = TabEquipamentos(self.db, parent=self)
            widget_antigo = self.stack.widget(2)
            self.stack.removeWidget(widget_antigo)
            self.stack.insertWidget(2, self.tab_equipamentos)
        elif indice == 3 and self.tab_manutencao is None:
            self.tab_manutencao = TabManutencao(self.db, parent=self)
            widget_antigo = self.stack.widget(3)
            self.stack.removeWidget(widget_antigo)
            self.stack.insertWidget(3, self.tab_manutencao)
        elif indice == 4 and self.tab_relatorios is None:
            from ui.tab_relatorios import TabRelatorios

            self.tab_relatorios = TabRelatorios(self.db, parent=self)
            widget_antigo = self.stack.widget(4)
            self.stack.removeWidget(widget_antigo)
            self.stack.insertWidget(4, self.tab_relatorios)

    # ── Menu ──────────────────────────────────────────────────────────────

    def _criar_menu(self):
        menu = self.menuBar()

        util_menu = menu.addMenu("⚙️ Utilitários")

        act_tema = QAction("☀️ Alternar Tema  (Ctrl+T)", self)
        act_tema.setShortcut("Ctrl+T")
        act_tema.triggered.connect(self._alternar_tema)
        util_menu.addAction(act_tema)

        act_audit = QAction("📜 Histórico de Alterações", self)
        act_audit.triggered.connect(self._abrir_auditoria)
        util_menu.addAction(act_audit)

        act_backup = QAction("💾 Fazer Backup", self)
        act_backup.triggered.connect(self._fazer_backup)
        util_menu.addAction(act_backup)

        act_restaurar = QAction("🔄 Restaurar Backup (indisponível)", self)
        act_restaurar.setEnabled(False)
        act_restaurar.triggered.connect(self._restaurar_backup)
        util_menu.addAction(act_restaurar)

        act_perm = QAction("🛡️ Verificar Permissões", self)
        act_perm.triggered.connect(self._verificar_permissoes)
        util_menu.addAction(act_perm)

        act_update = QAction("🔄 Verificar Atualizações", self)
        act_update.triggered.connect(self._verificar_atualizacao)
        util_menu.addAction(act_update)

        act_atrasos = QAction("⚠️ Checar Atrasos Agora", self)
        act_atrasos.triggered.connect(self._checar_atrasos)
        util_menu.addAction(act_atrasos)

    # ── Utilitários ───────────────────────────────────────────────────────

    def _abrir_auditoria(self):
        if not self._encerrando:
            AuditoriaDialog(self.db, parent=self).exec()

    def _fazer_backup(self):
        if self._encerrando or self._backup_dialog is not None:
            return
        self._backup_dialog = FazerBackupDialog(self.db, parent=self, quit_coordenado=True)
        try:
            self._backup_dialog.exec()
        finally:
            if not self._backup_dialog.em_andamento:
                self._liberar_dialogo_backup()
            else:
                # Um exit() externo pode desenrolar o exec() modal sem fechar
                # o worker. O diálogo permanece vivo até o sinal de término.
                self._backup_dialog.backup_finalizado.connect(self._liberar_dialogo_backup)

    @pyqtSlot()
    def _liberar_dialogo_backup(self):
        self._backup_dialog.deleteLater()
        self._backup_dialog = None
        self._continuar_encerramento()

    def _restaurar_backup(self):
        RestaurarBackupDialog(parent=self).exec()

    def _verificar_permissoes(self):
        PermissoesDialog(parent=self).exec()

    # ── Tema ──────────────────────────────────────────────────────────────

    def _aplicar_tema(self):
        arquivo = "styles_dark.qss" if self._tema_escuro else "styles_light.qss"
        caminho = _resource(arquivo)
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                self.setStyleSheet(f.read())
        except FileNotFoundError:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Arquivo de estilo não encontrado: {caminho}")

    def _alternar_tema(self):
        self._tema_escuro = not self._tema_escuro
        self._aplicar_tema()
        # Atualiza texto do botão de tema
        self.btn_tema.setText(
            "☀️  Tema Claro" if self._tema_escuro else "🌙  Tema Escuro"
        )
        # Recolorir linhas de todas as tabelas
        if self.tab_eventos is not None:
            self.tab_eventos.atualizar_lista()
        if self.tab_equipamentos is not None:
            self.tab_equipamentos.listar_todos()
        if self.tab_manutencao is not None:
            self.tab_manutencao.atualizar_lista()

    # ── Atrasos ───────────────────────────────────────────────────────────

    def _checar_atrasos(self):
        self._iniciar_atrasos("manual")

    def _checar_atrasos_auto(self):
        """Checa atrasos automaticamente uma única vez por sessão."""
        if self._encerrando or self._atrasos_auto_executado:
            return
        self._atrasos_auto_executado = True
        self._iniciar_atrasos("auto")

    def _iniciar_atrasos(self, modo):
        if self._encerrando or self._atrasos_thread is not None:
            return
        self._ultimo_modo_checar_atrasos = modo
        worker = _AtrasosThread(self._abrir_banco, self)
        self._atrasos_thread = worker
        worker.pronto.connect(self._mostrar_atrasos)
        worker.falha.connect(self._on_erro_checar_atrasos)
        worker.fechamento_falhou.connect(self._mostrar_fechamento_pendente)
        worker.finished.connect(self._atrasos_finalizado)
        worker.start()

    @pyqtSlot()
    def _atrasos_finalizado(self):
        worker = self._atrasos_thread
        if not worker.wait(0):
            QTimer.singleShot(10, self._atrasos_finalizado)
            return
        self._atrasos_thread = None
        worker.deleteLater()
        self._continuar_encerramento()

    @pyqtSlot(object)
    def _mostrar_atrasos(self, resultado):
        if self._encerrando or self.sender() is not self._atrasos_thread:
            return
        atrasados, datas_invalidas = resultado
        self._ultimo_atrasados = atrasados
        resumo = self._atualizar_badge_atrasos(len(atrasados), len(datas_invalidas))
        barra = self.statusBar()
        if resumo and self._ultimo_modo_checar_atrasos == "auto":
            barra.showMessage(f"⚠️ {resumo}. {self._ORIENTACAO_ATRASOS}", 12000)
            return
        mensagem = barra.currentMessage()
        if mensagem == self._FALHA_ATRASOS or mensagem.endswith(self._ORIENTACAO_ATRASOS):
            barra.clearMessage()
        if not resumo:
            return

        secoes = []
        if atrasados:
            secoes.append("Eventos vencidos com equipamentos pendentes:\n" + "\n".join(
                f"• #{eid} {nome} (fim: {dt}) - {pend} pendente(s)"
                for eid, nome, dt, pend in atrasados
            ))
        if datas_invalidas:
            secoes.append("Datas a revisar na aba Eventos; não foi possível determinar o atraso:\n"
                          + "\n".join(f"• #{eid} {nome} - {pend} pendente(s)"
                                      for eid, nome, _, pend in datas_invalidas))
        QMessageBox.warning(self, "⚠️ Atrasos e datas de eventos", "\n\n".join(secoes))

    def _atualizar_badge_atrasos(self, total_atrasados: int, total_datas_invalidas: int) -> str:
        """Sincroniza o botão com a última consulta bem-sucedida."""
        rotulos = []
        if total_atrasados:
            rotulos.append(f"{total_atrasados} atraso(s)")
        if total_datas_invalidas:
            rotulos.append(f"{total_datas_invalidas} data(s) a revisar")
        resumo = " e ".join(rotulos)
        self._btn_alerta_atrasos.setText(f"⚠️ {resumo}" if resumo else "⚠️ Atrasos")
        self._btn_alerta_atrasos.setToolTip("Clique para conferir atrasos e datas pendentes de revisão.")
        self._btn_alerta_atrasos.setVisible(bool(resumo))
        return resumo

    @pyqtSlot()
    def _on_erro_checar_atrasos(self):
        if not self._encerrando and self.sender() is self._atrasos_thread:
            self.statusBar().showMessage(self._FALHA_ATRASOS, 12000)

    # ── Alerta de vencimento de licença ──────────────────────────────

    def _mostrar_alerta_vencimento(self):
        if self._encerrando:
            return
        info = self._alerta_vencimento
        if not info:
            return

        dias = info["dias_restantes"]
        data = info["data_vencimento"]
        plano = info.get("plano", "").capitalize()

        if dias <= 3:
            cor_fundo = "#7F1D1D"
            cor_borda = "#F87171"
            cor_texto = "#FCA5A5"
        elif dias <= 7:
            cor_fundo = "#78350F"
            cor_borda = "#FBBF24"
            cor_texto = "#FDE68A"
        else:
            cor_fundo = "#1E3A5F"
            cor_borda = "#6BD1FF"
            cor_texto = "#BAE6FD"

        toast = QLabel(self)
        toast.setObjectName("alerta-vencimento")
        toast.setText(
            f"  Vencimento em {dias} dia{'s' if dias != 1 else ''}"
            f"  •  Plano {plano} expira em {data}"
            f"  •  Efetue o pagamento para continuar usando o sistema  "
        )
        toast.setStyleSheet(f"""
            QLabel#alerta-vencimento {{
                background-color: {cor_fundo};
                color: {cor_texto};
                border: 1px solid {cor_borda};
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
                padding: 12px 20px;
            }}
        """)
        toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        toast.adjustSize()

        # Posicionar no topo central da área de conteúdo
        x = self.sidebar.width() + (self.stack.width() - toast.width()) // 2
        toast.move(max(x, self.sidebar.width() + 10), 8)
        toast.raise_()
        toast.show()

        QTimer.singleShot(10000, toast.deleteLater)

    # ── Alerta de revogação de licença (grace period) ───────────────

    def _mostrar_alerta_revogacao(self):
        if self._encerrando:
            return
        """Exibe toast de aviso quando a licença está em grace period (24h)."""
        info = self._alerta_revogacao
        if not info:
            return

        mensagem = info.get("mensagem", "Licença revogada/suspensa.")
        prazo_fmt = info.get("prazo_fmt", "")

        # Construir texto do toast usando prazo_fmt quando disponível
        if prazo_fmt:
            texto_toast = f"  ⚠️  Licença revogada/suspensa. Você tem 24 horas para regularizar.\n📅 Prazo: {prazo_fmt}  "
        else:
            texto_toast = f"  ⚠️  {mensagem}  "

        toast = QLabel(self)
        toast.setObjectName("alerta-revogacao")
        toast.setText(texto_toast)
        toast.setStyleSheet(f"""
            QLabel#alerta-revogacao {{
                background-color: #7F1D1D;
                color: #FCA5A5;
                border: 1px solid #F87171;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
                padding: 12px 20px;
            }}
        """)
        toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        toast.setWordWrap(True)
        toast.setMinimumWidth(400)
        toast.adjustSize()

        # Posicionar no topo central da área de conteúdo
        x = self.sidebar.width() + (self.stack.width() - toast.width()) // 2
        toast.move(max(x, self.sidebar.width() + 10), 8)
        toast.raise_()
        toast.show()

        QTimer.singleShot(15000, toast.deleteLater)

    # ── Atualização ───────────────────────────────────────────────────────

    def _verificar_atualizacao(self):
        """Consulta Firebase por nova versão e oferece download."""
        if self._encerrando or self._dl_thread is not None:
            return
        self.btn_atualizar.setEnabled(False)
        self.btn_atualizar.setText("🔄  Verificando…")
        QApplication.processEvents()

        if self._encerrando:
            return
        try:
            info = verificar_atualizacao()
        except Exception as e:
            import logging
            logging.error(f"Erro ao verificar atualizações: {e}")
            info = None
        finally:
            self.btn_atualizar.setEnabled(True)
            self.btn_atualizar.setText("🔄  Atualizações")

        if info is None:
            QMessageBox.information(
                self, "Atualizações",
                f"Você já está na versão mais recente (v{__version__}).",
            )
            return

        versao = info.get("version", "?")
        changelog = info.get("changelog", "Sem detalhes.")
        data = info.get("data", "")
        url = info.get("url", "")
        sha256 = info.get("sha256")  # ← Novo: recupera hash para validação

        resposta = QMessageBox.question(
            self,
            "Nova Atualização Disponível",
            f"Versão {versao}  ({data})\n\n"
            f"{changelog}\n\n"
            f"Deseja baixar e instalar agora?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if self._encerrando or resposta != QMessageBox.StandardButton.Yes:
            return

        if not url:
            QMessageBox.warning(self, "Erro", "URL de download não disponível.")
            return

        self._iniciar_download(url, versao, sha256=sha256)  # ← Passa hash

    def _iniciar_download(self, url: str, versao: str, sha256: str | None = None):
        if self._encerrando or self._dl_thread is not None:
            return
        import tempfile
        destino = os.path.join(tempfile.gettempdir(), f"SEAGBH_v{versao}.exe")

        self._prog = QProgressDialog(
            f"Baixando SEAGBH v{versao}…", "Cancelar", 0, 100, self
        )
        self._prog.setWindowTitle("Atualização")
        self._prog.setMinimumWidth(380)
        self._prog.setAutoClose(False)
        self._prog.setAutoReset(False)
        self._prog.show()

        self._dl_thread = _DownloadThread(url, destino, sha256, self)
        self._dl_thread.progresso.connect(self._atualizar_progresso)
        self._dl_thread.finished.connect(self._download_concluido)
        self._prog.canceled.connect(self._dl_thread.cancelar)
        self._dl_thread.start()

    @pyqtSlot(int, int)
    def _atualizar_progresso(self, recebido: int, total: int):
        if (self._encerrando or self._dl_thread is None
                or self.sender() is not self._dl_thread or self._dl_thread.cancelado):
            return
        if total > 0:
            self._prog.setValue(int(recebido * 100 / total))
        else:
            self._prog.setLabelText(f"Baixando… {recebido // 1024} KB")

    @pyqtSlot()
    def _download_concluido(self):
        worker = self._dl_thread
        if not worker.wait(0):
            QTimer.singleShot(10, self._download_concluido)
            return
        self._prog.canceled.disconnect(worker.cancelar)
        self._prog.close()
        self._prog.deleteLater()
        self._dl_thread = None
        worker.deleteLater()
        caminho = worker.destino

        if self._encerrando or worker.cancelado:
            if worker.sucesso:
                self._limpar_download(caminho)
            self._continuar_encerramento()
            return
        if not worker.sucesso:
            QMessageBox.critical(
                self, "Erro",
                "Falha ao baixar a atualização.\n"
                "Arquivo corrompido ou conexão perdida.\n"
                "Verifique sua conexão e tente novamente.",
            )
            return

        try:
            validar_aplicacao_atualizacao(caminho)
        except RuntimeError as exc:
            self._limpar_download(caminho)
            QMessageBox.warning(self, "Atualização", str(exc))
            return
        QMessageBox.information(
            self, "Download Concluído",
            "O download foi concluído com sucesso.\n"
            "O aplicativo será fechado e reiniciado automaticamente.",
        )

        if self._encerrando:
            self._limpar_download(caminho)
            return
        self._atualizacao_pendente = caminho
        self.solicitar_saida()

    @staticmethod
    def _limpar_download(caminho):
        try:
            os.remove(caminho)
        except FileNotFoundError:
            pass
        except OSError:
            logging.getLogger(__name__).warning("Falha ao remover download temporário")

    # ── Fechamento limpo ──────────────────────────────────────────────────

    def closeEvent(self, event):
        if self.encerramento_confirmado:
            event.accept()
            return
        event.ignore()
        if self._encerrando:
            self._fechamento_principal_pendente = False
            if self._backup_dialog is not None and self._backup_dialog.em_andamento:
                self._backup_dialog.solicitar_fechamento()
            if self._atrasos_thread is not None:
                self._atrasos_thread.repetir_fechamento()
            if self.tab_dashboard is not None:
                self.tab_dashboard.solicitar_encerramento(repetir=True)
        else:
            self._iniciar_encerramento()
        self._agendar_encerramento()

    def eventFilter(self, objeto, evento):
        if (evento.type() == QEvent.Type.Quit and objeto is QApplication.instance()
                and not self.encerramento_confirmado):
            self.solicitar_saida()
            return True
        return super().eventFilter(objeto, evento)

    def solicitar_saida(self):
        """Encaminha o quit do Qt e a saída após atualização ao mesmo fluxo."""
        self._sair_da_aplicacao = True
        self.close()

    def _iniciar_encerramento(self):
        self._encerrando = True
        self._timer_atrasos.stop()
        self.centralWidget().setEnabled(False)
        self.menuBar().setEnabled(False)
        self._btn_alerta_atrasos.setEnabled(False)
        if self.tab_dashboard is not None:
            # A worker may have failed to close its SQLite connection BEFORE
            # the first exit request; wake ONLY that pending retry.
            self.tab_dashboard.solicitar_encerramento(repetir_pendente=True)
        if self._atrasos_thread is not None:
            self._atrasos_thread.requestInterruption()
            self._atrasos_thread.repetir_fechamento_se_pendente()
        if self._dl_thread is not None:
            self._dl_thread.cancelar()
        if self._backup_dialog is not None:
            self._backup_dialog.preparar_saida()
        self.statusBar().showMessage("Encerrando… aguardando operações em andamento.")

    def _agendar_encerramento(self):
        # O close original e qualquer diálogo modal podem estar na pilha do Qt.
        QTimer.singleShot(0, self._continuar_encerramento)

    @pyqtSlot()
    def _continuar_encerramento(self):
        if (not self._encerrando or self.encerramento_confirmado
                or self._fechamento_principal_pendente):
            return

        # A árvore QObject inclui os diálogos filhos mesmo em janelas próprias.
        modal = QApplication.activeModalWidget()
        if (isinstance(modal, QDialog) and modal is not self._backup_dialog
                and modal in self.findChildren(QDialog)):
            modal.reject()
            self._agendar_encerramento()
            return

        if (self._atrasos_thread is not None or self._dl_thread is not None
                or self._backup_dialog is not None
                or (self.tab_dashboard is not None and self.tab_dashboard.consultando)):
            return
        try:
            self.db.fechar_para_gui()
        except BaseException:
            self._fechamento_principal_pendente = True
            logging.getLogger(__name__).error("Fechamento do banco principal pendente")
            self._mostrar_fechamento_pendente()
            return

        self.encerramento_confirmado = True
        QApplication.instance().removeEventFilter(self)
        if self._atualizacao_pendente is not None:
            try:
                aplicar_atualizacao(self._atualizacao_pendente)
            except (OSError, RuntimeError):
                self._limpar_download(self._atualizacao_pendente)
                QMessageBox.warning(self, "Atualização", "Não foi possível iniciar o instalador.")
        self.close()
        if self._sair_da_aplicacao:
            QTimer.singleShot(0, QApplication.quit)

    @pyqtSlot()
    def _mostrar_fechamento_pendente(self):
        self.statusBar().showMessage(
            "Não foi possível fechar o banco. Solicite a saída novamente para repetir a tentativa."
        )
