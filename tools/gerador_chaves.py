"""
Gerenciamento de Clientes — SEAGBH
Ferramenta exclusiva do desenvolvedor.

Executar:  python tools/gerador_chaves.py
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import json
import hashlib
from datetime import datetime, timedelta
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

from core.licenca import gerar_chave, validar_chave, PLANOS, FIREBASE_URL, DIAS_ALERTA

_HISTORICO_PATH = os.path.join(os.path.dirname(__file__), "historico_chaves.json")


# ══════════════════════════════════════════════════════════════════════════════
#  Autenticação Firebase (email/senha) — sessão apenas em memória
# ══════════════════════════════════════════════════════════════════════════════

def _load_env_file() -> None:
    """Carrega variáveis do .env da raiz (sem sobrescrever env já definido)."""
    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
    )
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for linha in f:
                linha = linha.strip()
                if not linha or linha.startswith("#") or "=" not in linha:
                    continue
                key, value = linha.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass  # Sem .env o fluxo segue (API key pode vir do ambiente do SO)


_load_env_file()

# API key pública do Firebase (não é segredo), carregada do ambiente.
# NUNCA colocar a chave diretamente no código-fonte.
_FIREBASE_API_KEY = os.environ.get("SEAGBH_FIREBASE_API_KEY", "")

_FIREBASE_AUTH_URL = (
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
)
_FIREBASE_TOKEN_URL = "https://securetoken.googleapis.com/v1/token"

# Sessão de autenticação — SOMENTE em memória. NUNCA persistir em disco.
_AUTH_SESSION: dict = {
    "id_token": None,
    "refresh_token": None,
    "uid": None,
    "expira_em": 0.0,  # timestamp unix (time.time())
}


def _login_firebase(email: str, senha: str) -> bool:
    """Autentica no Firebase Authentication (REST) e preenche a sessão.

    A senha é usada apenas na requisição HTTP e descartada em seguida.
    Nunca é logada, salva em arquivo nem incluída em histórico.
    """
    import logging
    logger = logging.getLogger(__name__)

    if not _FIREBASE_API_KEY:
        logger.error("SEAGBH_FIREBASE_API_KEY não configurada.")
        return False

    body = json.dumps({
        "email": email,
        "password": senha,
        "returnSecureToken": True,
    }).encode("utf-8")

    try:
        req = Request(
            f"{_FIREBASE_AUTH_URL}?key={_FIREBASE_API_KEY}",
            data=body,
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urlopen(req, timeout=20) as resp:
            dados = json.loads(resp.read().decode())

        id_token = dados.get("idToken", "")
        if not id_token:
            logger.error("Resposta do login sem idToken.")
            return False

        _AUTH_SESSION["id_token"] = id_token
        _AUTH_SESSION["refresh_token"] = dados.get("refreshToken", "")
        _AUTH_SESSION["uid"] = dados.get("localId", "")
        expires_in = int(dados.get("expiresIn", "3600") or 3600)
        _AUTH_SESSION["expira_em"] = time.time() + expires_in
        return True
    except HTTPError as e:
        # 400 = credenciais inválidas. Não loga corpo da resposta (pode conter dados sensíveis).
        logger.warning(f"Login recusado pelo Firebase (HTTP {e.code}).")
        return False
    except (URLError, OSError, json.JSONDecodeError, ValueError) as e:
        logger.error(f"Falha de rede ao autenticar no Firebase: {e}")
        return False
    except Exception as e:
        logger.error(f"Erro inesperado no login Firebase: {e}")
        return False


def _refresh_token() -> bool:
    """Renova o id_token usando o refresh_token (mantidos em memória)."""
    import logging
    logger = logging.getLogger(__name__)

    if not _AUTH_SESSION["refresh_token"] or not _FIREBASE_API_KEY:
        return False

    body = urlencode({
        "grant_type": "refresh_token",
        "refresh_token": _AUTH_SESSION["refresh_token"],
    }).encode("utf-8")

    try:
        req = Request(
            f"{_FIREBASE_TOKEN_URL}?key={_FIREBASE_API_KEY}",
            data=body,
            method="POST",
        )
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urlopen(req, timeout=20) as resp:
            dados = json.loads(resp.read().decode())

        novo_token = dados.get("id_token") or dados.get("idToken")
        if not novo_token:
            return False

        _AUTH_SESSION["id_token"] = novo_token
        if dados.get("refresh_token"):
            _AUTH_SESSION["refresh_token"] = dados["refresh_token"]
        expires_in = int(dados.get("expires_in", "3600") or 3600)
        _AUTH_SESSION["expira_em"] = time.time() + expires_in
        return True
    except HTTPError as e:
        logger.warning(f"Falha ao renovar token (HTTP {e.code}). Sessão invalidada.")
        _logout()
        return False
    except (URLError, OSError, json.JSONDecodeError, ValueError) as e:
        logger.error(f"Falha de rede ao renovar token: {e}")
        return False
    except Exception as e:
        logger.error(f"Erro inesperado ao renovar token: {e}")
        return False



def _get_id_token() -> str | None:
    """Retorna id_token válido, renovando automaticamente ~60s antes do vencimento."""
    if not _AUTH_SESSION["id_token"]:
        return None
    if time.time() >= _AUTH_SESSION["expira_em"] - 60:
        if not _refresh_token():
            return None
    return _AUTH_SESSION["id_token"]


def _adicionar_autorizacao(req) -> bool:
    """Adiciona 'Authorization: Bearer <id_token>'. Retorna False se não houver sessão."""
    token = _get_id_token()
    if not token:
        return False
    req.add_header("Authorization", f"Bearer {token}")
    return True


def _logout():
    """Descarta a sessão de autenticação. O token nunca é persistido."""
    _AUTH_SESSION["id_token"] = None
    _AUTH_SESSION["refresh_token"] = None
    _AUTH_SESSION["uid"] = None
    _AUTH_SESSION["expira_em"] = 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  Persistência do histórico
# ══════════════════════════════════════════════════════════════════════════════

def _carregar_historico() -> list[dict]:
    try:
        with open(_HISTORICO_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _salvar_historico(historico: list[dict]):
    """Salva histórico com validação de permissões."""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Validar permissões antes de tentar gravar
        dir_path = os.path.dirname(_HISTORICO_PATH)
        if not os.access(dir_path, os.W_OK):
            raise PermissionError(f"Sem permissão de escrita em {dir_path}")
        
        with open(_HISTORICO_PATH, "w", encoding="utf-8") as f:
            json.dump(historico, f, ensure_ascii=False, indent=2)
    except (OSError, PermissionError) as e:
        logger.error(f"Falha ao salvar histórico: {e}")
        raise  # Permitir que o chamador saiba que falhou


_ARQUIVO_MORTO_PATH = os.path.join(os.path.dirname(__file__), "historico_excluidos.json")


def _carregar_arquivo_morto() -> list[dict]:
    try:
        with open(_ARQUIVO_MORTO_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _salvar_arquivo_morto(morto: list[dict]):
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


def _firebase_put(path: str, data) -> bool:
    """Envia dados para Firebase com validação de tamanho e logging."""
    import socket
    import logging
    logger = logging.getLogger(__name__)
    
    # Validar tamanho dos dados (Firebase tem limites)
    data_str = json.dumps(data)
    if len(data_str) > 1000000:  # 1MB limite
        logger.error(f"Dados muito grandes para Firebase: {len(data_str)} bytes")
        return False
    
    if "SEU-PROJETO" in FIREBASE_URL:
        return True
    try:
        token = _get_id_token()
        if not token:
            logger.error("Sessão de autenticação expirada ou inválida. Faça login novamente.")
            return False
        base_url = f"{FIREBASE_URL}/{path}.json"
        sep = "&" if "?" in base_url else "?"
        url = f"{base_url}{sep}{urlencode({'auth': token})}"
        payload = data_str.encode()
        req = Request(url, data=payload, method="PUT")
        req.add_header("Content-Type", "application/json")
        with urlopen(req, timeout=15) as resp:  # Aumentado para 15s
            return resp.status == 200
    except HTTPError as e:
        logger.error(f"HTTP {e.code} ao enviar para Firebase: {path}")
        if e.code in (401, 403):
            _logout()
        return False
    except socket.timeout:
        logger.warning(f"Timeout ao enviar para Firebase: {path}")
        return False
    except URLError as e:
        logger.error(f"Erro de rede ao enviar para Firebase: {path} - {e}")
        return False
    except Exception as e:
        logger.error(f"Erro ao enviar para Firebase {path}: {e}")
        return False


def _firebase_get(path: str):
    """Busca dados do Firebase com tratamento diferenciado de erros."""
    import socket
    import logging
    logger = logging.getLogger(__name__)
    
    if "SEU-PROJETO" in FIREBASE_URL:
        return None
    try:
        token = _get_id_token()
        if not token:
            logger.error("Sessão de autenticação expirada ou inválida. Faça login novamente.")
            return None
        base_url = f"{FIREBASE_URL}/{path}.json"
        sep = "&" if "?" in base_url else "?"
        url = f"{base_url}{sep}{urlencode({'auth': token})}"
        req = Request(url, method="GET")
        req.add_header("Accept", "application/json")
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        logger.error(f"HTTP {e.code} ao acessar Firebase: {path}")
        if e.code in (401, 403):
            _logout()
        return None
    except socket.timeout:
        logger.warning(f"Timeout ao acessar Firebase: {path}")
        return None
    except URLError as e:
        logger.error(f"Erro de rede ao acessar Firebase: {path} - {e}")
        return None
    except json.JSONDecodeError:
        logger.error(f"Resposta JSON inválida do Firebase: {path}")
        return None
    except Exception as e:
        logger.error(f"Erro desconhecido ao acessar Firebase {path}: {e}")
        return None


def _firebase_delete(path: str) -> bool:
    """Deleta dados do Firebase com logging diferenciado."""
    import socket
    import logging
    logger = logging.getLogger(__name__)
    
    if "SEU-PROJETO" in FIREBASE_URL:
        return True
    try:
        token = _get_id_token()
        if not token:
            logger.error("Sessão de autenticação expirada ou inválida. Faça login novamente.")
            return False
        base_url = f"{FIREBASE_URL}/{path}.json"
        sep = "&" if "?" in base_url else "?"
        url = f"{base_url}{sep}{urlencode({'auth': token})}"
        req = Request(url, method="DELETE")
        with urlopen(req, timeout=15) as resp:  # Aumentado para 15s
            return resp.status == 200
    except HTTPError as e:
        logger.error(f"HTTP {e.code} ao deletar do Firebase: {path}")
        if e.code in (401, 403):
            _logout()
        return False
    except socket.timeout:
        logger.warning(f"Timeout ao deletar do Firebase: {path}")
        return False
    except URLError as e:
        logger.error(f"Erro de rede ao deletar do Firebase: {path} - {e}")
        return False
    except Exception as e:
        logger.error(f"Erro ao deletar do Firebase {path}: {e}")
        return False


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

class _LoginWorker(QThread):
    """Executa o login Firebase em thread separada (não congela a interface)."""

    concluido = pyqtSignal(bool, str)  # sucesso, mensagem de erro (vazia se ok)

    def __init__(self, email: str, senha: str, parent=None):
        super().__init__(parent)
        self._email = email
        self._senha = senha

    def run(self):
        try:
            ok = _login_firebase(self._email, self._senha)
        except Exception:
            ok = False
        self._senha = ""  # descartar senha da memória da thread
        if ok:
            self.concluido.emit(True, "")
        else:
            self.concluido.emit(
                False,
                "Falha na autenticação.\n"
                "Verifique e-mail/senha e sua conexão com a internet.",
            )


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

        titulo = QLabel("Autenticação Administrativa")
        titulo.setObjectName("titulo")
        lay.addWidget(titulo)

        sub = QLabel(
            "Acesso exclusivo do desenvolvedor.\n"
            "A senha é usada apenas nesta sessão e nunca é armazenada."
        )
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
        if self._worker is not None and self._worker.isRunning():
            return
        email = self.entry_email.text().strip()
        senha = self.entry_senha.text()
        if not email or not senha:
            self.lbl_erro.setText("Informe e-mail e senha.")
            return
        self.lbl_erro.setText("")
        self._set_autenticando(True)
        self._worker = _LoginWorker(email, senha, parent=self)
        self._worker.concluido.connect(self._on_login_concluido)
        self._worker.start()

    def _on_login_concluido(self, ok: bool, msg: str):
        self.entry_senha.clear()  # descartar a senha digitada
        self._set_autenticando(False)
        self._worker = None
        if ok:
            self.accept()
        else:
            self.lbl_erro.setText(msg)

    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(3000)
        event.accept()


# ══════════════════════════════════════════════════════════════════════════════
#  Janela principal
# ══════════════════════════════════════════════════════════════════════════════

class GerenciamentoClientes(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gerenciamento de Clientes — SEAGBH")
        self.setMinimumSize(1020, 750)
        self._firebase_cache: dict[str, dict] = {}
        self._build_ui()
        self._carregar_firebase_dados()
        self._carregar_tabela()

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

    def _carregar_firebase_dados(self):
        """Carrega dados de auto_renew / alertas para todas as licenças."""
        historico = _carregar_historico()
        for reg in historico:
            lid = reg.get("id", "")
            if not lid:
                continue
            data = _firebase_get(f"licenses/{lid}")
            self._firebase_cache[lid] = data if isinstance(data, dict) else {}

    def _get_fb(self, license_id: str) -> dict:
        return self._firebase_cache.get(license_id, {})

    # ══════════════════════════════════════════════════════════════════════
    #  Tabela
    # ══════════════════════════════════════════════════════════════════════

    def _carregar_tabela(self):
        historico = _carregar_historico()
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

    def _filtrar_tabela(self):
        """Recarrega a tabela aplicando o filtro de busca."""
        self._carregar_tabela()

    def _linha_selecionada(self) -> dict | None:
        rows = self.tabela.selectionModel().selectedRows()
        if not rows:
            return None
        idx = rows[0].row()
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

        historico = _carregar_historico()
        historico.append({
            "id": payload["id"],
            "cliente": payload["c"],
            "plano": payload["p"],
            "emissao": payload["i"],
            "expira": payload["e"],
            "chave": chave,
            "status": "ativa",
        })
        _salvar_historico(historico)

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

        # ── Armazenar timestamp de revogação (grace period de 24h) ────────────
        revoked_at = datetime.now().isoformat()
        
        if not _firebase_put(f"licenses/{reg['id']}/revoked_at", revoked_at):
            QMessageBox.critical(self, "Erro", "Falha ao conectar ao Firebase.")
            return

        historico = _carregar_historico()
        for item in historico:
            if item["id"] == reg["id"]:
                item["status"] = "revogada"
                break
        _salvar_historico(historico)
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

        historico = _carregar_historico()
        for item in historico:
            if item["id"] == reg["id"]:
                item["status"] = "ativa"
                break
        _salvar_historico(historico)
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

        lid = reg["id"]

        # ── Sincronizar com Firebase: remover a licença ─────────────────────
        # Deletar do Firebase tanto a licença quanto sua revogação
        _firebase_delete(f"licenses/{lid}")
        _firebase_delete(f"revoked/{lid}")
        # Remover do cache local
        if lid in self._firebase_cache:
            del self._firebase_cache[lid]

        # Mover para arquivo morto
        morto = _carregar_arquivo_morto()
        morto.append(reg)
        _salvar_arquivo_morto(morto)

        # Remover do histórico ativo
        historico = _carregar_historico()
        historico = [h for h in historico if h.get("id") != lid]
        _salvar_historico(historico)
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
        morto = _carregar_arquivo_morto()
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
            historico = _carregar_historico()
            for h in historico:
                if h.get("id") == lid:
                    QMessageBox.information(dlg, "Info", "Esta licença já existe no histórico.")
                    return

            # Restaurar
            historico.append(registro)
            _salvar_historico(historico)
            _salvar_arquivo_morto(novo_morto)

            # ── Sincronizar com Firebase: recriar a licença ─────────────────
            # Se a licença foi deletada do Firebase, recriá-la
            fb_data = _firebase_get(f"licenses/{lid}")
            if fb_data is None:
                # Licença foi deletada — recriar com dados básicos
                default_data = {
                    "auto_renew": False,
                    "alerts_muted_until": None,
                    "expiry_override": None
                }
                _firebase_put(f"licenses/{lid}", default_data)
                self._firebase_cache[lid] = default_data
            else:
                self._firebase_cache[lid] = fb_data if isinstance(fb_data, dict) else {}

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

        btn.clicked.connect(_restaurar)
        dlg.exec()

    def _on_exportar_pdf(self):
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

        caminho, _ = QFileDialog.getSaveFileName(
            self, "Exportar Licenças", "licencas_seagbh.pdf",
            "PDF (*.pdf)"
        )
        if not caminho:
            return

        historico = _carregar_historico()
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

        new_date = date_edit.date().toString("yyyy-MM-dd")

        if not _firebase_put(f"licenses/{lid}/expiry_override", new_date):
            QMessageBox.critical(self, "Erro", "Falha ao conectar ao Firebase.")
            return

        if lid not in self._firebase_cache:
            self._firebase_cache[lid] = {}
        self._firebase_cache[lid]["expiry_override"] = new_date

        # Atualizar histórico local
        historico = _carregar_historico()
        for item in historico:
            if item["id"] == lid:
                item["expira"] = new_date
                break
        _salvar_historico(historico)
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
        btn_pub.clicked.connect(_publicar)
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


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(_STYLE)

    icon_path = os.path.join(os.path.dirname(__file__), "..", "src", "seaghb_icon.ico")
    app.setWindowIcon(QIcon(icon_path))

    # Garante que a API key do Firebase esteja disponível (via .env ou ambiente do SO).
    _load_env_file()
    if not os.environ.get("SEAGBH_FIREBASE_API_KEY", ""):
        QMessageBox.critical(
            None,
            "Configuração ausente",
            "Variável de ambiente SEAGBH_FIREBASE_API_KEY não configurada.\n\n"
            "Defina-a no arquivo .env da raiz do projeto ou no sistema antes\n"
            "de executar o Gerenciamento de Clientes.",
        )
        sys.exit(1)

    # Autenticação administrativa ANTES de abrir a janela principal.
    login = LoginDialog()
    if login.exec() != QDialog.DialogCode.Accepted:
        sys.exit(0)

    w = GerenciamentoClientes()
    w.show()
    sys.exit(app.exec())
