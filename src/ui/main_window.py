"""
Janela principal — sidebar + páginas empilhadas.
"""

import os

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QStackedWidget, QPushButton, QLabel, QStatusBar,
    QMessageBox, QSizePolicy, QProgressDialog, QApplication,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QAction

from core.database import Database
from ui.tab_dashboard import TabDashboard
from ui.tab_eventos import TabEventos
from ui.tab_equipamentos import TabEquipamentos
from ui.tab_manutencao import TabManutencao
from ui.tab_relatorios import TabRelatorios
from ui.dialogs_utils import (
    AuditoriaDialog, RestaurarBackupDialog,
    PermissoesDialog, FazerBackupDialog,
)
from core.versao import __version__
from core.atualizacao import verificar_atualizacao, baixar_atualizacao, aplicar_atualizacao


class _DownloadThread(QThread):
    """Thread para baixar atualização sem travar a UI."""
    progresso = pyqtSignal(int, int)  # recebido, total
    concluido = pyqtSignal(bool, str)  # sucesso, caminho

    def __init__(self, url: str, destino: str, sha256_esperado: str | None = None):
        super().__init__()
        self._url = url
        self._destino = destino
        self._sha256 = sha256_esperado

    def run(self):
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        try:
            ok = baixar_atualizacao(
                self._url, self._destino,
                progresso_cb=lambda r, t: self.progresso.emit(r, t),
                sha256_esperado=self._sha256,  # ← Passa hash para validação
            )
            self.concluido.emit(ok, self._destino)
        except Exception as e:
            logger.error(f"Erro nao tratado na thread de download: {e}")
            self.concluido.emit(False, self._destino)


class _AtrasosThread(QThread):
    """Thread para verificar atrasos sem bloquear a interface."""
    pronto = pyqtSignal(list)
    falha = pyqtSignal(str)

    def run(self):
        import logging
        logger = logging.getLogger(__name__)
        try:
            db = Database()
            atrasados = db.eventos_atrasados()
            try:
                db.fechar()
            except Exception:
                pass
            self.pronto.emit(atrasados)
        except Exception as e:
            logger.exception("Falha ao verificar atrasos em background")
            self.falha.emit(str(e))


def _resource(filename: str) -> str:
    """Caminho absoluto para um arquivo em ui/."""
    return os.path.join(os.path.dirname(__file__), filename)


class MainWindow(QMainWindow):
    """Janela principal do SEAGBH — versão PyQt6 com sidebar."""

    _PAGINAS = [
        ("🏠  Painel",        0),
        ("📋  Eventos",       1),
        ("📦  Equipamentos",  2),
        ("🔧  Manutenção",    3),
        ("📊  Relatórios",    4),
    ]

    def __init__(self, alerta_vencimento: dict | None = None,
                 alerta_revogacao: dict | None = None):
        super().__init__()
        self.setWindowTitle("SEAGBH — Sistema de Almoxarifado")
        self.resize(1280, 720)

        # Info de alerta de pagamento (vindo de licenca_ativa)
        self._alerta_vencimento = alerta_vencimento

        # Info de alerta de revogação (grace period de 24h)
        self._alerta_revogacao = alerta_revogacao

        # Banco de dados
        self.db = Database()

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
        self._ir_para_seguro(0)

        # ── Checagem automática de atrasos (segura) ──────────────────────
        # Dispara uma única vez após a inicialização completa para reduzir risco
        # de contenção no startup e evitar pop-up modal inesperado.
        QTimer.singleShot(12000, self._checar_atrasos_auto)

        # ── Alerta de vencimento de licença (2 s após abrir) ─────────────
        if self._alerta_vencimento:
            QTimer.singleShot(2000, self._mostrar_alerta_vencimento)

        # ── Alerta de revogação de licença (1,5 s após abrir) ───────────
        if self._alerta_revogacao:
            QTimer.singleShot(1500, self._mostrar_alerta_revogacao)

    # ── Navegação ─────────────────────────────────────────────────────────

    def _ir_para(self, indice: int):
        """Troca a página e destaca o botão ativo na sidebar."""
        # Carrega a aba sob demanda se ainda não foi carregada
        self._carregar_aba_se_necessario(indice)
        
        self.stack.setCurrentIndex(indice)
        for i, btn in enumerate(self._sidebar_btns):
            btn.setProperty("active", "true" if i == indice else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        # Atualiza dashboard ao voltar para ele
        if indice == 0 and self.tab_dashboard:
            self.tab_dashboard.atualizar()

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
            self.tab_dashboard = TabDashboard(self.db, parent=self)
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

        act_restaurar = QAction("🔄 Restaurar Backup", self)
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
        AuditoriaDialog(self.db, parent=self).exec()

    def _fazer_backup(self):
        FazerBackupDialog(self.db, parent=self).exec()

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
        import logging
        logger = logging.getLogger(__name__)
        try:
            if self._atrasos_thread is not None and self._atrasos_thread.isRunning():
                return

            self._ultimo_modo_checar_atrasos = "manual"
            self._atrasos_thread = _AtrasosThread(self)
            self._atrasos_thread.pronto.connect(self._mostrar_atrasos)
            self._atrasos_thread.falha.connect(self._on_erro_checar_atrasos)
            self._atrasos_thread.start()
        except KeyboardInterrupt:
            logger.warning("Checagem de atrasos interrompida por KeyboardInterrupt")
        except Exception as e:
            logger.exception(f"Falha inesperada ao iniciar checagem de atrasos: {e}")

    def _checar_atrasos_auto(self):
        """Checa atrasos automaticamente uma única vez por sessão."""
        if self._atrasos_auto_executado:
            return
        self._atrasos_auto_executado = True

        import logging
        logger = logging.getLogger(__name__)
        try:
            if self._atrasos_thread is not None and self._atrasos_thread.isRunning():
                return

            self._ultimo_modo_checar_atrasos = "auto"
            self._atrasos_thread = _AtrasosThread(self)
            self._atrasos_thread.pronto.connect(self._mostrar_atrasos)
            self._atrasos_thread.falha.connect(self._on_erro_checar_atrasos)
            self._atrasos_thread.start()
        except Exception as e:
            logger.exception(f"Falha na checagem automatica de atrasos: {e}")

    def _mostrar_atrasos(self, atrasados: list):
        self._ultimo_atrasados = atrasados
        if not atrasados:
            self._btn_alerta_atrasos.setVisible(False)
            return

        msg = "Eventos vencidos com equipamentos pendentes:\n\n"
        for eid, nome, dt, pend in atrasados:
            msg += f"• #{eid} {nome} (fim: {dt}) - {pend} pendente(s)\n"

        if self._ultimo_modo_checar_atrasos == "auto":
            self._mostrar_aviso_atrasos_discreto(len(atrasados))
            return

        QMessageBox.warning(self, "⚠️ Eventos Atrasados", msg)

    def _mostrar_aviso_atrasos_discreto(self, total_atrasados: int):
        """Mostra aviso não-bloqueante para checagem automática de atrasos."""
        self._btn_alerta_atrasos.setText(f"⚠️ {total_atrasados} atraso(s)")
        self._btn_alerta_atrasos.setToolTip(
            "Existem eventos atrasados. Clique para abrir os detalhes."
        )
        self._btn_alerta_atrasos.setVisible(True)
        self.statusBar().showMessage(
            f"⚠️ {total_atrasados} evento(s) com atraso e pendências. Menu: Utilitários > Checar Atrasos Agora.",
            12000,
        )

    def _on_erro_checar_atrasos(self, erro: str):
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Erro ao checar atrasos: {erro}")

    # ── Alerta de vencimento de licença ──────────────────────────────

    def _mostrar_alerta_vencimento(self):
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
        self.btn_atualizar.setEnabled(False)
        self.btn_atualizar.setText("🔄  Verificando…")
        QApplication.processEvents()

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
        if resposta != QMessageBox.StandardButton.Yes:
            return

        if not url:
            QMessageBox.warning(self, "Erro", "URL de download não disponível.")
            return

        self._iniciar_download(url, versao, sha256=sha256)  # ← Passa hash

    def _iniciar_download(self, url: str, versao: str, sha256: str | None = None):
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

        self._dl_thread = _DownloadThread(url, destino, sha256_esperado=sha256)
        self._dl_thread.progresso.connect(self._atualizar_progresso)
        self._dl_thread.concluido.connect(self._download_concluido)
        self._prog.canceled.connect(self._dl_thread.terminate)
        self._dl_thread.start()

    def _atualizar_progresso(self, recebido: int, total: int):
        if total > 0:
            self._prog.setValue(int(recebido * 100 / total))
        else:
            self._prog.setLabelText(f"Baixando… {recebido // 1024} KB")

    def _download_concluido(self, sucesso: bool, caminho: str):
        import logging
        logger = logging.getLogger(__name__)
        self._prog.close()

        if not sucesso:
            logger.error(f"Falha no download de atualização: {caminho}. SHA256 invalido ou conexao perdida.")
            QMessageBox.critical(
                self, "Erro",
                "Falha ao baixar a atualização.\n"
                "Arquivo corrompido ou conexão perdida.\n"
                "Verifique sua conexão e tente novamente.",
            )
            # Limpeza automática de arquivo corrompido
            if os.path.exists(caminho):
                try:
                    os.remove(caminho)
                except OSError:
                    pass
            return

        logger.info(f"Download de atualização concluido com sucesso: {caminho}")
        QMessageBox.information(
            self, "Download Concluído",
            "O download foi concluído com sucesso.\n"
            "O aplicativo será fechado e reiniciado automaticamente.",
        )

        try:
            bat_path = aplicar_atualizacao(caminho)
        except RuntimeError as exc:
            logger.error(f"Erro ao preparar atualização: {exc}")
            QMessageBox.warning(self, "Atualização", str(exc))
            # Limpar arquivo se update falhar
            if os.path.exists(caminho):
                try:
                    os.remove(caminho)
                except OSError:
                    pass
            return

        # ── CORRETO: Dar tempo para .bat começar a trabalhar ──────────────────
        # .bat vai esperar o .exe liberar. Se encerrarmos AGORA, haverá race condition.
        # Aguardamos 4 segundos para o .bat se estabelecer em sistemas lentos.
        logger.info(f"Script de atualização preparado: {bat_path}. Aguardando 4s antes de encerrar aplicacao.")
        QTimer.singleShot(4000, QApplication.quit)

    # ── Fechamento limpo ──────────────────────────────────────────────────

    def closeEvent(self, event):
        self.db.fechar()
        event.accept()
