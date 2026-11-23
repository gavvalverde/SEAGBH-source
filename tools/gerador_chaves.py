"""
Gerenciamento de Clientes — SEAGBH
Ferramenta exclusiva do desenvolvedor.

Executar:  python tools/gerador_chaves.py
"""

import sys
import os
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import json
import hashlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import wraps
from http.client import HTTPException
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode

from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QMessageBox,
    QFrame,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QDateEdit,
    QFileDialog,
    QListWidget,
    QListWidgetItem,
)
from PyQt6.QtCore import Qt, QTimer, QDate, QThread, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QColor, QIcon

from core.firebase_auth import (
    FalhaAuth,
    api_key_firebase as _firebase_api_key,
    carregar_configuracao,
    configuracao_admin_valida,
    login_firebase as _login_firebase,
    logout_firebase as _logout,
    sessao_administrativa_ativa,
    sessao_administrativa_renovavel,
    token_firebase as _get_id_token,
)
from core.historico_licencas import ErroHistorico, HistoricoLicencas
from core.licenca import gerar_chave, validar_chave, PLANOS, FIREBASE_URL, DIAS_ALERTA

_HISTORICO_PATH = os.path.join(os.path.dirname(__file__), "historico_chaves.json")
_LOGGER = logging.getLogger(__name__)


class _SessaoAdminEncerrada(RuntimeError):
    """A interface perdeu a autorização antes de acessar um recurso local."""


def _exigir_sessao_admin() -> None:
    if not sessao_administrativa_ativa():
        raise _SessaoAdminEncerrada("O Gerenciamento de Clientes exige uma sessão administrativa.")


def _acao_administrativa(acao):
    @wraps(acao)
    def executar(janela):
        janela._com_sessao(lambda: acao(janela))
    return executar


# ══════════════════════════════════════════════════════════════════════════════
#  Persistência do histórico
# ══════════════════════════════════════════════════════════════════════════════

def _carregar_historico() -> list[dict]:
    _exigir_sessao_admin()
    try:
        with open(_HISTORICO_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _salvar_historico(historico: list[dict]):
    """Salva histórico com validação de permissões."""
    _exigir_sessao_admin()
    
    try:
        # Validar permissões antes de tentar gravar
        dir_path = os.path.dirname(_HISTORICO_PATH)
        if not os.access(dir_path, os.W_OK):
            raise PermissionError("histórico sem permissão de escrita")
        
        with open(_HISTORICO_PATH, "w", encoding="utf-8") as f:
            json.dump(historico, f, ensure_ascii=False, indent=2)
    except OSError:
        _LOGGER.error("Falha ao salvar histórico de licenças.")
        raise


_ARQUIVO_MORTO_PATH = os.path.join(os.path.dirname(__file__), "historico_excluidos.json")


def _carregar_arquivo_morto() -> list[dict]:
    _exigir_sessao_admin()
    try:
        with open(_ARQUIVO_MORTO_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _salvar_arquivo_morto(morto: list[dict]):
    _exigir_sessao_admin()
    with open(_ARQUIVO_MORTO_PATH, "w", encoding="utf-8") as f:
        json.dump(morto, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
#  Firebase helpers
# ══════════════════════════════════════════════════════════════════════════════

def _calcular_sha256_arquivo(caminho_arquivo: str) -> str:
    """Calcula SHA256 de um arquivo e retorna em hexadecimal."""
    sha256_hash = hashlib.sha256()
    try:
        with open(caminho_arquivo, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()
    except (OSError, IOError):
        return ""


def _firebase_request(method: str, path: str, payload: bytes | None = None) -> tuple[bool, object]:
    """Executa uma operação autenticada; distingue leitura de null de falha."""
    if "SEU-PROJETO" in FIREBASE_URL:
        _LOGGER.error("Realtime Database não configurado.")
        return False, None
    try:
        token = _get_id_token()
        if not token:
            _LOGGER.warning("Sessão de autenticação expirada ou inválida.")
            return False, None
        base_url = f"{FIREBASE_URL}/{path}.json"
        sep = "&" if "?" in base_url else "?"
        url = f"{base_url}{sep}{urlencode({'auth': token})}"
        req = Request(url, data=payload, method=method)
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        if method == "GET":
            req.add_header("Accept", "application/json")
        with urlopen(req, timeout=10 if method == "GET" else 15) as resp:
            if resp.status == 200:
                return True, json.load(resp) if method == "GET" else None
            _LOGGER.warning("Realtime Database: %s recusado | HTTP: %s", method, resp.status)
    except HTTPError as erro:
        with erro:
            if erro.code in (401, 403):
                _logout()
            _LOGGER.warning("Realtime Database: %s recusado | HTTP: %s", method, erro.code)
    except TimeoutError:
        _LOGGER.warning("Realtime Database: %s excedeu o tempo limite.", method)
    except (URLError, OSError, HTTPException):
        _LOGGER.warning("Realtime Database: falha de conexão em %s.", method)
    except (ValueError, RecursionError):
        _LOGGER.warning("Realtime Database: requisição ou resposta inválida em %s.", method)
    return False, None


def _firebase_put(path: str, data) -> bool:
    payload = json.dumps(data).encode("utf-8")
    if len(payload) > 1_000_000:
        _LOGGER.error("Dados muito grandes para Firebase: %s bytes", len(payload))
        return False
    ok, _ = _firebase_request("PUT", path, payload)
    return ok


def _firebase_get(path: str) -> tuple[bool, object]:
    return _firebase_request("GET", path)


def _firebase_delete(path: str) -> bool:
    ok, _ = _firebase_request("DELETE", path)
    return ok


def _excluir_licenca_remota_atomicamente(lid: str) -> bool:
    """Apaga licença e revogação em UM update multilocal do Realtime Database.

    O PATCH em /.json usa caminhos relativos, com null para excluir ambos.
    Se as regras recusarem qualquer caminho, nenhum dos dois deve ser alterado.
    O resultado HTTP não comprova o estado remoto quando a resposta se perde.
    """
    if not isinstance(lid, str) or not re.fullmatch(r"[0-9a-f]{12}", lid):
        _LOGGER.warning("Realtime Database: exclusão recusada | Código: ID_INVALIDO")
        return False

    payload = json.dumps({
        f"licenses/{lid}": None,
        f"revoked/{lid}": None,
    }, separators=(",", ":")).encode("utf-8")
    ok, _ = _firebase_request("PATCH", "", payload)
    return ok


def _calcular_expiry_efetivo(base_expiry: str, plano: str, auto_renew: bool,
                              expiry_override: str | None = None) -> str:
    """Calcula vencimento efetivo considerando auto-renovação e override."""
    if expiry_override:
        base_expiry = expiry_override
    dias_plano = PLANOS.get(plano, 0)
    if dias_plano == 0:
        return base_expiry
    if not auto_renew:
        return base_expiry
    expiry = datetime.strptime(base_expiry, "%Y-%m-%d")
    hoje = datetime.now()
    while expiry < hoje:
        expiry += timedelta(days=dias_plano)
    return expiry.strftime("%Y-%m-%d")


# ══════════════════════════════════════════════════════════════════════════════
#  Toast estilizado (fecha sozinho em 2.5s ou ao clicar)
# ══════════════════════════════════════════════════════════════════════════════

class _ToastDialog(QDialog):
    """Diálogo toast que fecha ao clicar ou após timeout."""
    def mousePressEvent(self, event):
        self.accept()

def _mostrar_toast(parent, titulo: str, corpo: str, cor_borda: str = "#4ADE80"):
    dlg = _ToastDialog(parent)
    dlg.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
    dlg.setFixedWidth(420)
    dlg.setStyleSheet(f"""
        QDialog {{
            background-color: #0F172A;
            border: 2px solid {cor_borda};
            border-radius: 14px;
        }}
        QLabel {{
            background: transparent;
            color: #E2E8F0;
        }}
        QLabel#toast-titulo {{
            font-size: 17px;
            font-weight: 800;
            color: {cor_borda};
        }}
        QLabel#toast-corpo {{
            font-size: 13px;
            color: #CBD5E1;
            line-height: 1.4;
        }}
        QLabel#toast-hint {{
            font-size: 10px;
            color: #475569;
        }}
    """)

    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(28, 22, 28, 18)
    lay.setSpacing(10)

    lbl_titulo = QLabel(titulo)
    lbl_titulo.setObjectName("toast-titulo")
    lay.addWidget(lbl_titulo)

    lbl_corpo = QLabel(corpo)
    lbl_corpo.setObjectName("toast-corpo")
    lbl_corpo.setWordWrap(True)
    lay.addWidget(lbl_corpo)

    lbl_hint = QLabel("Fecha automaticamente...")
    lbl_hint.setObjectName("toast-hint")
    lbl_hint.setAlignment(Qt.AlignmentFlag.AlignRight)
    lay.addWidget(lbl_hint)

    dlg.adjustSize()

    if parent:
        geo = parent.geometry()
        x = geo.x() + (geo.width() - dlg.width()) // 2
        y = geo.y() + (geo.height() - dlg.height()) // 2
        dlg.move(x, y)

    QTimer.singleShot(2500, dlg.accept)
    dlg.exec()


# ══════════════════════════════════════════════════════════════════════════════
#  Login administrativo (Firebase Auth) — executa em thread para não travar a UI
# ══════════════════════════════════════════════════════════════════════════════

_MENSAGEM_CREDENCIAIS = "Não foi possível entrar. Confira o e-mail e a senha."
_MENSAGEM_RESPOSTA = "O Firebase retornou uma resposta inesperada. Consulte o código no terminal."
_MENSAGEM_ADMIN_UID = "Configure uma UID administrativa válida em SEAGBH_FIREBASE_ADMIN_UID."
_MENSAGENS_AUTH = {
    FalhaAuth.CREDENCIAIS_INVALIDAS: _MENSAGEM_CREDENCIAIS,
    FalhaAuth.USUARIO_DESABILITADO: _MENSAGEM_CREDENCIAIS,
    FalhaAuth.API_KEY_AUSENTE: "Configure SEAGBH_FIREBASE_API_KEY antes de entrar.",
    FalhaAuth.ADMIN_UID_INVALIDA: _MENSAGEM_ADMIN_UID,
    FalhaAuth.ACESSO_ADMIN_NEGADO: _MENSAGEM_CREDENCIAIS,
    FalhaAuth.API_KEY_INVALIDA: "A chave de API foi recusada. Confira a chave Web do projeto Firebase.",
    FalhaAuth.API_KEY_RESTRITA: "O Firebase bloqueou esta solicitação pelas restrições da chave de API.",
    FalhaAuth.PROJETO_INDISPONIVEL: "Confira o projeto Firebase e a configuração do Firebase Authentication.",
    FalhaAuth.PROVEDOR_DESATIVADO: "O login por e-mail e senha está desativado no projeto Firebase.",
    FalhaAuth.LIMITE_TENTATIVAS: "O Firebase limitou as tentativas. Aguarde antes de tentar novamente.",
    FalhaAuth.PROVEDOR_INDISPONIVEL: "O serviço de autenticação está indisponível. Tente novamente mais tarde.",
    FalhaAuth.TIMEOUT: "O Firebase demorou a responder. Confira sua conexão e tente novamente.",
    FalhaAuth.REDE_INDISPONIVEL: "Não foi possível conectar ao Firebase. Confira sua conexão.",
}


class _LoginWorker(QThread):
    """Executa o login Firebase em thread separada (não congela a interface)."""

    concluido = pyqtSignal(bool, str)  # sucesso, mensagem de erro (vazia se ok)

    def __init__(self, email: str, senha: str, parent=None):
        super().__init__(parent)
        self._email = email
        self._senha = senha
        self._falha: FalhaAuth | None = FalhaAuth.RESPOSTA_INVALIDA
        self.finished.connect(self._emitir_resultado)

    def run(self):
        try:
            self._falha = _login_firebase(self._email, self._senha)
        except Exception:
            _logout()
            _LOGGER.error("Firebase Auth: login recusado | Código: ERRO_INTERNO")
        finally:
            self._senha = ""

    def _emitir_resultado(self):
        if self._falha is None:
            self.concluido.emit(True, "")
        else:
            self.concluido.emit(False, _MENSAGENS_AUTH.get(self._falha, _MENSAGEM_RESPOSTA))


class LoginDialog(QDialog):
    """Diálogo de autenticação administrativa (Firebase Auth email/senha)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Autenticação — Gerenciamento de Clientes")
        self.setModal(True)
        self.setMinimumWidth(460)
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 28, 32, 28)
        lay.setSpacing(12)

        titulo = QLabel("Entrar no Gerenciamento de Clientes")
        titulo.setObjectName("titulo")
        lay.addWidget(titulo)

        sub = QLabel("Use a conta do projeto Firebase. A senha é usada apenas para este login.")
        sub.setObjectName("subtitle")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        self.entry_email = QLineEdit()
        self.entry_email.setPlaceholderText("E-mail administrativo")
        lay.addWidget(self.entry_email)

        self.entry_senha = QLineEdit()
        self.entry_senha.setPlaceholderText("Senha")
        self.entry_senha.setEchoMode(QLineEdit.EchoMode.Password)
        self.entry_senha.returnPressed.connect(self._on_entrar)
        lay.addWidget(self.entry_senha)

        self.lbl_erro = QLabel("")
        self.lbl_erro.setWordWrap(True)
        self.lbl_erro.setStyleSheet("color: #F87171; background: transparent;")
        lay.addWidget(self.lbl_erro)

        btns = QHBoxLayout()
        btns.setSpacing(10)

        self.btn_entrar = QPushButton("Entrar")
        self.btn_entrar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_entrar.clicked.connect(self._on_entrar)
        btns.addWidget(self.btn_entrar)

        self.btn_cancelar = QPushButton("Cancelar")
        self.btn_cancelar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancelar.clicked.connect(self.reject)
        btns.addWidget(self.btn_cancelar)

        lay.addLayout(btns)

    def _set_autenticando(self, ativo: bool):
        self.entry_email.setEnabled(not ativo)
        self.entry_senha.setEnabled(not ativo)
        self.btn_entrar.setEnabled(not ativo)
        self.btn_cancelar.setEnabled(not ativo)
        self.btn_entrar.setText("Autenticando..." if ativo else "Entrar")

    def _on_entrar(self):
        if self._worker is not None:
            return
        email = self.entry_email.text().strip()
        senha = self.entry_senha.text()
        if not email or not senha:
            self.lbl_erro.setText("Informe e-mail e senha.")
            return
        self.lbl_erro.setText("")
        self._set_autenticando(True)
        self._worker = _LoginWorker(email, senha, parent=self)
        self.entry_senha.setText("")
        self._worker.concluido.connect(self._on_login_concluido)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_login_concluido(self, ok: bool, msg: str):
        self._set_autenticando(False)
        self._worker = None
        if ok and sessao_administrativa_ativa():
            self.accept()
        else:
            self.lbl_erro.setText(msg or _MENSAGEM_RESPOSTA)

    def reject(self):
        if self._worker is not None:
            self.lbl_erro.setText("Autenticação em andamento. Aguarde...")
            return
        self.entry_senha.setText("")
        super().reject()


# ══════════════════════════════════════════════════════════════════════════════
#  Janela principal
# ══════════════════════════════════════════════════════════════════════════════

class _RenovacaoWorker(QThread):
    def run(self):
        try:
            _get_id_token()
        except Exception:
            _logout()
            _LOGGER.error("Firebase Auth: renovação recusada | Código: ERRO_INTERNO")


class _GravacaoHistoricoWorker(QThread):
    def __init__(self, gravar: Callable[[], None], pai):
        super().__init__(pai)
        self._gravar = gravar
        self.erro = None

    def run(self):
        try:
            _exigir_sessao_admin()
            self._gravar()
        except (ErroHistorico, _SessaoAdminEncerrada) as erro:
            self.erro = erro
        except Exception:
            self.erro = ErroHistorico("falha_armazenamento")


class GerenciamentoClientes(QWidget):
    def __init__(self, *, historico: HistoricoLicencas | None = None):
        _exigir_sessao_admin()
        super().__init__()
        self._historico_privado = historico
        self.setWindowTitle("Gerenciamento de Clientes — SEAGBH")
        self.setMinimumSize(1020, 750)
        self._firebase_cache: dict[str, dict] = {}
        self._renovacao: _RenovacaoWorker | None = None
        self._acao_apos_renovacao: Callable[[], None] | None = None
        self._gravacao: _GravacaoHistoricoWorker | None = None
        self._apos_gravacao: Callable[[], None] | None = None
        self._dialogo_gravacao: QDialog | None = None
        self._fechar_apos_worker = False
        self._build_ui()
        self._carregar_firebase_dados()
        self._carregar_tabela()

    def _com_sessao(self, acao: Callable[[], None]) -> None:
        if self._renovacao is not None or self._gravacao is not None:
            return
        if sessao_administrativa_ativa():
            self._executar_acao_autorizada(acao)
        elif sessao_administrativa_renovavel():
            self._acao_apos_renovacao = acao
            self._renovacao = _RenovacaoWorker(self)
            self._renovacao.finished.connect(self._concluir_renovacao)
            self._renovacao.finished.connect(self._renovacao.deleteLater)
            self.setEnabled(False)
            self.setCursor(Qt.CursorShape.WaitCursor)
            self._renovacao.start()
        else:
            self._avisar_sessao_encerrada()

    def _concluir_renovacao(self) -> None:
        acao = self._acao_apos_renovacao
        self._acao_apos_renovacao = None
        self._renovacao = None
        self.setEnabled(True)
        self.unsetCursor()
        if self._fechar_apos_worker:
            self.close()
        elif acao is not None and sessao_administrativa_ativa():
            self._executar_acao_autorizada(acao)
        else:
            self._avisar_sessao_encerrada()

    def closeEvent(self, event):
        if self._renovacao is not None or self._gravacao is not None:
            self._fechar_apos_worker = True
            event.ignore()
            return
        _logout()
        super().closeEvent(event)

    def _executar_acao_autorizada(self, acao: Callable[[], None], *, dialogo: QDialog | None = None) -> None:
        if self._gravacao is not None:
            return
        try:
            _exigir_sessao_admin()
            acao()
        except _SessaoAdminEncerrada:
            if dialogo is not None:
                dialogo.reject()
            self._avisar_sessao_encerrada()
        except ErroHistorico as erro:
            self._avisar_falha_historico(erro, dialogo)

    def _avisar_falha_historico(self, erro: ErroHistorico, dialogo: QDialog | None):
        _LOGGER.warning("Histórico administrativo indisponível | Código: %s", erro.codigo)
        QMessageBox.critical(
            dialogo or self, "Falha no histórico local",
            "O histórico local não pôde ser confirmado. Se houve uma alteração no Firebase, "
            "confira o estado remoto antes de repetir a operação.",
        )

    def _gravar_privado(self, gravar, concluir, *, dialogo=None):
        _exigir_sessao_admin()
        self._apos_gravacao = concluir
        self._dialogo_gravacao = dialogo
        self._gravacao = _GravacaoHistoricoWorker(gravar, self)
        self._gravacao.finished.connect(self._concluir_gravacao)
        self.setEnabled(False)
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            self._gravacao.start()
        except RuntimeError:
            self._gravacao.erro = ErroHistorico("falha_armazenamento")
            self._concluir_gravacao()

    def _concluir_gravacao(self):
        worker = self._gravacao
        if worker is None:
            return
        if not worker.wait(0):
            QTimer.singleShot(10, self._concluir_gravacao)
            return
        concluir, dialogo = self._apos_gravacao, self._dialogo_gravacao
        self._gravacao = self._apos_gravacao = self._dialogo_gravacao = None
        self.setEnabled(True)
        self.unsetCursor()
        erro = worker.erro
        worker.deleteLater()
        if isinstance(erro, ErroHistorico):
            self._avisar_falha_historico(erro, dialogo)
        if self._fechar_apos_worker:
            self.close()
        elif erro is None and concluir is not None:
            self._executar_acao_autorizada(concluir, dialogo=dialogo)
        elif isinstance(erro, _SessaoAdminEncerrada):
            if dialogo is not None:
                dialogo.reject()
            self._avisar_sessao_encerrada()

    def _avisar_sessao_encerrada(self) -> None:
        self._firebase_cache.clear()
        self.tabela.setRowCount(0)
        QMessageBox.warning(
            self, "Sessão encerrada",
            "Entre novamente no Gerenciamento de Clientes para realizar esta ação.",
        )

    # ══════════════════════════════════════════════════════════════════════
    #  UI
    # ══════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)

        titulo = QLabel("Gerenciamento de Clientes")
        titulo.setObjectName("titulo")
        root.addWidget(titulo)

        status_fb = "Configurado" if "SEU-PROJETO" not in FIREBASE_URL else "Não configurado"
        lbl_fb = QLabel(f"Firebase: {status_fb}")
        lbl_fb.setObjectName("subtitle")
        root.addWidget(lbl_fb)

        # ── Formulário + Botão gerar ──────────────────────────────────────
        form_row = QHBoxLayout()
        form_row.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(8)
        self.entry_cliente = QLineEdit()
        self.entry_cliente.setPlaceholderText("Nome da empresa ou cliente")
        self.entry_cliente.setMinimumWidth(240)
        form.addRow("Cliente:", self.entry_cliente)

        self.combo_plano = QComboBox()
        for nome, dias in PLANOS.items():
            label = "Vitalício (sem expiração)" if dias == 0 else f"{nome.capitalize()} ({dias} dias)"
            self.combo_plano.addItem(label, nome)
        form.addRow("Plano:", self.combo_plano)
        form_row.addLayout(form)

        btn_gerar = QPushButton("Gerar Chave")
        btn_gerar.setObjectName("btn-gerar")
        btn_gerar.setFixedHeight(72)
        btn_gerar.setMinimumWidth(180)
        btn_gerar.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_gerar.clicked.connect(self._on_gerar)
        form_row.addWidget(btn_gerar)

        root.addLayout(form_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #334155;")
        root.addWidget(sep)

        # ── Busca + Tabela ─────────────────────────────────────────────────
        search_row = QHBoxLayout()
        search_row.setSpacing(10)

        lbl_hist = QLabel("Licenças")
        lbl_hist.setObjectName("subtitle")
        search_row.addWidget(lbl_hist)

        search_row.addStretch()

        self.entry_busca = QLineEdit()
        self.entry_busca.setPlaceholderText("Buscar cliente...")
        self.entry_busca.setFixedWidth(260)
        self.entry_busca.setObjectName("search-box")
        self.entry_busca.textChanged.connect(self._filtrar_tabela)
        search_row.addWidget(self.entry_busca)

        root.addLayout(search_row)

        self.tabela = QTableWidget()
        self.tabela.setColumnCount(7)
        self.tabela.setHorizontalHeaderLabels(
            ["Cliente", "Plano", "Emissão", "Vencimento", "Renovação", "ID", "Status"]
        )
        self.tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabela.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.verticalHeader().setVisible(False)
        hdr = self.tabela.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 7):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.tabela.setMinimumHeight(220)
        root.addWidget(self.tabela, stretch=1)

        # ── Linha de ações 1 ──────────────────────────────────────────────
        bar1 = QHBoxLayout()
        bar1.setSpacing(10)

        btn_copiar = QPushButton("Copiar Chave")
        btn_copiar.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_copiar.clicked.connect(self._on_copiar)
        bar1.addWidget(btn_copiar)

        btn_revogar = QPushButton("Revogar")
        btn_revogar.setObjectName("btn-revogar")
        btn_revogar.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_revogar.clicked.connect(self._on_revogar)
        bar1.addWidget(btn_revogar)

        btn_reativar = QPushButton("Reativar")
        btn_reativar.setObjectName("btn-reativar")
        btn_reativar.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reativar.clicked.connect(self._on_reativar)
        bar1.addWidget(btn_reativar)

        btn_excluir = QPushButton("Excluir")
        btn_excluir.setObjectName("btn-excluir")
        btn_excluir.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_excluir.clicked.connect(self._on_excluir)
        bar1.addWidget(btn_excluir)

        bar1.addStretch()
        root.addLayout(bar1)

        # ── Linha de ações 2 (gerenciamento) ──────────────────────────────
        bar2 = QHBoxLayout()
        bar2.setSpacing(10)

        btn_auto = QPushButton("Auto-Renovação")
        btn_auto.setObjectName("btn-auto")
        btn_auto.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_auto.clicked.connect(self._on_toggle_auto_renew)
        bar2.addWidget(btn_auto)

        btn_mute = QPushButton("Silenciar Alertas")
        btn_mute.setObjectName("btn-mute")
        btn_mute.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_mute.clicked.connect(self._on_mute_alerts)
        bar2.addWidget(btn_mute)

        btn_edit = QPushButton("Editar Vencimento")
        btn_edit.setObjectName("btn-edit-date")
        btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_edit.clicked.connect(self._on_edit_expiry)
        bar2.addWidget(btn_edit)

        btn_exportar = QPushButton("Exportar PDF")
        btn_exportar.setObjectName("btn-exportar")
        btn_exportar.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_exportar.clicked.connect(self._on_exportar_pdf)
        bar2.addWidget(btn_exportar)

        btn_restaurar = QPushButton("Restaurar")
        btn_restaurar.setObjectName("btn-restaurar")
        btn_restaurar.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_restaurar.clicked.connect(self._on_restaurar)
        bar2.addWidget(btn_restaurar)

        btn_publicar = QPushButton("Publicar Atualização")
        btn_publicar.setObjectName("btn-publicar")
        btn_publicar.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_publicar.clicked.connect(self._on_publicar_atualizacao)
        bar2.addWidget(btn_publicar)

        bar2.addStretch()
        root.addLayout(bar2)

    # ══════════════════════════════════════════════════════════════════════
    #  Firebase — carregar dados em lote
    # ══════════════════════════════════════════════════════════════════════

    def _ler_historico(self, *, excluidos=False):
        _exigir_sessao_admin()
        if self._historico_privado is not None:
            if excluidos:
                return self._historico_privado.ler_excluidos()
            return self._historico_privado.ler_ativos()
        return _carregar_arquivo_morto() if excluidos else _carregar_historico()

    def _adicionar_registro(self, registro, concluir):
        _exigir_sessao_admin()
        banco = self._historico_privado
        if banco is not None:
            self._gravar_privado(lambda: banco.adicionar(registro), concluir)
        else:
            historico = _carregar_historico()
            historico.append(registro)
            _salvar_historico(historico)
            concluir()

    def _atualizar_registro(self, anterior, concluir, **mudancas):
        _exigir_sessao_admin()
        banco = self._historico_privado
        if banco is not None:
            self._gravar_privado(lambda: banco.atualizar(anterior, **mudancas), concluir)
        else:
            historico = _carregar_historico()
            for item in historico:
                if item["id"] == anterior["id"]:
                    item.update(mudancas)
                    break
            _salvar_historico(historico)
            concluir()

    def _carregar_firebase_dados(self):
        """Carrega dados de auto_renew / alertas para todas as licenças."""
        historico = self._ler_historico()
        for reg in historico:
            lid = reg.get("id", "")
            if not lid:
                continue
            ok, data = _firebase_get(f"licenses/{lid}")
            if ok:
                self._firebase_cache[lid] = data if isinstance(data, dict) else {}

    def _get_fb(self, license_id: str) -> dict:
        return self._firebase_cache.get(license_id, {})

    # ══════════════════════════════════════════════════════════════════════
    #  Tabela
    # ══════════════════════════════════════════════════════════════════════

    def _carregar_tabela(self):
        historico = self._ler_historico()
        filtro = self.entry_busca.text().strip().lower() if hasattr(self, 'entry_busca') else ""
        if filtro:
            historico = [r for r in historico if filtro in r.get("cliente", "").lower()]

        self.tabela.setRowCount(len(historico))

        hoje_str = datetime.now().strftime("%Y-%m-%d")

        for row, reg in enumerate(historico):
            plano = reg.get("plano", "")
            base_expiry = reg.get("expira", "")
            lid = reg.get("id", "")
            is_vitalicio = plano == "vitalicio"

            fb = self._get_fb(lid)
            auto_renew = fb.get("auto_renew", False)
            expiry_override = fb.get("expiry_override")

            # Vencimento efetivo
            if is_vitalicio:
                expiry_display = "Permanente"
            else:
                expiry_display = _calcular_expiry_efetivo(
                    base_expiry, plano, auto_renew, expiry_override
                )

            self.tabela.setItem(row, 0, QTableWidgetItem(reg.get("cliente", "")))
            self.tabela.setItem(
                row, 1,
                QTableWidgetItem("Vitalício" if is_vitalicio else plano.capitalize()),
            )
            self.tabela.setItem(row, 2, QTableWidgetItem(reg.get("emissao", "")))
            self.tabela.setItem(row, 3, QTableWidgetItem(expiry_display))

            # Auto-renovação
            auto_item = QTableWidgetItem("Ativo" if auto_renew else "Inativo")
            auto_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            auto_item.setForeground(QColor("#4ADE80" if auto_renew else "#94A3B8"))
            self.tabela.setItem(row, 4, auto_item)

            self.tabela.setItem(row, 5, QTableWidgetItem(lid))

            # Status
            status = reg.get("status", "ativa")
            if status == "revogada":
                item_status = QTableWidgetItem("REVOGADA")
                cor = QColor("#F87171")
            elif (
                not is_vitalicio
                and expiry_display != "Permanente"
                and expiry_display < hoje_str
            ):
                item_status = QTableWidgetItem("EXPIRADA")
                cor = QColor("#FBBF24")
            else:
                item_status = QTableWidgetItem("ATIVA")
                cor = QColor("#4ADE80")

            item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_status.setForeground(cor)
            self.tabela.setItem(row, 6, item_status)

            # Colorir fundo da linha para licenças prestes a expirar
            if (
                not is_vitalicio
                and status != "revogada"
                and expiry_display != "Permanente"
                and expiry_display >= hoje_str
            ):
                dias_alerta = DIAS_ALERTA.get(plano, 7)
                try:
                    dias_rest = (datetime.strptime(expiry_display, "%Y-%m-%d") - datetime.now()).days + 1
                except ValueError:
                    dias_rest = 999

                if 0 < dias_rest <= 3:
                    bg = QColor("#3B1111")  # vermelho escuro
                elif 0 < dias_rest <= dias_alerta:
                    bg = QColor("#3B2F08")  # amarelo escuro
                else:
                    bg = None

                if bg:
                    for col in range(self.tabela.columnCount()):
                        item = self.tabela.item(row, col)
                        if item:
                            item.setBackground(bg)

        if historico:
            self.tabela.scrollToBottom()

    @_acao_administrativa
    def _filtrar_tabela(self):
        """Recarrega a tabela aplicando o filtro de busca."""
        self._carregar_tabela()

    def _linha_selecionada(self) -> dict | None:
        rows = self.tabela.selectionModel().selectedRows()
        if not rows:
            return None
        idx = rows[0].row()
        if self._historico_privado is not None:
            id_exibido = self.tabela.item(idx, 5).text()
            return next((reg for reg in self._ler_historico() if reg["id"] == id_exibido), None)
        # Recalcular o histórico filtrado para achar o registro correto
        historico = _carregar_historico()
        filtro = self.entry_busca.text().strip().lower() if hasattr(self, 'entry_busca') else ""
        if filtro:
            historico = [r for r in historico if filtro in r.get("cliente", "").lower()]
        if 0 <= idx < len(historico):
            return historico[idx]
        return None

    # ══════════════════════════════════════════════════════════════════════
    #  Ações — Gerar / Copiar / Revogar / Reativar
    # ══════════════════════════════════════════════════════════════════════

    @_acao_administrativa
    def _on_gerar(self):
        cliente = self.entry_cliente.text().strip()
        if not cliente:
            QMessageBox.warning(self, "Aviso", "Informe o nome do cliente!")
            return

        plano = self.combo_plano.currentData()
        chave = gerar_chave(cliente, plano)
        payload = validar_chave(chave)

        # Auto-renovação ativa por padrão — persistir no Firebase ANTES de efeitos locais
        if not _firebase_put(f"licenses/{payload['id']}/auto_renew", True):
            QMessageBox.critical(self, "Erro", "Falha ao conectar ao Firebase.")
            return

        self._adicionar_registro({
            "id": payload["id"],
            "cliente": payload["c"],
            "plano": payload["p"],
            "emissao": payload["i"],
            "expira": payload["e"],
            "chave": chave,
            "status": "ativa",
        }, lambda: self._concluir_emissao(payload, chave))

    def _concluir_emissao(self, payload, chave):
        self._firebase_cache[payload["id"]] = {"auto_renew": True}

        self._carregar_tabela()
        QGuiApplication.clipboard().setText(chave)

        plano_fmt = "Vitalício" if payload["p"] == "vitalicio" else payload["p"].capitalize()
        expira_fmt = "Permanente" if payload["p"] == "vitalicio" else payload["e"]
        _mostrar_toast(
            self,
            titulo="Chave Gerada com Sucesso",
            corpo=(
                f"Cliente: {payload['c']}\n"
                f"Plano: {plano_fmt}\n"
                f"Expira: {expira_fmt}\n"
                f"ID: {payload['id']}\n\n"
                f"Chave copiada para a área de transferência!\n"
                f"Auto-Renovação ativada por padrão."
            ),
            cor_borda="#4ADE80",
        )
        self.entry_cliente.clear()

    def _on_copiar(self):
        self._com_sessao(self._copiar_chave)

    def _copiar_chave(self):
        reg = self._linha_selecionada()
        if not reg:
            QMessageBox.warning(self, "Aviso", "Selecione uma licença na tabela!")
            return
        QGuiApplication.clipboard().setText(reg["chave"])
        _mostrar_toast(
            self,
            titulo="Chave Copiada",
            corpo=f"Chave do cliente \"{reg['cliente']}\" copiada\npara a área de transferência.",
            cor_borda="#6BD1FF",
        )

    @_acao_administrativa
    def _on_revogar(self):
        reg = self._linha_selecionada()
        if not reg:
            QMessageBox.warning(self, "Aviso", "Selecione uma licença na tabela!")
            return
        if reg.get("status") == "revogada":
            QMessageBox.information(self, "Info", "Esta licença já está revogada.")
            return

        confirma = QMessageBox.warning(
            self,
            "Confirmar Revogação",
            f"Tem certeza que deseja revogar a licença?\n\n"
            f"Cliente: {reg['cliente']}\n"
            f"ID: {reg['id']}\n\n"
            f"A licença permanecerá ativa por 24 horas no Firebase.\n"
            f"Após isso, o acesso será bloqueado.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirma != QMessageBox.StandardButton.Yes:
            return
        _exigir_sessao_admin()

        # ── Armazenar timestamp de revogação (grace period de 24h) ────────────
        revoked_at = datetime.now().isoformat()
        
        if not _firebase_put(f"licenses/{reg['id']}/revoked_at", revoked_at):
            QMessageBox.critical(self, "Erro", "Falha ao conectar ao Firebase.")
            return

        self._atualizar_registro(reg, lambda: self._concluir_revogacao(reg), status="revogada")

    def _concluir_revogacao(self, reg):
        self._carregar_tabela()

        _mostrar_toast(
            self,
            titulo="Licença Revogada",
            corpo=(
                f"A licença de \"{reg['cliente']}\" foi revogada.\n"
                f"Permanecerá ativa por 24 horas.\n"
                f"Após isso, o acesso será bloqueado na próxima verificação."
            ),
            cor_borda="#F87171",
        )

    @_acao_administrativa
    def _on_reativar(self):
        reg = self._linha_selecionada()
        if not reg:
            QMessageBox.warning(self, "Aviso", "Selecione uma licença na tabela!")
            return
        if reg.get("status") != "revogada":
            QMessageBox.information(self, "Info", "Esta licença já está ativa.")
            return

        # ── Remover timestamp de revogação IMEDIATAMENTE ──────────────────────
        if not _firebase_delete(f"licenses/{reg['id']}/revoked_at"):
            QMessageBox.critical(self, "Erro", "Falha ao conectar ao Firebase.")
            return

        self._atualizar_registro(reg, lambda: self._concluir_reativacao(reg), status="ativa")

    def _concluir_reativacao(self, reg):
        self._carregar_tabela()

        QGuiApplication.clipboard().setText(reg["chave"])
        _mostrar_toast(
            self,
            titulo="Licença Reativada",
            corpo=(
                f"A licença de \"{reg['cliente']}\" foi reativada imediatamente.\n"
                f"A chave foi copiada para a área de transferência.\n\n"
                f"Envie ao cliente para que ele cole na tela de ativação."
            ),
            cor_borda="#4ADE80",
        )

    # ══════════════════════════════════════════════════════════════════════
    #  Ações — Excluir / Renovar / Exportar CSV
    # ══════════════════════════════════════════════════════════════════════

    @_acao_administrativa
    def _on_excluir(self):
        reg = self._linha_selecionada()
        if not reg:
            QMessageBox.warning(self, "Aviso", "Selecione uma licença na tabela!")
            return

        confirma = QMessageBox.warning(
            self,
            "Confirmar Exclusão",
            f"Tem certeza que deseja excluir este registro?\n\n"
            f"Cliente: {reg['cliente']}\n"
            f"ID: {reg['id']}\n\n"
            f"Você poderá restaurá-lo depois pelo botão Restaurar.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirma != QMessageBox.StandardButton.Yes:
            return
        _exigir_sessao_admin()

        lid = reg["id"]

        # A remocao dos dois caminhos remotos deve ser uma unica escrita atomica.
        # Em timeout, o estado remoto pode ser incerto; nunca marque sucesso local.
        if not _excluir_licenca_remota_atomicamente(lid):
            QMessageBox.critical(
                self, "Erro de sincronização",
                "A exclusão no Firebase não foi confirmada. O registro local foi "
                "preservado. Confira o estado remoto antes de repetir.",
            )
            return
        _exigir_sessao_admin()
        banco = self._historico_privado
        if banco is not None:
            self._gravar_privado(lambda: banco.mover(reg, para_excluidos=True),
                                 lambda: self._concluir_exclusao(reg))
            return
        morto = _carregar_arquivo_morto()
        morto.append(reg)
        _salvar_arquivo_morto(morto)
        historico = [h for h in _carregar_historico() if h.get("id") != lid]
        _salvar_historico(historico)
        self._concluir_exclusao(reg)

    def _concluir_exclusao(self, reg):
        self._firebase_cache.pop(reg["id"], None)
        self._carregar_tabela()

        _mostrar_toast(
            self,
            titulo="Registro Excluído",
            corpo=(
                f"Licença de \"{reg['cliente']}\" movida para o arquivo morto.\n"
                f"Firebase sincronizado — licença removida do servidor.\n"
                f"Use o botão Restaurar para recuperá-la."
            ),
            cor_borda="#94A3B8",
        )

    def _on_restaurar(self):
        self._com_sessao(self._abrir_restauracao)

    def _abrir_restauracao(self):
        morto = self._ler_historico(excluidos=True)
        if not morto:
            QMessageBox.information(self, "Info", "Não há licenças excluídas para restaurar.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Restaurar Licença")
        dlg.setFixedSize(520, 380)
        dlg.setStyleSheet("""
            QDialog { background-color: #0F172A; color: #E2E8F0; }
            QLabel { background: transparent; color: #E2E8F0; font-size: 13px; }
            QListWidget {
                background-color: #1E293B; color: #E2E8F0;
                border: 1px solid #334155; border-radius: 8px;
                padding: 6px; font-size: 13px;
                outline: none;
            }
            QListWidget::item {
                padding: 8px 10px;
                border-bottom: 1px solid #334155;
            }
            QListWidget::item:selected {
                background-color: #0D3B66;
            }
            QPushButton {
                background-color: #0369A1; color: white; border: none;
                border-radius: 8px; padding: 10px 20px; font-weight: 700;
            }
            QPushButton:hover { background-color: #0284C7; }
        """)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        lay.addWidget(QLabel("Selecione a licença para restaurar:"))

        lista = QListWidget()
        for reg in morto:
            plano = reg.get("plano", "")
            plano_fmt = "Vitalício" if plano == "vitalicio" else plano.capitalize()
            expira = "Permanente" if plano == "vitalicio" else reg.get("expira", "")
            texto = f"{reg.get('cliente', '')}  —  {plano_fmt}  —  Venc: {expira}  —  ID: {reg.get('id', '')}"
            item = QListWidgetItem(texto)
            item.setData(Qt.ItemDataRole.UserRole, reg.get("id"))
            lista.addItem(item)
        lay.addWidget(lista, stretch=1)

        btn = QPushButton("Restaurar Selecionada")
        lay.addWidget(btn)

        def _restaurar():
            sel = lista.currentItem()
            if not sel:
                return
            lid = sel.data(Qt.ItemDataRole.UserRole)

            # Encontrar registro no arquivo morto
            registro = None
            novo_morto = []
            for r in morto:
                if r.get("id") == lid and registro is None:
                    registro = r
                else:
                    novo_morto.append(r)

            if not registro:
                return

            # Verificar duplicata no histórico ativo
            historico = self._ler_historico()
            for h in historico:
                if h.get("id") == lid:
                    QMessageBox.information(dlg, "Info", "Esta licença já existe no histórico.")
                    return

            # ── Sincronizar com Firebase: recriar a licença ─────────────────
            caminho = f"licenses/{lid}"
            ok, fb_data = _firebase_get(caminho)
            if not ok:
                QMessageBox.critical(dlg, "Erro", "Falha ao consultar a licença no Firebase.")
                return
            if fb_data is None:
                fb_data = {
                    "auto_renew": False,
                    "alerts_muted_until": None,
                    "expiry_override": None
                }
                if not _firebase_put(caminho, fb_data):
                    QMessageBox.critical(dlg, "Erro", "Falha ao restaurar a licença no Firebase.")
                    return

            _exigir_sessao_admin()
            banco = self._historico_privado
            if banco is not None:
                self._gravar_privado(
                    lambda: banco.mover(registro, para_excluidos=False),
                    lambda: self._concluir_restauracao(dlg, registro, fb_data),
                    dialogo=dlg,
                )
            else:
                historico.append(registro)
                _salvar_historico(historico)
                _salvar_arquivo_morto(novo_morto)
                self._concluir_restauracao(dlg, registro, fb_data)

        btn.clicked.connect(lambda: self._executar_acao_autorizada(_restaurar, dialogo=dlg))
        dlg.exec()

    def _concluir_restauracao(self, dlg, registro, fb_data):
        self._firebase_cache[registro["id"]] = fb_data if isinstance(fb_data, dict) else {}
        self._carregar_tabela()
        dlg.accept()

        plano = registro.get("plano", "")
        plano_fmt = "Vitalício" if plano == "vitalicio" else plano.capitalize()
        _mostrar_toast(
            self,
            titulo="Licença Restaurada",
            corpo=(
                f"Cliente: {registro['cliente']}\n"
                f"Plano: {plano_fmt}\n"
                f"ID: {registro['id']}\n\n"
                f"Registro restaurado ao histórico."
            ),
            cor_borda="#4ADE80",
        )

    def _on_exportar_pdf(self):
        self._com_sessao(self._selecionar_destino_pdf)

    def _selecionar_destino_pdf(self):
        caminho, _ = QFileDialog.getSaveFileName(
            self, "Exportar Licenças", "licencas_seagbh.pdf",
            "PDF (*.pdf)"
        )
        if caminho:
            self._com_sessao(lambda: self._exportar_pdf(caminho))

    def _exportar_pdf(self, caminho):
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

        historico = self._ler_historico()
        hoje_str = datetime.now().strftime("%Y-%m-%d")

        # Montar dados da tabela
        dados = [["Cliente", "Plano", "Emissão", "Vencimento",
                  "Auto-Renov.", "ID", "Status"]]

        for reg in historico:
            plano = reg.get("plano", "")
            base_expiry = reg.get("expira", "")
            lid = reg.get("id", "")
            is_vitalicio = plano == "vitalicio"
            fb = self._get_fb(lid)
            auto_renew = fb.get("auto_renew", False)

            if is_vitalicio:
                expiry_display = "Permanente"
            else:
                expiry_display = _calcular_expiry_efetivo(
                    base_expiry, plano, auto_renew, fb.get("expiry_override")
                )

            status = reg.get("status", "ativa")
            if status != "revogada" and not is_vitalicio and expiry_display < hoje_str:
                status = "expirada"

            dados.append([
                reg.get("cliente", ""),
                "Vitalício" if is_vitalicio else plano.capitalize(),
                reg.get("emissao", ""),
                expiry_display,
                "Sim" if auto_renew else "Não",
                lid,
                status.upper(),
            ])

        # Gerar PDF
        doc = SimpleDocTemplate(
            caminho, pagesize=landscape(A4),
            leftMargin=15*mm, rightMargin=15*mm,
            topMargin=15*mm, bottomMargin=15*mm,
        )

        styles = getSampleStyleSheet()
        titulo_style = ParagraphStyle(
            "TituloPDF", parent=styles["Title"],
            fontSize=16, spaceAfter=4,
        )
        sub_style = ParagraphStyle(
            "SubPDF", parent=styles["Normal"],
            fontSize=9, textColor=colors.grey, spaceAfter=12,
        )

        elements = [
            Paragraph("Gerenciamento de Clientes — SEAGBH", titulo_style),
            Paragraph(f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}", sub_style),
        ]

        col_widths = [140, 70, 70, 75, 60, 90, 65]
        tabela = Table(dados, colWidths=col_widths, repeatRows=1)

        estilo = TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#F1F5F9")]),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ])

        # Colorir status por linha
        for i, row_data in enumerate(dados[1:], start=1):
            st = row_data[-1]
            if st == "REVOGADA":
                estilo.add("TEXTCOLOR", (6, i), (6, i), colors.HexColor("#DC2626"))
            elif st == "EXPIRADA":
                estilo.add("TEXTCOLOR", (6, i), (6, i), colors.HexColor("#D97706"))
            else:
                estilo.add("TEXTCOLOR", (6, i), (6, i), colors.HexColor("#059669"))

        tabela.setStyle(estilo)
        elements.append(tabela)

        doc.build(elements)

        _mostrar_toast(
            self,
            titulo="PDF Exportado",
            corpo=f"Relatório exportado para:\n{caminho}",
            cor_borda="#4ADE80",
        )

    # ══════════════════════════════════════════════════════════════════════
    #  Ações — Auto-Renovação / Silenciar Alertas / Editar Vencimento
    # ══════════════════════════════════════════════════════════════════════

    @_acao_administrativa
    def _on_toggle_auto_renew(self):
        reg = self._linha_selecionada()
        if not reg:
            QMessageBox.warning(self, "Aviso", "Selecione uma licença na tabela!")
            return
        if reg.get("plano") == "vitalicio":
            QMessageBox.information(self, "Info", "Planos vitalícios não precisam de renovação.")
            return

        lid = reg["id"]
        fb = self._get_fb(lid)
        current = fb.get("auto_renew", False)
        new_val = not current

        if not _firebase_put(f"licenses/{lid}/auto_renew", new_val):
            QMessageBox.critical(self, "Erro", "Falha ao conectar ao Firebase.")
            return

        if lid not in self._firebase_cache:
            self._firebase_cache[lid] = {}
        self._firebase_cache[lid]["auto_renew"] = new_val
        self._carregar_tabela()

        estado = "ativada" if new_val else "desativada"
        _mostrar_toast(
            self,
            titulo=f"Auto-Renovação {'Ativada' if new_val else 'Desativada'}",
            corpo=(
                f"Auto-renovação {estado} para \"{reg['cliente']}\".\n\n"
                + (
                    "A licença será renovada automaticamente a cada período."
                    if new_val
                    else "A licença expirará normalmente ao final do período."
                )
            ),
            cor_borda="#4ADE80" if new_val else "#FBBF24",
        )

    @_acao_administrativa
    def _on_mute_alerts(self):
        reg = self._linha_selecionada()
        if not reg:
            QMessageBox.warning(self, "Aviso", "Selecione uma licença na tabela!")
            return
        if reg.get("plano") == "vitalicio":
            QMessageBox.information(self, "Info", "Planos vitalícios não geram alertas.")
            return

        lid = reg["id"]
        fb = self._get_fb(lid)
        auto_renew = fb.get("auto_renew", False)
        base_expiry = reg.get("expira", "")
        eff_expiry = _calcular_expiry_efetivo(
            base_expiry, reg.get("plano", ""), auto_renew, fb.get("expiry_override")
        )

        if not _firebase_put(f"licenses/{lid}/alerts_muted_until", eff_expiry):
            QMessageBox.critical(self, "Erro", "Falha ao conectar ao Firebase.")
            return

        if lid not in self._firebase_cache:
            self._firebase_cache[lid] = {}
        self._firebase_cache[lid]["alerts_muted_until"] = eff_expiry

        _mostrar_toast(
            self,
            titulo="Alertas Silenciados",
            corpo=(
                f"Alertas de vencimento silenciados para \"{reg['cliente']}\"\n"
                f"até {eff_expiry}.\n\n"
                f"Após o vencimento, os alertas voltarão automaticamente."
            ),
            cor_borda="#94A3B8",
        )

    @_acao_administrativa
    def _on_edit_expiry(self):
        reg = self._linha_selecionada()
        if not reg:
            QMessageBox.warning(self, "Aviso", "Selecione uma licença na tabela!")
            return
        if reg.get("plano") == "vitalicio":
            QMessageBox.information(self, "Info", "Planos vitalícios não têm vencimento.")
            return

        lid = reg["id"]
        fb = self._get_fb(lid)

        dlg = QDialog(self)
        dlg.setWindowTitle("Editar Vencimento")
        dlg.setFixedSize(340, 200)
        dlg.setStyleSheet("""
            QDialog { background-color: #0F172A; color: #E2E8F0; }
            QLabel { background: transparent; color: #E2E8F0; font-size: 13px; }
            QDateEdit {
                background-color: #1E293B; color: #E2E8F0;
                border: 1px solid #334155; border-radius: 8px;
                padding: 10px; font-size: 13px;
            }
            QPushButton {
                background-color: #0369A1; color: white; border: none;
                border-radius: 8px; padding: 10px 20px; font-weight: 700;
            }
            QPushButton:hover { background-color: #0284C7; }
        """)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        lay.addWidget(QLabel(f"Cliente: {reg['cliente']}"))
        lay.addWidget(QLabel("Nova data de vencimento:"))

        date_edit = QDateEdit(dlg)
        date_edit.setCalendarPopup(True)
        date_edit.setDisplayFormat("yyyy-MM-dd")

        current = fb.get("expiry_override") or reg.get("expira", "")
        date_edit.setDate(QDate.fromString(current, "yyyy-MM-dd"))
        lay.addWidget(date_edit)

        btn = QPushButton("Salvar")
        btn.clicked.connect(dlg.accept)
        lay.addWidget(btn)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        _exigir_sessao_admin()

        new_date = date_edit.date().toString("yyyy-MM-dd")

        if not _firebase_put(f"licenses/{lid}/expiry_override", new_date):
            QMessageBox.critical(self, "Erro", "Falha ao conectar ao Firebase.")
            return

        self._atualizar_registro(reg, lambda: self._concluir_edicao_vencimento(reg, new_date), expira=new_date)

    def _concluir_edicao_vencimento(self, reg, new_date):
        lid = reg["id"]
        if lid not in self._firebase_cache:
            self._firebase_cache[lid] = {}
        self._firebase_cache[lid]["expiry_override"] = new_date
        self._carregar_tabela()

        _mostrar_toast(
            self,
            titulo="Vencimento Atualizado",
            corpo=f"Vencimento de \"{reg['cliente']}\" alterado para {new_date}.",
            cor_borda="#6BD1FF",
        )

    # ══════════════════════════════════════════════════════════════════════
    #  Publicar Atualização (Firebase /updates/latest)
    # ══════════════════════════════════════════════════════════════════════

    @_acao_administrativa
    def _on_publicar_atualizacao(self):
        """Publica metadados de nova versão no Firebase /updates/latest."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Publicar Atualização")
        dlg.setFixedSize(520, 400)
        dlg.setStyleSheet("""
            QDialog { background-color: #0F172A; color: #E2E8F0; }
            QLabel { background: transparent; color: #E2E8F0; font-size: 13px; }
            QLineEdit, QTextEdit {
                background-color: #1E293B; color: #E2E8F0;
                border: 1px solid #334155; border-radius: 8px;
                padding: 8px; font-size: 13px;
            }
            QCheckBox { color: #E2E8F0; font-size: 13px; spacing: 8px; }
            QCheckBox::indicator {
                width: 18px; height: 18px; border-radius: 4px;
                border: 1px solid #475569; background-color: #1E293B;
            }
            QCheckBox::indicator:checked {
                background-color: #0EA5E9; border-color: #0EA5E9;
            }
            QPushButton {
                background-color: #7C3AED; color: white; border: none;
                border-radius: 8px; padding: 10px 20px; font-weight: 700;
            }
            QPushButton:hover { background-color: #8B5CF6; }
        """)

        from PyQt6.QtWidgets import QTextEdit, QCheckBox

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)

        # NOVO: Validação e limite de tamanho para versão
        edit_versao = QLineEdit()
        edit_versao.setPlaceholderText("Ex: 2.1.0")
        edit_versao.setMaxLength(20)  # Limite de caracteres
        form.addRow("Versão:", edit_versao)

        # NOVO: Limite de tamanho para URL
        edit_url = QLineEdit()
        edit_url.setPlaceholderText("https://github.com/usuario/repo/releases/download/v2.1.0/SEAGBH.exe")
        edit_url.setMaxLength(500)  # Limite de caracteres
        form.addRow("URL do .exe:", edit_url)

        edit_changelog = QTextEdit()
        edit_changelog.setPlaceholderText("- Correção de bugs\n- Novo recurso X")
        edit_changelog.setFixedHeight(100)
        form.addRow("Changelog:", edit_changelog)

        # SHA256 com botão para calcular
        sha256_layout = QHBoxLayout()
        edit_sha256 = QLineEdit()
        edit_sha256.setPlaceholderText("a1b2c3d4...")
        edit_sha256.setReadOnly(True)  # Somente leitura (preenchido automaticamente)
        sha256_layout.addWidget(edit_sha256)
        
        btn_calc_sha256 = QPushButton("Calcular")
        btn_calc_sha256.setFixedWidth(100)
        sha256_layout.addWidget(btn_calc_sha256)
        form.addRow("SHA256:", sha256_layout)

        chk_obrigatoria = QCheckBox("Atualização obrigatória")
        form.addRow("", chk_obrigatoria)

        lay.addLayout(form)

        info_label = QLabel("")
        info_label.setWordWrap(True)
        lay.addWidget(info_label)

        btn_pub = QPushButton("Publicar no Firebase")
        lay.addWidget(btn_pub)

        def _calcular_sha():
            """Abre diálogo para selecionar arquivo .exe e calcula SHA256."""
            arquivo, _ = QFileDialog.getOpenFileName(
                dlg,
                "Selecione o arquivo SEAGBH.exe",
                "",
                "Executáveis (*.exe);;Todos os arquivos (*.*)"
            )
            if arquivo:
                sha256 = _calcular_sha256_arquivo(arquivo)
                if sha256:
                    edit_sha256.setText(sha256)
                    info_label.setText(f"SHA256 calculado: {sha256[:32]}...")
                    info_label.setStyleSheet("color: #86EFAC; background: transparent;")
                else:
                    info_label.setText("Erro ao calcular SHA256.")
                    info_label.setStyleSheet("color: #F87171; background: transparent;")
            else:
                info_label.setText("Nenhum arquivo selecionado.")
                info_label.setStyleSheet("color: #FCD34D; background: transparent;")

        def _publicar():
            versao = edit_versao.text().strip()
            url = edit_url.text().strip()
            changelog = edit_changelog.toPlainText().strip()
            sha256 = edit_sha256.text().strip()

            if not versao or not url or not sha256:
                info_label.setText("Versão, URL e SHA256 são obrigatórios.")
                info_label.setStyleSheet("color: #F87171; background: transparent;")
                return

            dados = {
                "version": versao,
                "url": url,
                "sha256": sha256.lower(),  # Armazenar em minúsculas
                "changelog": changelog or "Melhorias e correções.",
                "data": datetime.now().strftime("%d/%m/%Y"),
                "obrigatoria": chk_obrigatoria.isChecked(),
            }

            ok = _firebase_put("updates/latest", dados)
            if ok:
                dlg.accept()
                _mostrar_toast(
                    self,
                    titulo="Atualização Publicada",
                    corpo=(
                        f"Versão {versao} publicada com sucesso.\n"
                        f"Arquivo validado com SHA256."
                    ),
                    cor_borda="#A78BFA",
                )
            else:
                info_label.setText("Falha ao enviar para o Firebase.")
                info_label.setStyleSheet("color: #F87171; background: transparent;")

        btn_calc_sha256.clicked.connect(_calcular_sha)
        btn_pub.clicked.connect(lambda: self._executar_acao_autorizada(_publicar, dialogo=dlg))
        dlg.exec()


# ══════════════════════════════════════════════════════════════════════════════
#  Estilo
# ══════════════════════════════════════════════════════════════════════════════

_STYLE = """
    QWidget {
        background-color: #0F172A;
        color: #E2E8F0;
        font-family: "Segoe UI", sans-serif;
        font-size: 12px;
    }
    QLabel#titulo {
        font-size: 22px;
        font-weight: 800;
        color: #6BD1FF;
        background: transparent;
    }
    QLabel#subtitle {
        font-size: 14px;
        font-weight: 700;
        color: #94A3B8;
        background: transparent;
    }
    QLabel {
        background: transparent;
        font-size: 13px;
    }
    QLineEdit {
        background-color: #1E293B;
        color: #E2E8F0;
        border: 1px solid #334155;
        border-radius: 8px;
        padding: 10px 14px;
        font-size: 13px;
    }
    QLineEdit:focus {
        border-color: #6BD1FF;
    }
    QComboBox {
        background-color: #1E293B;
        color: #E2E8F0;
        border: 1px solid #334155;
        border-radius: 8px;
        padding: 10px 14px;
        font-size: 13px;
    }
    QComboBox::drop-down {
        border: none;
        width: 30px;
    }
    QComboBox QAbstractItemView {
        background-color: #1E293B;
        color: #E2E8F0;
        border: 1px solid #334155;
        selection-background-color: #0369A1;
    }
    QTableWidget {
        background-color: #1E293B;
        color: #E2E8F0;
        border: 1px solid #334155;
        border-radius: 8px;
        gridline-color: #334155;
        font-size: 12px;
        selection-background-color: #0D3B66;
        selection-color: #E2E8F0;
    }
    QTableWidget::item {
        padding: 6px 10px;
    }
    QHeaderView::section {
        background-color: #152238;
        color: #94A3B8;
        border: none;
        border-bottom: 1px solid #334155;
        padding: 8px 10px;
        font-weight: 700;
        font-size: 11px;
        text-transform: uppercase;
    }
    QPushButton {
        background-color: #1E293B;
        color: #E2E8F0;
        border: 1px solid #334155;
        border-radius: 8px;
        font-weight: 600;
        padding: 10px 20px;
        font-size: 13px;
    }
    QPushButton:hover {
        background-color: #334155;
    }
    QPushButton#btn-gerar {
        background-color: #0369A1;
        color: #FFFFFF;
        border: none;
        font-size: 15px;
        font-weight: 700;
        padding: 14px;
    }
    QPushButton#btn-gerar:hover {
        background-color: #0284C7;
    }
    QPushButton#btn-revogar {
        background-color: #DC2626;
        color: #FFFFFF;
        border: none;
        font-weight: 700;
        padding: 10px 24px;
    }
    QPushButton#btn-revogar:hover {
        background-color: #EF4444;
    }
    QPushButton#btn-reativar {
        background-color: #059669;
        color: #FFFFFF;
        border: none;
        font-weight: 700;
        padding: 10px 24px;
    }
    QPushButton#btn-reativar:hover {
        background-color: #10B981;
    }
    QPushButton#btn-auto {
        background-color: #7C3AED;
        color: #FFFFFF;
        border: none;
        font-weight: 700;
        padding: 10px 24px;
    }
    QPushButton#btn-auto:hover {
        background-color: #8B5CF6;
    }
    QPushButton#btn-mute {
        background-color: #64748B;
        color: #FFFFFF;
        border: none;
        font-weight: 700;
        padding: 10px 24px;
    }
    QPushButton#btn-mute:hover {
        background-color: #94A3B8;
    }
    QPushButton#btn-edit-date {
        background-color: #0891B2;
        color: #FFFFFF;
        border: none;
        font-weight: 700;
        padding: 10px 24px;
    }
    QPushButton#btn-edit-date:hover {
        background-color: #06B6D4;
    }
    QPushButton#btn-excluir {
        background-color: #7F1D1D;
        color: #FFFFFF;
        border: none;
        font-weight: 700;
        padding: 10px 24px;
    }
    QPushButton#btn-excluir:hover {
        background-color: #991B1B;
    }
    QPushButton#btn-exportar {
        background-color: #15803D;
        color: #FFFFFF;
        border: none;
        font-weight: 700;
        padding: 10px 24px;
    }
    QPushButton#btn-exportar:hover {
        background-color: #16A34A;
    }
    QLineEdit#search-box {
        background-color: #1E293B;
        color: #E2E8F0;
        border: 1px solid #334155;
        border-radius: 8px;
        padding: 8px 14px;
        font-size: 13px;
    }
    QLineEdit#search-box:focus {
        border-color: #6BD1FF;
    }
    QPushButton#btn-restaurar {
        background-color: #1D4ED8;
        color: #FFFFFF;
        border: none;
        font-weight: 700;
        padding: 10px 24px;
    }
    QPushButton#btn-restaurar:hover {
        background-color: #2563EB;
    }
    QPushButton#btn-publicar {
        background-color: #7C3AED;
        color: #FFFFFF;
        border: none;
        font-weight: 700;
        padding: 10px 24px;
    }
    QPushButton#btn-publicar:hover {
        background-color: #8B5CF6;
    }
"""


def _main() -> int:
    carregar_configuracao(Path(__file__).resolve().parents[1] / ".env")
    app = QApplication(sys.argv)
    app.setStyleSheet(_STYLE)

    icon_path = os.path.join(os.path.dirname(__file__), "..", "src", "seaghb_icon.ico")
    app.setWindowIcon(QIcon(icon_path))

    try:
        if not _firebase_api_key():
            _LOGGER.warning("Firebase Auth: login indisponível | Código: %s", FalhaAuth.API_KEY_AUSENTE)
            QMessageBox.critical(
                None,
                "Configuração ausente",
                "Variável de ambiente SEAGBH_FIREBASE_API_KEY não configurada.\n\n"
                "Defina-a no arquivo .env da raiz do projeto ou no sistema antes\n"
                "de executar o Gerenciamento de Clientes.",
            )
            return 1

        if not configuracao_admin_valida():
            _LOGGER.warning("Firebase Auth: login indisponível | Código: %s", FalhaAuth.ADMIN_UID_INVALIDA)
            QMessageBox.critical(None, "Configuração administrativa", _MENSAGEM_ADMIN_UID)
            return 1

        login = LoginDialog()
        if login.exec() != QDialog.DialogCode.Accepted or not sessao_administrativa_ativa():
            return 0

        w = GerenciamentoClientes()
        w.show()
        return app.exec()
    except _SessaoAdminEncerrada:
        QMessageBox.warning(None, "Sessão encerrada", "Entre novamente no Gerenciamento de Clientes.")
        return 1
    finally:
        _logout()


if __name__ == "__main__":
    sys.exit(_main())
