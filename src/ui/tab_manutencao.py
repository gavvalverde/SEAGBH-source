"""
Aba de Manutenção — PyQt6.
Cadastrar saída, registrar retorno e listar status.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QBrush

from core.database import Database


class TabManutencao(QWidget):
    """Widget completo da aba Manutenção."""

    COLUNAS = ("ID", "Código", "Nome", "Data Saída", "Local Manutenção", "Status")

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self._build_ui()
        self.atualizar_lista()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(16)

        # ── Título da página ──────────────────────────────────────────────
        titulo = QLabel("Manutenção de Equipamentos")
        titulo.setProperty("role", "title")
        root.addWidget(titulo)

        # ── Formulário ────────────────────────────────────────────────────
        form = QFormLayout()
        form.setHorizontalSpacing(15)
        form.setVerticalSpacing(10)

        self.entry_codigo = QLineEdit()
        self.entry_local = QLineEdit()
        form.addRow("Código de Barras:", self.entry_codigo)
        form.addRow("Local de Manutenção:", self.entry_local)
        root.addLayout(form)

        # ── Botões ────────────────────────────────────────────────────────
        bar = QHBoxLayout()
        bar.setSpacing(8)

        botoes = [
            ("🔧 Cadastrar Saída",   "primary", self._on_cadastrar_saida),
            ("📥 Registrar Retorno", "success", self._on_registrar_retorno),
            ("🔄 Atualizar Lista",   None,      self.atualizar_lista),
        ]
        for texto, cls, slot in botoes:
            btn = QPushButton(texto)
            if cls:
                btn.setProperty("class", cls)
            btn.clicked.connect(slot)
            bar.addWidget(btn)

        root.addLayout(bar)

        # ── Tabela ────────────────────────────────────────────────────────
        self.tabela = QTableWidget()
        self.tabela.setColumnCount(len(self.COLUNAS))
        self.tabela.setHorizontalHeaderLabels(self.COLUNAS)
        self.tabela.setAlternatingRowColors(True)
        self.tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabela.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.verticalHeader().setVisible(False)

        hdr = self.tabela.horizontalHeader()
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        widths = [50, 150, 250, 150, 200, 120]
        for i, w in enumerate(widths):
            self.tabela.setColumnWidth(i, w)

        root.addWidget(self.tabela, stretch=1)

    # ── Dados ─────────────────────────────────────────────────────────────

    def atualizar_lista(self):
        dados = self.db.listar_manutencao()
        self.tabela.setRowCount(0)
        for row_data in dados:
            row = self.tabela.rowCount()
            self.tabela.insertRow(row)
            for col, val in enumerate(row_data):
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.tabela.setItem(row, col, item)

            status = row_data[5]
            if self._is_tema_escuro():
                if status == "Em manutenção":
                    self._colorir_linha(row, "#3D2900", "#FFBA05")
                elif status == "Concluído":
                    self._colorir_linha(row, "#0D2E1A", "#00C86F")
            else:
                if status == "Em manutenção":
                    self._colorir_linha(row, "#FEF3C7", "#92400E")
                elif status == "Concluído":
                    self._colorir_linha(row, "#D1FAE5", "#065F46")

    def _is_tema_escuro(self) -> bool:
        win = self.window()
        return getattr(win, '_tema_escuro', True)

    def _colorir_linha(self, row, bg, fg):
        bg_b = QBrush(QColor(bg))
        fg_b = QBrush(QColor(fg))
        for col in range(self.tabela.columnCount()):
            item = self.tabela.item(row, col)
            if item:
                item.setBackground(bg_b)
                item.setForeground(fg_b)

    def _linha_selecionada(self) -> int | None:
        rows = self.tabela.selectionModel().selectedRows()
        if not rows:
            QMessageBox.warning(self, "Aviso", "Selecione um equipamento na lista!")
            return None
        return int(self.tabela.item(rows[0].row(), 0).text())

    # ── Cadastrar saída ───────────────────────────────────────────────────

    def _on_cadastrar_saida(self):
        codigo = self.entry_codigo.text().strip()
        local = self.entry_local.text().strip()
        if not codigo:
            return

        equip_id = self.db.obter_id_por_codigo(codigo)
        if equip_id is None:
            QMessageBox.warning(self, "Aviso", f"Código {codigo} não encontrado!")
            return

        if self.db.esta_em_manutencao(equip_id):
            QMessageBox.warning(self, "Aviso", "⚠️ Equipamento já está em manutenção!")
            return

        try:
            self.db.cadastrar_saida_manutencao(equip_id, local)
            self.atualizar_lista()
            self.entry_codigo.clear()
            self.entry_local.clear()
            QMessageBox.information(self, "Sucesso", "Saída para manutenção cadastrada!")
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha: {e}")

    # ── Registrar retorno ─────────────────────────────────────────────────

    def _on_registrar_retorno(self):
        manut_id = self._linha_selecionada()
        if manut_id is None:
            return

        status = self.db.obter_status_manutencao(manut_id)
        if status != "Em manutenção":
            QMessageBox.warning(self, "Aviso", "⚠️ Este equipamento não está em manutenção!")
            return

        try:
            self.db.registrar_retorno_manutencao(manut_id)
            self.atualizar_lista()
            QMessageBox.information(self, "Sucesso", "Retorno registrado com sucesso!")
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha: {e}")
