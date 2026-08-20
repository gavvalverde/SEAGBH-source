"""Splash screen estável e premium para inicialização do SEAGBH."""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QProgressBar,
    QFrame,
    QGraphicsDropShadowEffect,
)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont, QColor

from core.cores import Cores
from core.versao import __version__


class SplashScreen(QWidget):
    """Splash com visual premium e animação segura (sem loop de progresso)."""

    def __init__(self, on_finished=None, duration_ms: int = 3400):
        super().__init__()
        self._on_finished = on_finished
        self._duration_ms = duration_ms
        self._dots_count = 0

        self.setWindowTitle("SEAGBH - Carregando")
        self.setFixedSize(1280, 720)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        # Escopo no objeto da splash para nao "vazar" gradiente para filhos.
        self.setObjectName("splash-root")
        self.setStyleSheet(f"""
            QWidget#splash-root {{
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 {Cores.AZUL_ESCURO},
                    stop:0.5 {Cores.AZUL_ALURA},
                    stop:1 {Cores.AZUL_ESCURO}
                );
            }}
        """)

        self._build_ui()
        self._center()
        self._start_fade_in()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(80, 72, 80, 56)
        layout.setSpacing(0)

        layout.addStretch(2)

        card = QFrame()
        card.setObjectName("splash-card")
        card.setStyleSheet(f"""
            QFrame#splash-card {{
                background: #040E1A;
                border: 1px solid rgba(26, 58, 92, 110);
                border-radius: 22px;
            }}
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 150))
        card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(52, 36, 52, 34)
        card_layout.setSpacing(0)

        card_layout.addSpacing(18)

        # Ícone grande
        icone = QLabel("⚙️   📦")
        icone.setFont(QFont("Segoe UI", 66))
        icone.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icone.setStyleSheet(f"""
            color: {Cores.AZUL_MEDIO};
            background: transparent;
            padding-bottom: 2px;
        """)
        card_layout.addWidget(icone)

        # Título principal
        self.lbl_titulo = QLabel("SEAGBH")
        self.lbl_titulo.setFont(QFont("Segoe UI", 56, QFont.Weight.Bold))
        self.lbl_titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_titulo.setStyleSheet(f"""
            color: #F4FAFF;
            background: transparent;
            letter-spacing: 8px;
        """)
        card_layout.addWidget(self.lbl_titulo)

        # Subtítulo
        subtitulo = QLabel("Sistema de Almoxarifado")
        subtitulo.setFont(QFont("Segoe UI", 14))
        subtitulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitulo.setStyleSheet(f"""
            color: {Cores.AZUL_MEDIO};
            background: transparent;
            letter-spacing: 4px;
            margin-top: 2px;
        """)
        card_layout.addWidget(subtitulo)

        versao_chip = QLabel(f"Versao {__version__}  |  Build Estavel")
        versao_chip.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        versao_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        versao_chip.setStyleSheet(f"""
            color: {Cores.AZUL_MEDIO};
            background: rgba(7, 51, 67, 140);
            border: 1px solid rgba(26, 58, 92, 130);
            border-radius: 10px;
            padding: 5px 12px;
            margin-top: 10px;
        """)
        card_layout.addWidget(versao_chip, alignment=Qt.AlignmentFlag.AlignHCenter)

        card_layout.addSpacing(16)

        card_layout.addSpacing(18)

        # Label de fase
        fase = QLabel("Inicializando Ambiente")
        fase.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        fase.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fase.setStyleSheet(f"""
            color: {Cores.CINZA_ESCURO};
            background: transparent;
            letter-spacing: 2px;
            text-transform: uppercase;
            margin-bottom: 8px;
        """)
        card_layout.addWidget(fase)

        # Barra de progresso indeterminada (mais robusta em runtime)
        self.barra = QProgressBar()
        self.barra.setFixedHeight(20)
        self.barra.setRange(0, 0)
        self.barra.setTextVisible(False)
        self.barra.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid {Cores.BORDA_ESCURA};
                border-radius: 10px;
                background-color: {Cores.BG_INPUT};
            }}
            QProgressBar::chunk {{
                border-radius: 9px;
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 {Cores.AZUL_MEDIO},
                    stop:1 {Cores.VERDE_ALURA}
                );
            }}
        """)
        card_layout.addWidget(self.barra)

        # Status com dots animados
        self.lbl_status = QLabel("Carregando módulos principais")
        self.lbl_status.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet(f"""
            color: {Cores.CINZA_MEDIO};
            background: transparent;
            margin-top: 14px;
        """)
        card_layout.addWidget(self.lbl_status)

        dica = QLabel("Sincronizando inventario, eventos e seguranca de licenca")
        dica.setFont(QFont("Segoe UI", 9))
        dica.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dica.setStyleSheet(f"""
            color: {Cores.CINZA_ESCURO};
            background: transparent;
            margin-top: 6px;
            letter-spacing: 1px;
        """)
        card_layout.addWidget(dica)

        card_layout.addSpacing(10)

        layout.addWidget(card)

        layout.addStretch(2)

        # Rodapé
        rodape = QLabel("By Gabriel do Amaral Valverde")
        rodape.setFont(QFont("Segoe UI", 10))
        rodape.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rodape.setStyleSheet(f"""
            color: {Cores.CINZA_ESCURO};
            background: transparent;
        """)
        layout.addWidget(rodape)

        # Mantém layout estável: sem efeitos em QLabel para evitar ghosting.

    # ── Centralizar ───────────────────────────────────────────────────────

    def _center(self):
        screen_obj = self.screen()
        if screen_obj is None:
            from PyQt6.QtWidgets import QApplication
            screen_obj = QApplication.primaryScreen()
        if screen_obj is None:
            return
        screen = screen_obj.geometry()
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2
        self.move(x, y)

    # ── Fade-in ───────────────────────────────────────────────────────────

    def _start_fade_in(self):
        self.setWindowOpacity(0.0)
        self.show()

        self._fade = QPropertyAnimation(self, b"windowOpacity")
        self._fade.setDuration(600)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fade.finished.connect(self._iniciar_animacao)
        self._fade.start()

    def _iniciar_animacao(self):
        # Timer para animar os "..." do status
        self._dots_timer = QTimer(self)
        self._dots_timer.setInterval(350)
        self._dots_timer.timeout.connect(self._animate_dots)
        self._dots_timer.start()

        # Inicia fade-out após o período de exibição
        QTimer.singleShot(self._duration_ms, self._start_fade_out)

    def _animate_dots(self):
        self._dots_count = (self._dots_count + 1) % 4
        dots = "." * self._dots_count
        self.lbl_status.setText(f"Carregando módulos principais{dots}")

    def _start_fade_out(self):
        self._fade_out = QPropertyAnimation(self, b"windowOpacity")
        self._fade_out.setDuration(250)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fade_out.finished.connect(self._finalizar)
        self._fade_out.start()

    def _finalizar(self):
        if hasattr(self, "_dots_timer") and self._dots_timer.isActive():
            self._dots_timer.stop()
        self.close()
        if self._on_finished:
            self._on_finished()
