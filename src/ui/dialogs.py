"""
Diálogos reutilizáveis — PyQt6.
Contém popups de criação de evento, retorno, edição,
histórico, detalhes, localização e bloco de notas.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox,
    QAbstractItemView, QTextEdit, QListWidget, QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QBrush

from core.database import Database


# ═══════════════════════════════════════════════════════════════════════════
#  Criação de Evento (popup com scan de código de barras)
# ═══════════════════════════════════════════════════════════════════════════

class CriarEventoDialog(QDialog):
    """Popup para adicionar equipamentos ao novo evento."""

    def __init__(self, db: Database, nome, local, resp, inicio, fim, parent=None):
        super().__init__(parent)
        self.db = db
        self._nome = nome
        self._local = local
        self._resp = resp
        self._inicio = inicio
        self._fim = fim
        self._equipamentos: list[tuple[int, str, str]] = []  # (id, codigo, nome)
        self._conteudo_notas = ""
        self.setWindowTitle("Adicionar Equipamentos ao Evento")
        self.resize(800, 550)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)

        lbl_title = QLabel("Seleção de Equipamentos")
        lbl_title.setProperty("role", "title")
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(lbl_title)

        # Entry código
        row = QHBoxLayout()
        row.addWidget(QLabel("Código de Barras:"))
        self.entry_codigo = QLineEdit()
        self.entry_codigo.returnPressed.connect(self._adicionar)
        row.addWidget(self.entry_codigo, stretch=1)
        root.addLayout(row)

        # Tabela de selecionados
        lbl_sec = QLabel("Equipamentos Selecionados")
        lbl_sec.setProperty("role", "section")
        root.addWidget(lbl_sec)

        self.tabela = QTableWidget()
        self.tabela.setColumnCount(2)
        self.tabela.setHorizontalHeaderLabels(["Código", "Nome do Equipamento"])
        self.tabela.horizontalHeader().setStretchLastSection(True)
        self.tabela.setColumnWidth(0, 250)
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.verticalHeader().setVisible(False)
        root.addWidget(self.tabela, stretch=1)

        # Botões
        bar = QHBoxLayout()
        bar.addStretch()
        btn_notas = QPushButton("📝 Bloco de Anotações")
        btn_notas.clicked.connect(self._abrir_notas)
        bar.addWidget(btn_notas)
        btn_finish = QPushButton("✔️ Finalizar Cadastro")
        btn_finish.setProperty("class", "success")
        btn_finish.clicked.connect(self._finalizar)
        bar.addWidget(btn_finish)
        root.addLayout(bar)

    # ── Scan / adicionar ──────────────────────────────────────────────────

    def _adicionar(self):
        """Adiciona novo equipamento/evento com validação de encoding."""
        import logging
        logger = logging.getLogger(__name__)
        
        codigo = self.entry_codigo.text().strip()
        if not codigo:
            return
        
        # Validar encoding
        try:
            codigo.encode('utf-8')
        except UnicodeEncodeError:
            logger.warning(f"Código com caracteres inválidos: {codigo}")
            QMessageBox.warning(self, "Aviso", "Código contém caracteres inválidos!")
            return
        
        # ... resto do código original ...

        equip_id = self.db.obter_id_por_codigo(codigo)
        if equip_id is None:
            QMessageBox.warning(
                self,
                "Aviso",
                (
                    f"Código {codigo} não encontrado!\n\n"
                    "Orientação: use exatamente o código cadastrado, "
                    "incluindo zeros à esquerda e sem espaços."
                ),
            )
            self.entry_codigo.clear()
            self.entry_codigo.setFocus()
            return

        # Duplicata na lista
        if any(e[0] == equip_id for e in self._equipamentos):
            QMessageBox.warning(self, "Aviso", f"Equipamento {codigo} já adicionado!")
            self.entry_codigo.clear()
            self.entry_codigo.setFocus()
            return

        # Pendência de retorno
        mov = self.db.verificar_pendencia_retorno(equip_id)
        if mov and mov[0] == "saida":
            nome_evt = self.db.obter_nome_evento(mov[1])
            QMessageBox.warning(
                self, "Equipamento Pendente",
                f"Equipamento {codigo} está pendente de retorno no evento \"{nome_evt}\".",
            )
            self.entry_codigo.clear()
            self.entry_codigo.setFocus()
            return

        # Manutenção
        if self.db.esta_em_manutencao(equip_id):
            QMessageBox.warning(self, "Aviso", "⚠️ Equipamento em manutenção!")
            self.entry_codigo.clear()
            self.entry_codigo.setFocus()
            return

        nome_equip = self.db.obter_nome_equipamento(equip_id)
        self._equipamentos.append((equip_id, codigo, nome_equip))

        row = self.tabela.rowCount()
        self.tabela.insertRow(row)
        self.tabela.setItem(row, 0, QTableWidgetItem(codigo))
        self.tabela.setItem(row, 1, QTableWidgetItem(nome_equip))

        self.entry_codigo.clear()
        self.entry_codigo.setFocus()

    # ── Finalizar ─────────────────────────────────────────────────────────

    def _finalizar(self):
        if not self._equipamentos:
            QMessageBox.warning(self, "Aviso", "Adicione pelo menos um equipamento!")
            return
        try:
            ids = [e[0] for e in self._equipamentos]
            evento_id = self.db.criar_evento(
                self._nome, self._local, self._resp,
                self._inicio, self._fim, ids, self._conteudo_notas,
            )
            self.db.registrar_auditoria("eventos", evento_id, "CRIAÇÃO",
                                        f"{self._nome} | {len(ids)} equips")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha ao criar evento: {e}")

    # ── Bloco de notas ────────────────────────────────────────────────────

    def _abrir_notas(self):
        dlg = BlocoNotasDialog(self._conteudo_notas, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._conteudo_notas = dlg.conteudo


# ═══════════════════════════════════════════════════════════════════════════
#  Registrar Retorno de Equipamentos
# ═══════════════════════════════════════════════════════════════════════════

class RetornoDialog(QDialog):
    """Popup para registrar retorno de equipamentos por scan."""

    def __init__(self, db: Database, evento_id: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.evento_id = evento_id
        self._evento_concluido = False
        self.setWindowTitle("Registrar Retorno de Equipamentos")
        self.resize(800, 600)
        self.setModal(True)
        self._build_ui()
        self._carregar_listas()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)

        cols = QHBoxLayout()

        # Pendentes
        left = QVBoxLayout()
        lbl_pend = QLabel("Pendentes de Retorno:")
        lbl_pend.setProperty("role", "section")
        left.addWidget(lbl_pend)
        self.list_pend = QListWidget()
        left.addWidget(self.list_pend)
        cols.addLayout(left)

        # Devolvidos
        right = QVBoxLayout()
        lbl_dev = QLabel("Já Devolvidos:")
        lbl_dev.setProperty("role", "section")
        right.addWidget(lbl_dev)
        self.list_dev = QListWidget()
        right.addWidget(self.list_dev)
        cols.addLayout(right)

        root.addLayout(cols, stretch=1)

        # Entry
        root.addWidget(QLabel("Código de Barras:"))
        self.entry_codigo = QLineEdit()
        self.entry_codigo.returnPressed.connect(self._on_scan)
        root.addWidget(self.entry_codigo)
        self.entry_codigo.setFocus()

    def _carregar_listas(self):
        self.list_pend.clear()
        self.list_dev.clear()

        for codigo, nome in self.db.pendentes_retorno(self.evento_id):
            self.list_pend.addItem(f"{nome} ({codigo})")

        for codigo, nome, dt in self.db.devolvidos_retorno(self.evento_id):
            self.list_dev.addItem(f"{nome} ({codigo}) – {dt}")

    def _on_scan(self):
        codigo = self.entry_codigo.text().strip()
        if not codigo:
            return

        # Verifica se está pendente
        pendentes_textos = [self.list_pend.item(i).text()
                            for i in range(self.list_pend.count())]
        if not any(f"({codigo})" in t for t in pendentes_textos):
            QMessageBox.warning(
                self,
                "Aviso",
                (
                    f"Código {codigo} não está pendente!\n\n"
                    "Orientação: confirme se o código foi cadastrado no evento "
                    "com o mesmo formato (inclusive zeros à esquerda)."
                ),
            )
            self.entry_codigo.clear()
            return

        self.db.registrar_retorno(self.evento_id, codigo)
        self.entry_codigo.clear()
        self._carregar_listas()

        # Auto-conclusão
        if self.list_pend.count() == 0:
            self.db.concluir_evento(self.evento_id)
            self._evento_concluido = True
            QMessageBox.information(
                self, "Concluído",
                "Todos os equipamentos foram devolvidos.\nEvento marcado como Concluído.",
            )
            self.accept()


# ═══════════════════════════════════════════════════════════════════════════
#  Edição de Evento
# ═══════════════════════════════════════════════════════════════════════════

class EditarEventoDialog(QDialog):
    """Popup de edição de evento com gerenciamento de equipamentos."""

    def __init__(self, db: Database, evento_id: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.evento_id = evento_id
        self.setWindowTitle(f"Editar Evento #{evento_id}")
        self.resize(1000, 700)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self):
        dados = self.db.obter_evento(self.evento_id)
        if not dados:
            return

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)

        # Campos editáveis
        form = QFormLayout()
        form.setHorizontalSpacing(15)
        form.setVerticalSpacing(10)
        self.entry_nome = QLineEdit(dados["nome_evento"])
        self.entry_local = QLineEdit(dados["local_evento"])
        self.entry_resp = QLineEdit(dados["responsavel"])
        self.entry_inicio = QLineEdit(dados["data_inicio"])
        self.entry_fim = QLineEdit(dados["data_fim"])
        form.addRow("Nome do Evento:", self.entry_nome)
        form.addRow("Local do Evento:", self.entry_local)
        form.addRow("Responsável:", self.entry_resp)
        form.addRow("Data Início (DD/MM/AAAA):", self.entry_inicio)
        form.addRow("Data Fim (DD/MM/AAAA):", self.entry_fim)
        root.addLayout(form)

        # Tabela de equipamentos
        lbl = QLabel("Equipamentos do Evento:")
        lbl.setProperty("role", "section")
        root.addWidget(lbl)

        self.tabela = QTableWidget()
        self.tabela.setColumnCount(2)
        self.tabela.setHorizontalHeaderLabels(["Código", "Equipamento"])
        self.tabela.horizontalHeader().setStretchLastSection(True)
        self.tabela.setColumnWidth(0, 200)
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabela.verticalHeader().setVisible(False)
        root.addWidget(self.tabela, stretch=1)
        self._recarregar_equips()

        # Botões
        bar = QHBoxLayout()
        btn_add = QPushButton("➕ Adicionar Equipamento")
        btn_add.setProperty("class", "success")
        btn_add.clicked.connect(self._adicionar_equip)
        bar.addWidget(btn_add)

        btn_rem = QPushButton("➖ Remover Selecionado")
        btn_rem.setProperty("class", "danger")
        btn_rem.clicked.connect(self._remover_equip)
        bar.addWidget(btn_rem)

        btn_notas = QPushButton("📝 Bloco de Anotações")
        btn_notas.clicked.connect(self._abrir_notas)
        bar.addWidget(btn_notas)

        bar.addStretch()

        btn_salvar = QPushButton("💾 Salvar Alterações")
        btn_salvar.setProperty("class", "primary")
        btn_salvar.clicked.connect(self._salvar)
        bar.addWidget(btn_salvar)

        root.addLayout(bar)

    def _recarregar_equips(self):
        equips = self.db.equipamentos_do_evento(self.evento_id)
        self.tabela.setRowCount(0)
        for codigo, nome, _status in equips:
            row = self.tabela.rowCount()
            self.tabela.insertRow(row)
            self.tabela.setItem(row, 0, QTableWidgetItem(codigo))
            self.tabela.setItem(row, 1, QTableWidgetItem(nome))

    def _adicionar_equip(self):
        dlg = AdicionarEquipEventoDialog(self.db, self.evento_id, parent=self)
        dlg.exec()
        self._recarregar_equips()

    def _remover_equip(self):
        rows = self.tabela.selectionModel().selectedRows()
        if not rows:
            return
        codigo = self.tabela.item(rows[0].row(), 0).text()
        equip_id = self.db.obter_id_por_codigo(codigo)
        if equip_id is None:
            return
        try:
            self.db.remover_equipamento_evento(self.evento_id, equip_id)
            self._recarregar_equips()
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha ao remover: {e}")

    def _abrir_notas(self):
        conteudo = self.db.obter_notas(self.evento_id)
        dlg = BlocoNotasDialog(conteudo, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.salvar_notas(self.evento_id, dlg.conteudo)

    def _salvar(self):
        try:
            self.db.atualizar_evento(
                self.evento_id,
                self.entry_nome.text().strip(),
                self.entry_local.text().strip(),
                self.entry_resp.text().strip(),
                self.entry_inicio.text().strip(),
                self.entry_fim.text().strip(),
            )
            QMessageBox.information(self, "Sucesso", "Alterações salvas com sucesso!")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha ao salvar: {e}")


# ═══════════════════════════════════════════════════════════════════════════
#  Adicionar Equipamento durante edição de evento (sub-popup)
# ═══════════════════════════════════════════════════════════════════════════

class AdicionarEquipEventoDialog(QDialog):
    """Sub-popup: scan contínuo de equipamentos para ADD em evento existente."""

    def __init__(self, db: Database, evento_id: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.evento_id = evento_id
        self.setWindowTitle(f"Adicionar Equipamento – Evento #{evento_id}")
        self.resize(400, 400)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(15, 15, 15, 15)

        root.addWidget(QLabel("Código de Barras:"))
        self.entry = QLineEdit()
        self.entry.returnPressed.connect(self._adicionar)
        root.addWidget(self.entry)
        self.entry.setFocus()

        lbl = QLabel("Adicionados nesta sessão:")
        lbl.setProperty("role", "section")
        root.addWidget(lbl)

        self.tabela = QTableWidget()
        self.tabela.setColumnCount(2)
        self.tabela.setHorizontalHeaderLabels(["Código", "Nome"])
        self.tabela.horizontalHeader().setStretchLastSection(True)
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.verticalHeader().setVisible(False)
        root.addWidget(self.tabela, stretch=1)

        bar = QHBoxLayout()
        bar.addStretch()
        btn_ok = QPushButton("OK")
        btn_ok.setProperty("class", "primary")
        btn_ok.clicked.connect(self.accept)
        bar.addWidget(btn_ok)
        root.addLayout(bar)

    def _adicionar(self):
        codigo = self.entry.text().strip()
        if not codigo:
            return

        equip_id = self.db.obter_id_por_codigo(codigo)
        if equip_id is None:
            QMessageBox.warning(
                self,
                "Aviso",
                (
                    f"Código {codigo} não encontrado!\n\n"
                    "Orientação: use exatamente o código cadastrado, "
                    "incluindo zeros à esquerda e sem espaços."
                ),
            )
            self.entry.clear()
            self.entry.setFocus()
            return

        # Já no evento?
        equips_atuais = self.db.equipamentos_do_evento(self.evento_id)
        if any(e[0] == codigo for e in equips_atuais):
            QMessageBox.warning(self, "Aviso", f"Equipamento {codigo} já adicionado!")
            self.entry.clear()
            self.entry.setFocus()
            return

        if self.db.esta_em_manutencao(equip_id):
            QMessageBox.warning(self, "Aviso", "⚠️ Equipamento em manutenção!")
            self.entry.clear()
            self.entry.setFocus()
            return

        try:
            self.db.adicionar_equipamento_evento(self.evento_id, equip_id)
            nome = self.db.obter_nome_equipamento(equip_id)
            row = self.tabela.rowCount()
            self.tabela.insertRow(row)
            self.tabela.setItem(row, 0, QTableWidgetItem(codigo))
            self.tabela.setItem(row, 1, QTableWidgetItem(nome))
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha: {e}")

        self.entry.clear()
        self.entry.setFocus()


# ═══════════════════════════════════════════════════════════════════════════
#  Histórico Completo
# ═══════════════════════════════════════════════════════════════════════════

class HistoricoDialog(QDialog):
    """Mostra todos os eventos (agendados + concluídos) com opção de ver detalhes."""

    COLUNAS = ("ID", "Evento", "Local", "Responsável", "Início", "Fim", "Status")

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Histórico Completo de Eventos")
        self.resize(1080, 720)
        self.setModal(True)
        self._build_ui()
        self._carregar()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)

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
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        widths = [50, 200, 150, 150, 100, 100, 100]
        for i, w in enumerate(widths):
            self.tabela.setColumnWidth(i, w)
        root.addWidget(self.tabela, stretch=1)

        bar = QHBoxLayout()
        bar.addStretch()
        btn_det = QPushButton("🔎 Ver Detalhes")
        btn_det.setProperty("class", "primary")
        btn_det.clicked.connect(self._ver_detalhes)
        bar.addWidget(btn_det)
        root.addLayout(bar)

    def _carregar(self):
        dados = self.db.listar_eventos()
        self.tabela.setRowCount(0)
        for row_data in dados:
            row = self.tabela.rowCount()
            self.tabela.insertRow(row)
            for col, val in enumerate(row_data):
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.tabela.setItem(row, col, item)
            status = row_data[6]
            if self._is_tema_escuro():
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

    def _colorir_linha(self, row, bg, fg):
        bg_b = QBrush(QColor(bg))
        fg_b = QBrush(QColor(fg))
        for col in range(self.tabela.columnCount()):
            item = self.tabela.item(row, col)
            if item:
                item.setBackground(bg_b)
                item.setForeground(fg_b)

    def _ver_detalhes(self):
        rows = self.tabela.selectionModel().selectedRows()
        if not rows:
            QMessageBox.warning(self, "Aviso", "Selecione um evento!")
            return
        evento_id = int(self.tabela.item(rows[0].row(), 0).text())
        dlg = DetalhesEventoDialog(self.db, evento_id, parent=self)
        dlg.exec()


# ═══════════════════════════════════════════════════════════════════════════
#  Detalhes do Evento
# ═══════════════════════════════════════════════════════════════════════════

class DetalhesEventoDialog(QDialog):
    """Mostra dados do evento + tabela de equipamentos."""

    def __init__(self, db: Database, evento_id: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.evento_id = evento_id
        self.setWindowTitle(f"Detalhes do Evento #{evento_id}")
        self.resize(900, 600)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self):
        dados = self.db.obter_evento(self.evento_id)
        if not dados:
            QMessageBox.critical(self, "Erro", "Evento não encontrado!")
            return

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)

        # Informações do evento
        info = (
            f"Nome: {dados['nome_evento']}\n"
            f"Local: {dados['local_evento']}\n"
            f"Responsável: {dados['responsavel']}\n"
            f"Período: {dados['data_inicio']} a {dados['data_fim']}\n"
            f"Status: {dados['status']}"
        )
        lbl_info = QLabel(info)
        lbl_info.setStyleSheet("font-size: 13px;")
        root.addWidget(lbl_info)

        # Notas
        notas = dados.get("notas", "")
        if notas:
            lbl_notas_title = QLabel("Anotações:")
            lbl_notas_title.setProperty("role", "section")
            root.addWidget(lbl_notas_title)
            lbl_notas = QLabel(notas)
            lbl_notas.setWordWrap(True)
            root.addWidget(lbl_notas)

        # Equipamentos
        lbl_eq = QLabel("Equipamentos Alocados:")
        lbl_eq.setProperty("role", "section")
        root.addWidget(lbl_eq)

        tabela = QTableWidget()
        tabela.setColumnCount(3)
        tabela.setHorizontalHeaderLabels(["Código", "Equipamento", "Status"])
        tabela.horizontalHeader().setStretchLastSection(True)
        tabela.setColumnWidth(0, 200)
        tabela.setColumnWidth(1, 350)
        tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tabela.verticalHeader().setVisible(False)

        equips = self.db.equipamentos_do_evento(self.evento_id)
        tabela.setRowCount(len(equips))
        for i, (codigo, nome, status) in enumerate(equips):
            tabela.setItem(i, 0, QTableWidgetItem(codigo or "N/A"))
            tabela.setItem(i, 1, QTableWidgetItem(nome or "—"))
            tabela.setItem(i, 2, QTableWidgetItem(status or "Pendente"))

        if not equips:
            tabela.setRowCount(1)
            tabela.setItem(0, 0, QTableWidgetItem("Nenhum equipamento registrado"))

        root.addWidget(tabela, stretch=1)

        btn_fechar = QPushButton("Fechar")
        btn_fechar.clicked.connect(self.accept)
        root.addWidget(btn_fechar, alignment=Qt.AlignmentFlag.AlignRight)


# ═══════════════════════════════════════════════════════════════════════════
#  Localização de Equipamento
# ═══════════════════════════════════════════════════════════════════════════

class LocalizacaoDialog(QDialog):
    """Popup com dados de onde o equipamento está."""

    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Localização do Equipamento {info['codigo']}")
        self.resize(450, 400)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)

        campos = [
            ("Evento", info.get("nome_evento", "")),
            ("Data Início", info.get("data_inicio", "")),
            ("Local Evento", info.get("local_evento", "")),
            ("Nome Equip.", info.get("nome_equip", "")),
            ("Código Barras", info.get("codigo", "")),
            ("Descrição", info.get("descricao", "")),
        ]
        for label, valor in campos:
            lbl = QLabel(f"{label}:")
            lbl.setProperty("role", "section")
            root.addWidget(lbl)
            root.addWidget(QLabel(f"    {valor}"))

        root.addStretch()
        btn = QPushButton("Fechar")
        btn.clicked.connect(self.accept)
        root.addWidget(btn, alignment=Qt.AlignmentFlag.AlignRight)


# ═══════════════════════════════════════════════════════════════════════════
#  Bloco de Notas
# ═══════════════════════════════════════════════════════════════════════════

class BlocoNotasDialog(QDialog):
    """Editor simples de texto. Retorna conteúdo via .conteudo."""

    def __init__(self, conteudo_inicial: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bloco de Anotações")
        self.resize(600, 400)
        self.setModal(True)
        self.conteudo = conteudo_inicial

        root = QVBoxLayout(self)
        root.setContentsMargins(15, 15, 15, 15)

        self.text = QTextEdit()
        self.text.setPlainText(conteudo_inicial)
        root.addWidget(self.text, stretch=1)

        bar = QHBoxLayout()
        bar.addStretch()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        bar.addWidget(btn_cancel)
        btn_save = QPushButton("Salvar")
        btn_save.setProperty("class", "primary")
        btn_save.clicked.connect(self._salvar)
        bar.addWidget(btn_save)
        root.addLayout(bar)

    def _salvar(self):
        self.conteudo = self.text.toPlainText()
        self.accept()
