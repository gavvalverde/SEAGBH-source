"""
Aba de Equipamentos — PyQt6.
CRUD completo: cadastrar, consultar, listar, editar, remover.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox,
    QAbstractItemView, QDialog, QInputDialog,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QBrush

from core.database import Database
from datetime import datetime


class TabEquipamentos(QWidget):
    """Widget completo da aba Equipamentos."""

    COLUNAS = ("ID", "Código", "Nome", "Localização", "Data")

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self._build_ui()
        self.listar_todos()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(16)

        # ── Título da página ──────────────────────────────────────────────
        titulo = QLabel("Gestão de Equipamentos")
        titulo.setProperty("role", "title")
        root.addWidget(titulo)

        # ── Formulário ────────────────────────────────────────────────────
        form = QFormLayout()
        form.setHorizontalSpacing(15)
        form.setVerticalSpacing(10)

        self.entry_codigo = QLineEdit()
        self.entry_nome = QLineEdit()
        self.entry_descricao = QLineEdit()
        self.entry_localizacao = QLineEdit()
        form.addRow("Código de Barras:", self.entry_codigo)
        form.addRow("Nome do Equipamento:", self.entry_nome)
        form.addRow("Descrição:", self.entry_descricao)
        form.addRow("Localização:", self.entry_localizacao)
        root.addLayout(form)

        # ── Barra de botões ───────────────────────────────────────────────
        bar = QHBoxLayout()
        bar.setSpacing(8)

        botoes = [
            ("Cadastrar",    "primary", self._on_cadastrar),
            ("Consultar",    None,      self._on_consultar),
            ("Listar Todos", None,      self.listar_todos),
            ("Editar",       None,      self._on_editar),
            ("Remover",      "danger",  self._on_remover),
        ]
        for texto, cls, slot in botoes:
            btn = QPushButton(texto)
            if cls:
                btn.setProperty("class", cls)
            btn.clicked.connect(slot)
            bar.addWidget(btn)

        root.addLayout(bar)

        # ── Busca ─────────────────────────────────────────────────────────
        search_bar = QHBoxLayout()
        search_bar.addWidget(QLabel("Buscar:"))
        self.entry_search = QLineEdit()
        self.entry_search.setPlaceholderText("Código, nome ou localização…")
        self.entry_search.returnPressed.connect(self._on_filtrar)
        search_bar.addWidget(self.entry_search, stretch=1)
        btn_search = QPushButton("🔍")
        btn_search.setFixedSize(48, 42)
        btn_search.setStyleSheet("padding: 4px 8px; font-size: 16px;")
        btn_search.clicked.connect(self._on_filtrar)
        search_bar.addWidget(btn_search)
        root.addLayout(search_bar)

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

        widths = [60, 180, 300, 250, 160]
        for i, w in enumerate(widths):
            self.tabela.setColumnWidth(i, w)

        root.addWidget(self.tabela, stretch=1)

    # ── Dados ─────────────────────────────────────────────────────────────

    def listar_todos(self):
        """Carrega equipamentos disponíveis (não em manutenção)."""
        dados = self.db.listar_equipamentos_disponiveis()
        self._popular_tabela(dados)

    def _on_filtrar(self):
        filtro = self.entry_search.text().strip()
        dados = self.db.listar_equipamentos_disponiveis(filtro)
        self._popular_tabela(dados)

    def _popular_tabela(self, dados):
        self.tabela.setRowCount(0)
        agora = datetime.now()
        for row_data in dados:
            row = self.tabela.rowCount()
            self.tabela.insertRow(row)
            for col, val in enumerate(row_data):
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.tabela.setItem(row, col, item)

            # Destaque para recém-cadastrados (< 2 min)
            try:
                dt = datetime.strptime(str(row_data[4]), "%d/%m/%Y %H:%M")
                if (agora - dt).total_seconds() < 120:
                    if self._is_tema_escuro():
                        self._colorir_linha(row, "#0D2E1A", "#00C86F")
                    else:
                        self._colorir_linha(row, "#D1FAE5", "#065F46")
            except (ValueError, IndexError):
                pass

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

    def _limpar_campos(self):
        self.entry_codigo.clear()
        self.entry_nome.clear()
        self.entry_descricao.clear()
        self.entry_localizacao.clear()

    def _linha_selecionada(self) -> int | None:
        rows = self.tabela.selectionModel().selectedRows()
        if not rows:
            QMessageBox.warning(self, "Aviso", "Selecione um equipamento!")
            return None
        return int(self.tabela.item(rows[0].row(), 0).text())

    # ── Cadastrar ─────────────────────────────────────────────────────────

    def _on_cadastrar(self):
        codigo = self.entry_codigo.text().strip()
        nome = self.entry_nome.text().strip()
        desc = self.entry_descricao.text().strip()
        local = self.entry_localizacao.text().strip()

        if not codigo or not nome:
            QMessageBox.critical(self, "Erro", "Código e nome são obrigatórios!")
            return

        try:
            self.db.cadastrar_equipamento(codigo, nome, desc, local)
            self.db.registrar_auditoria("equipamentos", 0, "CADASTRO",
                                        f"{codigo} | {nome}")
            QMessageBox.information(self, "Sucesso", "Equipamento cadastrado com sucesso!")
            self.listar_todos()
            self._limpar_campos()
        except Exception as e:
            if "UNIQUE" in str(e).upper():
                QMessageBox.critical(self, "Erro", "Código já cadastrado!")
            else:
                QMessageBox.critical(self, "Erro", f"Falha: {e}")

    # ── Consultar ─────────────────────────────────────────────────────────

    def _on_consultar(self):
        codigo = self.entry_codigo.text().strip()
        if not codigo:
            QMessageBox.warning(self, "Aviso", "Digite um código!")
            return

        equip_id = self.db.obter_id_por_codigo(codigo)
        if equip_id is None:
            QMessageBox.information(self, "Info", "Item não encontrado!")
            return

        equip = self.db.obter_equipamento(equip_id)
        if not equip:
            QMessageBox.information(self, "Info", "Item não encontrado!")
            return

        labels = ["ID", "Código", "Nome", "Descrição", "Localização", "Data"]
        texto = "\n".join(f"{l}: {v}" for l, v in zip(labels, equip))
        QMessageBox.information(self, f"Equipamento #{equip[0]}", texto)

    # ── Editar ────────────────────────────────────────────────────────────

    def _on_editar(self):
        equip_id = self._linha_selecionada()
        if equip_id is None:
            return

        equip = self.db.obter_equipamento(equip_id)
        if not equip:
            return

        dlg = _EditarEquipDialog(equip, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self.db.editar_equipamento(equip_id, dlg.nome, dlg.descricao, dlg.localizacao)
                self.db.registrar_auditoria(
                    "equipamentos", equip_id, "EDIÇÃO",
                    f"Nome: {dlg.nome} | Local: {dlg.localizacao}")
                QMessageBox.information(self, "Sucesso", "Alterações salvas!")
                self.listar_todos()
            except Exception as e:
                QMessageBox.critical(self, "Erro", f"Falha: {e}")

    # ── Remover ───────────────────────────────────────────────────────────

    def _on_remover(self):
        equip_id = self._linha_selecionada()
        if equip_id is None:
            return

        resp = QMessageBox.question(
            self, "Confirmar Exclusão",
            "Tem certeza que deseja excluir este equipamento e todos os registros relacionados?",
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        pin, ok = QInputDialog.getText(
            self, "Verificação Final",
            "Digite o código de segurança de 4 dígitos:",
        )
        if not ok or pin != "2025":
            QMessageBox.critical(self, "Acesso Negado", "Código de segurança incorreto!")
            return

        try:
            self.db.remover_equipamento(equip_id)
            self.db.registrar_auditoria("equipamentos", equip_id, "EXCLUSÃO", "")
            QMessageBox.information(self, "Sucesso", "Equipamento removido com sucesso!")
            self.listar_todos()
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha: {e}")


# ═══════════════════════════════════════════════════════════════════════════
#  Dialog interno — Editar Equipamento
# ═══════════════════════════════════════════════════════════════════════════

class _EditarEquipDialog(QDialog):
    def __init__(self, equip: tuple, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Editar Equipamento")
        self.resize(600, 300)
        self.setModal(True)

        self.nome = equip[2]
        self.descricao = equip[3] or ""
        self.localizacao = equip[4] or ""

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)

        form = QFormLayout()
        form.setHorizontalSpacing(15)
        form.setVerticalSpacing(10)

        self.e_nome = QLineEdit(self.nome)
        self.e_desc = QLineEdit(self.descricao)
        self.e_local = QLineEdit(self.localizacao)
        form.addRow("Nome:", self.e_nome)
        form.addRow("Descrição:", self.e_desc)
        form.addRow("Localização:", self.e_local)
        root.addLayout(form)

        bar = QHBoxLayout()
        bar.addStretch()
        btn = QPushButton("SALVAR ALTERAÇÕES")
        btn.setProperty("class", "success")
        btn.clicked.connect(self._salvar)
        bar.addWidget(btn)
        root.addLayout(bar)

    def _salvar(self):
        nome = self.e_nome.text().strip()
        if not nome:
            QMessageBox.critical(self, "Erro", "Nome é obrigatório!")
            return
        self.nome = nome
        self.descricao = self.e_desc.text().strip()
        self.localizacao = self.e_local.text().strip()
        self.accept()
