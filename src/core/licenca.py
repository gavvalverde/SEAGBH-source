"""
Sistema de licenciamento híbrido — offline + Firebase.

Chaves assinadas com HMAC-SHA256.
Verificação via Firebase Realtime Database a cada INTERVALO_VERIFICACAO dias.
Auto-renovação e alertas de vencimento controlados pelo Firebase.
"""

import hmac
import hashlib
import base64
import json
import os
import sys
import uuid
from datetime import datetime, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURAÇÃO
# ══════════════════════════════════════════════════════════════════════════════

# URL do Firebase Realtime Database (REST API — NÃO a URL do console)
FIREBASE_URL = "https://seagbh-licencas-default-rtdb.firebaseio.com"

# Intervalo entre verificações online (dias)
INTERVALO_VERIFICACAO = 1

# Chave secreta para assinatura (fragmentada para dificultar leitura)
_K1 = b"\x53\x45\x41\x47\x42\x48\x2d\x4c\x49\x43\x2d"
_K2 = b"\x53\x49\x47\x4e\x2d\x4b\x45\x59\x2d\x32\x30"
_K3 = b"\x32\x36\x2d\x47\x56\x2d\x50\x52\x45\x4d\x49"
_SECRET = _K1 + _K2 + _K3

# Planos disponíveis (nome → dias)  |  0 = vitalício (nunca expira)
PLANOS = {
    "mensal": 30,
    "trimestral": 90,
    "semestral": 180,
    "anual": 365,
    "vitalicio": 0,
}

# Dias de antecedência para alertas de vencimento por plano
DIAS_ALERTA = {
    "mensal": 7,
    "trimestral": 15,
    "semestral": 30,
    "anual": 30,
    "vitalicio": 0,
}


# ══════════════════════════════════════════════════════════════════════════════
#  INTERNOS
# ══════════════════════════════════════════════════════════════════════════════

def _get_app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return (
        os.path.dirname(os.path.abspath(__file__))
        .replace("\\core", "")
        .replace("/core", "")
    )


_LICENCA_PATH = os.path.join(_get_app_dir(), ".licenca.dat")


def _assinar(payload_json: str) -> str:
    """Retorna os primeiros 40 hex do HMAC-SHA256 do payload."""
    return hmac.new(_SECRET, payload_json.encode(), hashlib.sha256).hexdigest()[:40]


# ══════════════════════════════════════════════════════════════════════════════
#  GERAÇÃO DE CHAVES  (usada pelo gerador, NÃO pelo app do cliente)
# ══════════════════════════════════════════════════════════════════════════════

def gerar_chave(cliente: str, plano: str) -> str:
    """Gera uma chave de licença assinada."""
    if plano not in PLANOS:
        raise ValueError(f"Plano inválido: {plano}")

    agora = datetime.now()
    dias = PLANOS[plano]

    payload = {
        "id": uuid.uuid4().hex[:12],
        "c": cliente,
        "p": plano,
        "i": agora.strftime("%Y-%m-%d"),
        "e": "9999-12-31" if dias == 0 else (agora + timedelta(days=dias)).strftime("%Y-%m-%d"),
    }

    payload_json = json.dumps(payload, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode()
    assinatura = _assinar(payload_json)

    return f"{payload_b64}.{assinatura}"


# ══════════════════════════════════════════════════════════════════════════════
#  VALIDAÇÃO DE CHAVES
# ══════════════════════════════════════════════════════════════════════════════

def validar_chave(chave: str) -> dict | None:
    """Valida assinatura e retorna payload, ou None se inválida."""
    try:
        partes = chave.strip().split(".")
        if len(partes) != 2:
            return None

        payload_b64, assinatura = partes
        payload_json = base64.urlsafe_b64decode(payload_b64).decode()

        if not hmac.compare_digest(assinatura, _assinar(payload_json)):
            return None

        payload = json.loads(payload_json)

        for campo in ("id", "c", "p", "i", "e"):
            if campo not in payload:
                return None

        return payload
    except Exception:
        return None


def _expiry_efetivo(payload: dict, dados_local: dict | None = None) -> str:
    """
    Calcula data de expiração efetiva considerando auto-renovação.
    Se auto_renew está ativo, estende a data base repetidamente pelo período
    do plano até chegar numa data futura.
    Retorna string "YYYY-MM-DD".
    """
    base_expiry = payload["e"]
    plano = payload.get("p", "")
    dias_plano = PLANOS.get(plano, 0)

    if dias_plano == 0:  # vitalício
        return base_expiry

    # Verificar se auto_renew está ativo (via dados locais do Firebase)
    fb = {}
    if dados_local:
        fb = dados_local.get("firebase", {}) or {}

    if not fb.get("auto_renew"):
        # Se tem expiry_override do admin, usar esse
        if fb.get("expiry_override"):
            return fb["expiry_override"]
        return base_expiry

    # Auto-renovação: estender desde a data base até o futuro
    expiry = datetime.strptime(base_expiry, "%Y-%m-%d")
    hoje = datetime.now()
    while expiry < hoje:
        expiry += timedelta(days=dias_plano)
    return expiry.strftime("%Y-%m-%d")


def chave_expirada(payload: dict, dados_local: dict | None = None) -> bool:
    """Retorna True se a chave já passou da data de expiração efetiva."""
    if payload.get("p") == "vitalicio":
        return False
    try:
        expiry_str = _expiry_efetivo(payload, dados_local)
        expira = datetime.strptime(expiry_str, "%Y-%m-%d")
        return datetime.now() > expira + timedelta(days=1)
    except (ValueError, KeyError):
        return True


def dias_restantes(payload: dict, dados_local: dict | None = None) -> int:
    """Retorna dias restantes na licença. -1 = vitalício."""
    if payload.get("p") == "vitalicio":
        return -1
    try:
        expiry_str = _expiry_efetivo(payload, dados_local)
        expira = datetime.strptime(expiry_str, "%Y-%m-%d")
        return max(0, (expira - datetime.now()).days + 1)
    except (ValueError, KeyError):
        return 0


# ══════════════════════════════════════════════════════════════════════════════
#  ARMAZENAMENTO LOCAL
# ══════════════════════════════════════════════════════════════════════════════

def _salvar_dados_brutos(dados: dict) -> bool:
    """Salva dict no arquivo .licenca.dat com proteção HMAC e atomicidade."""
    import tempfile
    dados_json = json.dumps(dados)
    sig = hmac.new(_SECRET, dados_json.encode(), hashlib.sha256).hexdigest()
    conteudo = base64.b64encode(f"{dados_json}|{sig}".encode()).decode()
    try:
        # Usar arquivo temporário + rename para garantir atomicidade
        # (em caso de erro durante escrita, arquivo original não fica corrompido)
        fd, temp_path = tempfile.mkstemp(text=True, dir=os.path.dirname(_LICENCA_PATH))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(conteudo)
            # Rename é atômico no Windows (POSIX também)
            os.replace(temp_path, _LICENCA_PATH)
            return True
        except (OSError, IOError):
            # Limpeza do arquivo temporário em caso de erro
            try:
                os.unlink(temp_path)
            except (OSError, IOError):
                pass
            return False
    except OSError:
        return False


def salvar_licenca(chave: str) -> bool:
    """Salva a chave ativada + data da última verificação em disco."""
    dados = {
        "chave": chave,
        "ultima_verificacao": datetime.now().isoformat(),
        "firebase": {},
    }
    return _salvar_dados_brutos(dados)


def carregar_licenca() -> dict | None:
    """Carrega a licença salva. Retorna dict ou None se corrompida.
    Se corrompida, remove arquivo automaticamente.
    Cria backup automaticamente na primeira leitura bem-sucedida.
    """
    import logging
    import shutil
    logger = logging.getLogger(__name__)
    
    try:
        with open(_LICENCA_PATH, "r", encoding="utf-8") as f:
            conteudo = f.read()

        decoded = base64.b64decode(conteudo).decode()
        dados_json, sig = decoded.rsplit("|", 1)

        sig_esperada = hmac.new(
            _SECRET, dados_json.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, sig_esperada):
            logger.error("HMAC mismatch — arquivo .licenca.dat corrompido! Removendo...")
            remover_licenca()  # Limpar automaticamente
            return None

        # Criar backup automático se ainda não existe
        backup_path = _LICENCA_PATH + ".backup"
        if not os.path.exists(backup_path):
            try:
                shutil.copy2(_LICENCA_PATH, backup_path)
                logger.info(f"Backup automático criado: {backup_path}")
            except (OSError, IOError) as e:
                logger.warning(f"Não foi possível criar backup: {e}")

        return json.loads(dados_json)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.warning(f"Erro ao carregar licença: {type(e).__name__}: {e}")
        # Tentar remover arquivo corrompido
        try:
            os.remove(_LICENCA_PATH)
        except OSError:
            pass
        return None


def remover_licenca():
    """Remove o arquivo de licença."""
    try:
        os.remove(_LICENCA_PATH)
    except OSError:
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  VERIFICAÇÃO VIA FIREBASE
# ══════════════════════════════════════════════════════════════════════════════

def _firebase_get(path: str):
    """GET genérico no Firebase. Retorna o valor JSON ou None."""
    if "SEU-PROJETO" in FIREBASE_URL:
        return None
    try:
        url = f"{FIREBASE_URL}/{path}.json"
        req = Request(url, method="GET")
        req.add_header("Accept", "application/json")
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except (URLError, OSError, json.JSONDecodeError):
        return None


def _consultar_firebase(license_id: str) -> dict | None:
    """
    Consulta Firebase para revogação, auto-renovação e alertas.
    Retorna dict com todos os dados ou None se sem conexão.
    """
    if "SEU-PROJETO" in FIREBASE_URL:
        return {"revogada": False, "auto_renew": False,
                "alerts_muted_until": None, "expiry_override": None, "revoked_at": None}

    resultado = {}

    # Revogação com grace period de 24 horas
    license_data = _firebase_get(f"licenses/{license_id}")
    revoked_at = None
    if isinstance(license_data, dict):
        revoked_at = license_data.get("revoked_at")
    
    # Verificar se passou 24 horas desde a revogação
    if revoked_at:
        try:
            revoked_time = datetime.fromisoformat(revoked_at)
            if (datetime.now() - revoked_time).total_seconds() < 86400:  # 24h em segundos
                resultado["revogada"] = False  # Ainda em grace period
            else:
                resultado["revogada"] = True  # Passou 24h, bloqueia
        except ValueError:
            resultado["revogada"] = True  # Se data inválida, bloqueia
    else:
        resultado["revogada"] = False

    # Dados da licença (auto_renew, alerts_muted_until, expiry_override)
    if isinstance(license_data, dict):
        resultado["auto_renew"] = license_data.get("auto_renew", False)
        resultado["alerts_muted_until"] = license_data.get("alerts_muted_until")
        resultado["expiry_override"] = license_data.get("expiry_override")
        resultado["revoked_at"] = license_data.get("revoked_at")
    else:
        resultado["auto_renew"] = False
        resultado["alerts_muted_until"] = None
        resultado["expiry_override"] = None
        resultado["revoked_at"] = None

    return resultado


def precisa_verificacao_online() -> bool:
    """Retorna True se passaram INTERVALO_VERIFICACAO dias desde a última verificação."""
    dados = carregar_licenca()
    if not dados:
        return True
    try:
        ultima = datetime.fromisoformat(dados["ultima_verificacao"])
        return (datetime.now() - ultima).days >= INTERVALO_VERIFICACAO
    except (ValueError, KeyError):
        return True


# ══════════════════════════════════════════════════════════════════════════════
#  ALERTAS DE VENCIMENTO
# ══════════════════════════════════════════════════════════════════════════════

def info_alerta(payload: dict, dados_local: dict | None = None) -> dict | None:
    """
    Retorna info do alerta de vencimento, ou None se não precisa alertar.
    Retorno: {"dias_restantes": int, "data_vencimento": str, "plano": str}
    """
    plano = payload.get("p", "")
    if plano == "vitalicio":
        return None

    dias_antecedencia = DIAS_ALERTA.get(plano, 7)
    if dias_antecedencia == 0:
        return None

    dias = dias_restantes(payload, dados_local)
    if dias <= 0 or dias > dias_antecedencia:
        return None

    # Verificar se alertas estão silenciados
    fb = {}
    if dados_local:
        fb = dados_local.get("firebase", {}) or {}

    muted_until = fb.get("alerts_muted_until")
    if muted_until:
        try:
            muted_date = datetime.strptime(muted_until, "%Y-%m-%d")
            if datetime.now() <= muted_date + timedelta(days=1):
                return None  # Alertas silenciados até essa data
        except ValueError:
            pass

    expiry_str = _expiry_efetivo(payload, dados_local)

    return {
        "dias_restantes": dias,
        "data_vencimento": expiry_str,
        "plano": plano,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  VERIFICAÇÃO PRINCIPAL  (usada pelo app)
# ══════════════════════════════════════════════════════════════════════════════

def licenca_ativa() -> tuple[bool, str, dict | None]:
    """
    Verifica se existe uma licença válida.
    Retorna (valida, mensagem, payload).
    O payload pode conter '_alerta' com info de vencimento.
    """
    # 1. Carregar licença local
    dados = carregar_licenca()
    if not dados or "chave" not in dados:
        return False, "Nenhuma licença encontrada.\nAtive o sistema para continuar.", None

    # 2. Validar assinatura
    payload = validar_chave(dados["chave"])
    if payload is None:
        return False, "Licença inválida ou corrompida.", None

    # 3. Verificação online (a cada INTERVALO_VERIFICACAO dias)
    if precisa_verificacao_online():
        fb_data = _consultar_firebase(payload["id"])
        if fb_data is not None:
            # Revogação
            if fb_data["revogada"]:
                remover_licenca()
                return False, "Licença revogada.\nEntre em contato com o suporte.", None

            # Salvar dados do Firebase localmente
            dados["firebase"] = fb_data
            dados["ultima_verificacao"] = datetime.now().isoformat()
            _salvar_dados_brutos(dados)
        # Se None (sem internet) → continua com dados locais

    # 4. Verificar expiração (com auto-renovação)
    if chave_expirada(payload, dados):
        remover_licenca()
        return False, "Licença expirada.\nRenove sua assinatura para continuar.", None

    # 5. Montar retorno
    dias = dias_restantes(payload, dados)
    nome_plano = payload["p"].capitalize()
    cliente = payload["c"]

    # Anexar info de alerta ao payload para o app mostrar
    alerta = info_alerta(payload, dados)
    if alerta:
        payload["_alerta"] = alerta

    if dias == -1:
        return True, f"{cliente} — {nome_plano} (acesso permanente)", payload
    return True, f"{cliente} — {nome_plano} ({dias} dias restantes)", payload
