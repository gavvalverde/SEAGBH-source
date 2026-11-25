"""
Aba de Eventos — PyQt6.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QAbstractItemView,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QBrush

from core.database import Database, ErroMovimentacao
from core.datas_eventos import (
    ErroPeriodoEvento, ORIENTACAO_DATA_EVENTO, problema_periodo_evento, validar_periodo_evento,
)
from ui.dialogs import (
    CriarEventoDialog, RetornoDialog, EditarEventoDialog,
    HistoricoDialog, LocalizacaoDialog,
)


class TabEventos(QWidget):
    """Widget completo da aba Eventos."""

    COLUNAS = ("ID", "Evento", "Local", "Responsável", "Início", "Fim", "Status")

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self._build_ui()
        self.atualizar_lista()

    # ── Construção da interface ───────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(16)

        # ── Título da página ──────────────────────────────────────────────
        titulo = QLabel("Gestão de Eventos")
        titulo.setProperty("role", "title")
        root.addWidget(titulo)

        # ── Formulário ────────────────────────────────────────────────────
        form = QFormLayout()
        form.setHorizontalSpacing(15)
        form.setVerticalSpacing(10)

        self.entry_nome   = QLineEdit();  form.addRow("Nome do Evento:", self.entry_nome)
        self.entry_local  = QLineEdit();  form.addRow("Local do Evento:", self.entry_local)
        self.entry_resp   = QLineEdit();  form.addRow("Responsável:", self.entry_resp)
        self.entry_inicio = QLineEdit();  form.addRow("Data Início:", self.entry_inicio)
        self.entry_fim    = QLineEdit();  form.addRow("Data Fim:", self.entry_fim)
        for campo in (self.entry_inicio, self.entry_fim):
            campo.setPlaceholderText("DD/MM/AAAA [HH:MM[:SS]]")
            campo.setToolTip(ORIENTACAO_DATA_EVENTO)

        root.addLayout(form)

        # ── Barra de botões ───────────────────────────────────────────────
        bar = QHBoxLayout()
        bar.setSpacing(8)

        botoes = [
            ("🗸 Criar Evento",        "primary",  self._on_criar),
            ("📤 Registrar Retorno",   "success",  self._on_retorno),
            ("✏️ Editar Evento",       None,       self._on_editar),
            ("🗑️ Remover Evento",     "danger",   self._on_remover),
            ("🔄 Atualizar Lista",     None,       self.atualizar_lista),
            ("📜 Histórico",           "primary",  self._on_historico),
        ]
        for texto, cls, slot in botoes:
            btn = QPushButton(texto)
            if cls:
                btn.setProperty("class", cls)
            btn.clicked.connect(slot)
            bar.addWidget(btn)

        root.addLayout(bar)

        # ── Barra de pesquisa de localização ──────────────────────────────
        search_bar = QHBoxLayout()
        search_bar.addWidget(QLabel("Barra de pesquisa de localização:"))
        self.entry_search = QLineEdit()
        self.entry_search.setPlaceholderText("Código de barras…")
        self.entry_search.returnPressed.connect(self._on_localizar)
        search_bar.addWidget(self.entry_search)
        btn_search = QPushButton("🔍")
        btn_search.setFixedSize(48, 42)
        btn_search.setStyleSheet("padding: 4px 8px; font-size: 16px;")
        btn_search.clicked.connect(self._on_localizar)
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

        header = self.tabela.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        widths = [50, 250, 150, 120, 100, 100, 100]
        for i, w in enumerate(widths):
            self.tabela.setColumnWidth(i, w)

        root.addWidget(self.tabela, stretch=1)

    # ── Dados ─────────────────────────────────────────────────────────────

    def atualizar_lista(self):
        """Recarrega a tabela com dados do banco."""
        dados = self.db.listar_eventos()
        self.tabela.setRowCount(0)

        agendados  = [r for r in dados if r[6] != "Concluído"]
        concluidos = [r for r in dados if r[6] == "Concluído"]

        for row_data in agendados + concluidos:
            row = self.tabela.rowCount()
            self.tabela.insertRow(row)
            for col, val in enumerate(row_data):
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.tabela.setItem(row, col, item)

            status = row_data[6]
            problema = problema_periodo_evento(row_data[4], row_data[5])
            if problema:
                item_status = self.tabela.item(row, 6)
                item_status.setText("Datas a revisar")
                item_status.setToolTip(f"{problema} Status registrado: {status}.")
                bg, fg = (("#39260E", "#FFCD7A") if self._is_tema_escuro()
                          else ("#FFF3D6", "#77420C"))
                self._colorir_linha(row, bg, fg)
            elif self._is_tema_escuro():
                if status == "Concluído":
                    self._colorir_linha(row, "#0D2E1A", "#00C86F")
                elif status == "Agendado":
                    self._colorir_linha(row, "#073343", "#6BD1FF")
            else:
                if status == "Concluído":
                    self._colorir_linha(row, "#D1FAE5", "#065F46")
                elif status == "Agendado":
                    self._colorir_linha(row, "#DBEAFE", "#1E40AF")

    def _is_tema_escuro(self) -> bool:
        win = self.window()
        return getattr(win, '_tema_escuro', True)

    def _colorir_linha(self, row: int, bg: str, fg: str):
        bg_brush = QBrush(QColor(bg))
        fg_brush = QBrush(QColor(fg))
        for col in range(self.tabela.columnCount()):
            item = self.tabela.item(row, col)
            if item:
                item.setBackground(bg_brush)
                item.setForeground(fg_brush)

    def _linha_selecionada(self) -> int | None:
        rows = self.tabela.selectionModel().selectedRows()
        if not rows:
            QMessageBox.warning(self, "Aviso", "Selecione um evento!")
            return None
        return int(self.tabela.item(rows[0].row(), 0).text())

    def _limpar_campos(self):
        self.entry_nome.clear()
        self.entry_local.clear()
        self.entry_resp.clear()
        self.entry_inicio.clear()
        self.entry_fim.clear()

    # ── Slots ─────────────────────────────────────────────────────────────

    def _on_criar(self):
        nome = self.entry_nome.text().strip()
        local = self.entry_local.text().strip()
        resp = self.entry_resp.text().strip()
        inicio = self.entry_inicio.text().strip()
        fim = self.entry_fim.text().strip()

        if not all([nome, local, resp, inicio, fim]):
            QMessageBox.critical(self, "Erro", "Preencha todos os campos do evento!")
            return

        try:
            validar_periodo_evento(inicio, fim)
        except ErroPeriodoEvento as erro:
            QMessageBox.warning(self, "Datas do evento", str(erro))
            return

        dlg = CriarEventoDialog(self.db, nome, local, resp, inicio, fim, parent=self)
        if dlg.exec():
            self.atualizar_lista()
            self._limpar_campos()

    def _on_retorno(self):
        evento_id = self._linha_selecionada()
        if evento_id is None:
            return
        dlg = RetornoDialog(self.db, evento_id, parent=self)
        dlg.exec()
        self.atualizar_lista()

    def _on_editar(self):
        evento_id = self._linha_selecionada()
        if evento_id is None:
            return

        evento = self.db.obter_evento(evento_id)
        if evento and evento["status"] == "Concluído":
            QMessageBox.information(self, "Info", "Eventos concluídos não podem ser editados!")
            return

        dlg = EditarEventoDialog(self.db, evento_id, parent=self)
        if dlg.exec():
            self.atualizar_lista()

    def _on_remover(self):
        evento_id = self._linha_selecionada()
        if evento_id is None:
            return

        resp = QMessageBox.question(
            self, "Confirmar Exclusão",
            "Tem certeza que deseja excluir este evento e todos os registros relacionados?",
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        try:
            self.db.remover_evento(evento_id)
            self.atualizar_lista()
            QMessageBox.information(self, "Sucesso", "Evento excluído com sucesso!")
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha na exclusão: {e}")

    def _on_historico(self):
        dlg = HistoricoDialog(self.db, parent=self)
        dlg.exec()

    def _on_localizar(self):
        codigo = self.entry_search.text().strip()
        if not codigo:
            return

        try:
            info = self.db.localizar_equipamento(codigo)
        except ErroMovimentacao as erro:
            QMessageBox.warning(self, "Localização não confirmada", str(erro))
            return
        except Exception:
            QMessageBox.critical(self, "Erro", "Não foi possível consultar a localização.")
            return

        if info is None:
            QMessageBox.information(
                self, "Sem movimentação ativa",
                f"Nenhuma saída pendente ou manutenção ativa foi encontrada para o código {codigo}.",
            )
            return

        if info["tipo"] == "manutencao":
            QMessageBox.warning(
                self, "Equipamento em Manutenção",
                f"O equipamento {codigo} está em manutenção no momento.",
            )
            return

        dlg = LocalizacaoDialog(info, parent=self)
        dlg.exec()
