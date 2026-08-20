"""Diálogo de ativação de licença — PyQt6."""

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QMessageBox,
    QGraphicsOpacityEffect,
    QStackedWidget,
    QWidget,
)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont

from core.licenca import validar_chave, chave_expirada, salvar_licenca, dias_restantes


class LicencaDialog(QDialog):
    """Diálogo para inserir código de ativação."""

    def __init__(self, mensagem: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ativação de Licença — SEAGBH")
        self.setFixedSize(560, 380)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
        )
        self._ativado = False
        self._build_ui(mensagem)
        self._apply_style()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self, mensagem: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        # Página 0: formulário de ativação
        self.stack.addWidget(self._build_form_page(mensagem))
        # Página 1: tela de sucesso
        self.stack.addWidget(self._build_success_page())

    def _build_form_page(self, mensagem: str) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(36, 32, 36, 32)
        lay.setSpacing(14)

        titulo = QLabel("🔐  Ativação do Sistema")
        titulo.setObjectName("titulo-licenca")
        lay.addWidget(titulo)

        if mensagem:
            lbl_msg = QLabel(mensagem)
            lbl_msg.setObjectName("msg-licenca")
            lbl_msg.setWordWrap(True)
            lay.addWidget(lbl_msg)

        lay.addSpacing(4)

        lbl = QLabel("Cole seu código de ativação:")
        lbl.setStyleSheet("font-weight: 600; font-size: 13px;")
        lay.addWidget(lbl)

        self.entry_chave = QLineEdit()
        self.entry_chave.setPlaceholderText("Ex: eyJpZCI6IjEyM2FiYzQ1Ni...")
        self.entry_chave.setMinimumHeight(44)
        self.entry_chave.returnPressed.connect(self._on_ativar)
        lay.addWidget(self.entry_chave)

        lay.addSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(12)

        btn_ativar = QPushButton("✅  Ativar Licença")
        btn_ativar.setObjectName("btn-ativar")
        btn_ativar.setMinimumHeight(42)
        btn_ativar.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_ativar.clicked.connect(self._on_ativar)
        bar.addWidget(btn_ativar)

        btn_sair = QPushButton("Sair")
        btn_sair.setObjectName("btn-sair")
        btn_sair.setMinimumHeight(42)
        btn_sair.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_sair.clicked.connect(self.reject)
        bar.addWidget(btn_sair)

        lay.addLayout(bar)
        lay.addStretch()
        return page

    def _build_success_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("success-page")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(36, 0, 36, 32)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(8)

        # Ícone grande
        icone = QLabel("✅")
        icone.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icone.setStyleSheet("font-size: 52px; background: transparent;")
        lay.addWidget(icone)

        lay.addSpacing(4)

        self.lbl_titulo_sucesso = QLabel("Licença Ativada!")
        self.lbl_titulo_sucesso.setObjectName("titulo-sucesso")
        self.lbl_titulo_sucesso.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_titulo_sucesso)

        self.lbl_detalhes = QLabel("")
        self.lbl_detalhes.setObjectName("detalhes-sucesso")
        self.lbl_detalhes.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_detalhes.setWordWrap(True)
        lay.addWidget(self.lbl_detalhes)

        lay.addSpacing(12)

        self.lbl_entrando = QLabel("Entrando no sistema...")
        self.lbl_entrando.setObjectName("entrando")
        self.lbl_entrando.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_entrando)

        return page

    # ── Estilo ────────────────────────────────────────────────────────────

    def _apply_style(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #0A1929;
                color: #E2E8F0;
                font-family: "Segoe UI", sans-serif;
            }
            QStackedWidget, QWidget#success-page {
                background-color: #0A1929;
            }
            QLabel {
                color: #E2E8F0;
                background: transparent;
            }
            QLabel#titulo-licenca {
                font-size: 20px;
                font-weight: 800;
                color: #6BD1FF;
            }
            QLabel#msg-licenca {
                font-size: 12px;
                color: #94A3B8;
            }
            QLabel#titulo-sucesso {
                font-size: 24px;
                font-weight: 800;
                color: #4ADE80;
            }
            QLabel#detalhes-sucesso {
                font-size: 14px;
                color: #CBD5E1;
                line-height: 1.5;
            }
            QLabel#entrando {
                font-size: 12px;
                color: #6BD1FF;
                font-weight: 600;
            }
            QLineEdit {
                background-color: #0D2847;
                color: #E2E8F0;
                border: 1px solid #1A3A5C;
                border-radius: 8px;
                padding: 10px 14px;
                font-size: 13px;
                selection-background-color: #6BD1FF;
                selection-color: #031626;
            }
            QLineEdit:focus {
                border-color: #6BD1FF;
            }
            QPushButton#btn-ativar {
                background-color: #0369A1;
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 700;
                padding: 10px 28px;
            }
            QPushButton#btn-ativar:hover {
                background-color: #0284C7;
            }
            QPushButton#btn-ativar:pressed {
                background-color: #075985;
            }
            QPushButton#btn-sair {
                background-color: #1E293B;
                color: #94A3B8;
                border: 1px solid #334155;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
                padding: 10px 28px;
            }
            QPushButton#btn-sair:hover {
                background-color: #334155;
                color: #E2E8F0;
            }
        """)

    # ── Lógica ────────────────────────────────────────────────────────────

    def _on_ativar(self):
        chave = self.entry_chave.text().strip()
        if not chave:
            QMessageBox.warning(self, "Aviso", "Cole o código de ativação!")
            return

        payload = validar_chave(chave)
        if payload is None:
            QMessageBox.critical(self, "Erro", "Código de ativação inválido!")
            return

        if chave_expirada(payload):
            QMessageBox.critical(self, "Erro", "Este código já expirou!")
            return

        if not salvar_licenca(chave):
            QMessageBox.critical(self, "Erro", "Falha ao salvar a licença no disco.")
            return

        # Usar timeout realista para operações de rede durante ativação
        import socket
        import logging
        logger = logging.getLogger(__name__)
        timeout_original = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(10)  # Timeout de 10 segundos (aumentado de 2 para redes lentas)
            dias = dias_restantes(payload)
        except socket.timeout:
            # Timeout de rede — assumir vencimento próximo como precaução
            logger.warning("Timeout ao verificar dias restantes - assumindo vencimento próximo")
            dias = 0
        except Exception as e:
            # Outro erro — logar e assumir segurança
            logger.error(f"Erro ao obter dias restantes: {e}")
            dias = 0
        finally:
            socket.setdefaulttimeout(timeout_original)

        # ── Transição para tela de sucesso ────────────────────────
        plano = payload["p"].capitalize()
        cliente = payload["c"]

        if dias == -1:
            validade = "Acesso permanente"
        else:
            validade = f"Válida por {dias} dias"

        self.lbl_detalhes.setText(
            f"{cliente}\n{plano} — {validade}"
        )

        self.stack.setCurrentIndex(1)
        self._ativado = True

        # Fecha automaticamente após 2 segundos
        QTimer.singleShot(2000, self.accept)

    # ── API ────────────────────────────────────────────────────────────────

    @property
    def ativado(self) -> bool:
        return self._ativado
