"""
Diálogos de Utilitários — PyQt6.
Auditoria, backup/restauração, verificação de permissões.
"""

import os
import glob
import shutil
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QListWidget, QMessageBox,
)
from PyQt6.QtCore import Qt

from core.database import Database, APP_DIR, get_db_path


_PASTA_BACKUPS = os.path.join(APP_DIR, "Backups")


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
    """Permite selecionar e restaurar um backup do banco de dados."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🔄 Restaurar Backup")
        self.resize(550, 420)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        layout.addWidget(QLabel("Selecione um backup para restaurar:"))

        self.lista = QListWidget()
        self._carregar_backups()
        layout.addWidget(self.lista, stretch=1)

        btn_layout = QHBoxLayout()
        btn_restaurar = QPushButton("Restaurar Selecionado")
        btn_restaurar.setProperty("class", "primary")
        btn_restaurar.clicked.connect(self._restaurar)
        btn_layout.addWidget(btn_restaurar)

        btn_fechar = QPushButton("Cancelar")
        btn_fechar.clicked.connect(self.close)
        btn_layout.addWidget(btn_fechar)

        layout.addLayout(btn_layout)

    def _carregar_backups(self):
        self.lista.clear()
        os.makedirs(_PASTA_BACKUPS, exist_ok=True)
        arquivos = sorted(
            glob.glob(os.path.join(_PASTA_BACKUPS, "*.db")),
            key=os.path.getmtime,
            reverse=True,
        )
        for arq in arquivos:
            self.lista.addItem(os.path.basename(arq))

        if self.lista.count() == 0:
            self.lista.addItem("(nenhum backup encontrado)")

    def _restaurar(self):
        item = self.lista.currentItem()
        if not item or item.text().startswith("("):
            QMessageBox.warning(self, "Aviso", "Selecione um backup válido.")
            return

        nome = item.text()
        caminho_bak = os.path.join(_PASTA_BACKUPS, nome)

        resp = QMessageBox.question(
            self, "Confirmar Restauração",
            f"Deseja restaurar o backup:\n{nome}\n\n"
            "O banco atual será substituído. Esta ação não pode ser desfeita.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        try:
            destino = get_db_path()
            shutil.copy2(caminho_bak, destino)
            QMessageBox.information(
                self, "Sucesso",
                "Backup restaurado com sucesso!\n"
                "Reinicie a aplicação para usar o banco restaurado.",
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha ao restaurar:\n{e}")


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


class FazerBackupDialog(QDialog):
    """Cria um backup do banco de dados atual."""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("💾 Fazer Backup")
        self.resize(400, 200)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        layout.addWidget(QLabel("Clique no botão abaixo para criar um backup do banco de dados."))
        layout.addStretch()

        btn = QPushButton("💾 Criar Backup Agora")
        btn.setProperty("class", "primary")
        btn.setMinimumHeight(40)
        btn.clicked.connect(self._fazer_backup)
        layout.addWidget(btn)

        layout.addStretch()

    def _fazer_backup(self):
        try:
            os.makedirs(_PASTA_BACKUPS, exist_ok=True)

            # Flush WAL
            self.db.conn.execute("PRAGMA wal_checkpoint(FULL)")

            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            destino = os.path.join(_PASTA_BACKUPS, f"seaghb_backup_{ts}.db")
            shutil.copy2(get_db_path(), destino)

            # Rotação: manter no máximo 10 backups
            backups = sorted(
                glob.glob(os.path.join(_PASTA_BACKUPS, "seaghb_backup_*.db")),
                key=os.path.getmtime,
            )
            while len(backups) > 10:
                os.remove(backups.pop(0))

            QMessageBox.information(
                self, "Sucesso",
                f"Backup criado com sucesso:\n{os.path.basename(destino)}",
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha ao criar backup:\n{e}")
