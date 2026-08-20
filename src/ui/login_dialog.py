"""
Diálogo de login — substitui o Toplevel do Tkinter.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QMessageBox,
)
from PyQt6.QtCore import Qt


class LoginDialog(QDialog):
    """
    Retorna QDialog.Accepted se as credenciais estiverem corretas,
    QDialog.Rejected caso contrário.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Acesso Restrito — Equipamentos")
        self.setFixedSize(340, 180)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Título
        titulo = QLabel("🔒 Sessão Bloqueada")
        titulo.setProperty("role", "title")
        titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(titulo)

        # Usuário
        row_user = QHBoxLayout()
        row_user.addWidget(QLabel("Usuário:"))
        self.entry_user = QLineEdit()
        self.entry_user.setPlaceholderText("admin")
        row_user.addWidget(self.entry_user)
        layout.addLayout(row_user)

        # Senha
        row_pass = QHBoxLayout()
        row_pass.addWidget(QLabel("Senha:"))
        self.entry_pass = QLineEdit()
        self.entry_pass.setEchoMode(QLineEdit.EchoMode.Password)
        row_pass.addWidget(self.entry_pass)
        layout.addLayout(row_pass)

        # Botão
        self.btn_login = QPushButton("🔓 Desbloquear")
        self.btn_login.setProperty("class", "primary")
        self.btn_login.clicked.connect(self._validar)
        layout.addWidget(self.btn_login)

        # Enter para disparar login
        self.entry_pass.returnPressed.connect(self._validar)

        self.entry_user.setFocus()

    def _validar(self):
        if self.entry_user.text() == "admin" and self.entry_pass.text() == "seguranca123":
            self.accept()
        else:
            QMessageBox.critical(self, "Acesso Negado", "Credenciais inválidas!")
