"""
Aba Dashboard — visão geral do sistema com cards de estatísticas.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QGridLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal

from core.database import Database


class _CarregarDadosThread(QThread):
    """Thread para carregar dados do dashboard em background."""
    dados_prontos = pyqtSignal(dict)  # Emite os dados carregados

    def run(self):
        """Carrega os dados sem bloquear a UI."""
        try:
            # ⚠️ IMPORTANTE: Criar nova instância de Database nesta thread!
            # SQLite não permite usar a mesma conexão em threads diferentes
            db = Database()
            
            dados = {
                'total_equip': len(db.todos_equipamentos_completos()),
                'eventos_ativos': db.eventos_em_andamento(),
                'em_manut': db.equipamentos_em_manutencao(),
                'atrasos': db.eventos_atrasados(),
            }
            self.dados_prontos.emit(dados)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Erro ao carregar dados do dashboard: {e}")
            # Emite dados vazios em caso de erro
            self.dados_prontos.emit({
                'total_equip': 0,
                'eventos_ativos': [],
                'em_manut': [],
                'atrasos': [],
            })


class TabDashboard(QWidget):
    """Painel inicial com estatísticas do sistema."""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self._carregador = None
        self._build_ui()
        # Agenda o carregamento de dados em background thread
        QTimer.singleShot(100, self._carregar_dados_async)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(24)

        # ── Título ────────────────────────────────────────────────────────
        titulo = QLabel("Painel Geral")
        titulo.setProperty("role", "title")
        root.addWidget(titulo)

        # ── Cards de estatísticas ─────────────────────────────────────────
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(16)

        self.card_equip   = self._criar_card("📦", "0", "Total Equipamentos")
        self.card_eventos = self._criar_card("📋", "0", "Eventos Ativos")
        self.card_manut   = self._criar_card("🔧", "0", "Em Manutenção")
        self.card_atrasos = self._criar_card("⚠️", "0", "Atrasos")

        for card in (self.card_equip, self.card_eventos, self.card_manut, self.card_atrasos):
            cards_layout.addWidget(card["frame"])

        root.addLayout(cards_layout)

        # ── Seção: Eventos ativos recentes ────────────────────────────────
        sec_label = QLabel("Eventos Ativos")
        sec_label.setProperty("role", "section")
        root.addWidget(sec_label)

        self.tabela_eventos = QTableWidget()
        self.tabela_eventos.setColumnCount(5)
        self.tabela_eventos.setHorizontalHeaderLabels(
            ["ID", "Evento", "Responsável", "Início", "Fim"]
        )
        self.tabela_eventos.setAlternatingRowColors(True)
        self.tabela_eventos.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabela_eventos.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela_eventos.verticalHeader().setVisible(False)
        hdr = self.tabela_eventos.horizontalHeader()
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tabela_eventos.setMaximumHeight(260)
        root.addWidget(self.tabela_eventos)

        # ── Seção: Equipamentos em manutenção ─────────────────────────────
        sec_manut = QLabel("Equipamentos em Manutenção")
        sec_manut.setProperty("role", "section")
        root.addWidget(sec_manut)

        self.tabela_manut = QTableWidget()
        self.tabela_manut.setColumnCount(4)
        self.tabela_manut.setHorizontalHeaderLabels(
            ["Código", "Nome", "Local", "Data Saída"]
        )
        self.tabela_manut.setAlternatingRowColors(True)
        self.tabela_manut.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabela_manut.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela_manut.verticalHeader().setVisible(False)
        hdr2 = self.tabela_manut.horizontalHeader()
        hdr2.setStretchLastSection(True)
        hdr2.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tabela_manut.setMaximumHeight(200)
        root.addWidget(self.tabela_manut)

        root.addStretch()

    # ── Fábrica de card ───────────────────────────────────────────────────

    def _criar_card(self, icone: str, valor: str, rotulo: str) -> dict:
        frame = QFrame()
        frame.setProperty("class", "card-stat")
        frame.setFixedHeight(120)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(4)

        lbl_icon = QLabel(icone)
        lbl_icon.setProperty("role", "stat-icon")

        lbl_valor = QLabel(valor)
        lbl_valor.setProperty("role", "stat-value")

        lbl_rotulo = QLabel(rotulo)
        lbl_rotulo.setProperty("role", "stat-label")

        layout.addWidget(lbl_icon)
        layout.addWidget(lbl_valor)
        layout.addWidget(lbl_rotulo)

        return {"frame": frame, "valor": lbl_valor, "icone": lbl_icon}

    # ── Atualização de dados ──────────────────────────────────────────────

    def _carregar_dados_async(self):
        """Inicia o carregamento de dados em background thread."""
        if self._carregador is not None and self._carregador.isRunning():
            return  # Já está carregando
        
        self._carregador = _CarregarDadosThread()
        self._carregador.dados_prontos.connect(self._atualizar_com_dados)
        self._carregador.start()

    def _atualizar_com_dados(self, dados: dict):
        """Atualiza a UI com os dados carregados."""
        self.card_equip["valor"].setText(str(dados['total_equip']))
        self.card_eventos["valor"].setText(str(len(dados['eventos_ativos'])))
        self.card_manut["valor"].setText(str(len(dados['em_manut'])))
        self.card_atrasos["valor"].setText(str(len(dados['atrasos'])))

        # Tabela eventos ativos
        self.tabela_eventos.setRowCount(0)
        for row_data in dados['eventos_ativos'][:15]:
            row = self.tabela_eventos.rowCount()
            self.tabela_eventos.insertRow(row)
            for col, val in enumerate(row_data):
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.tabela_eventos.setItem(row, col, item)

        # Tabela manutenção
        self.tabela_manut.setRowCount(0)
        for row_data in dados['em_manut'][:10]:
            row = self.tabela_manut.rowCount()
            self.tabela_manut.insertRow(row)
            for col, val in enumerate(row_data):
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.tabela_manut.setItem(row, col, item)

    def atualizar(self):
        """Recarrega todas as estatísticas do banco (em background)."""
        self._carregar_dados_async()
