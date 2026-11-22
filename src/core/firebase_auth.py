"""Autenticação Firebase do Gerenciamento de Clientes, com sessão em memória."""

import json
import logging
import os
import shlex
import time
from dataclasses import dataclass
from enum import StrEnum
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


_LOGGER = logging.getLogger(__name__)
_API_KEY_ENV = "SEAGBH_FIREBASE_API_KEY"
_LOGIN_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
_TOKEN_URL = "https://securetoken.googleapis.com/v1/token"
_LIMITE_ERRO = 16 * 1024
_LIMITE_RESPOSTA = 64 * 1024
_VALIDADE_MAXIMA_TOKEN = 3600


class FalhaAuth(StrEnum):
    API_KEY_AUSENTE = "API_KEY_AUSENTE"
    API_KEY_INVALIDA = "API_KEY_INVALIDA"
    API_KEY_RESTRITA = "API_KEY_RESTRITA"
    PROJETO_INDISPONIVEL = "PROJETO_INDISPONIVEL"
    PROVEDOR_DESATIVADO = "PROVEDOR_DESATIVADO"
    CREDENCIAIS_INVALIDAS = "CREDENCIAIS_INVALIDAS"
    USUARIO_DESABILITADO = "USUARIO_DESABILITADO"
    LIMITE_TENTATIVAS = "LIMITE_TENTATIVAS"
    PROVEDOR_INDISPONIVEL = "PROVEDOR_INDISPONIVEL"
    TIMEOUT = "TIMEOUT"
    REDE_INDISPONIVEL = "REDE_INDISPONIVEL"
    SESSAO_INVALIDA = "SESSAO_INVALIDA"
    TOKEN_AUSENTE = "TOKEN_AUSENTE"
    RESPOSTA_INVALIDA = "RESPOSTA_INVALIDA"
    ERRO_HTTP_DESCONHECIDO = "ERRO_HTTP_DESCONHECIDO"


_CODIGOS_FIREBASE = {
    "INVALID_LOGIN_CREDENTIALS": FalhaAuth.CREDENCIAIS_INVALIDAS,
    "INVALID_PASSWORD": FalhaAuth.CREDENCIAIS_INVALIDAS,
    "EMAIL_NOT_FOUND": FalhaAuth.CREDENCIAIS_INVALIDAS,
    "INVALID_EMAIL": FalhaAuth.CREDENCIAIS_INVALIDAS,
    "USER_DISABLED": FalhaAuth.USUARIO_DESABILITADO,
    "TOO_MANY_ATTEMPTS_TRY_LATER": FalhaAuth.LIMITE_TENTATIVAS,
    "OPERATION_NOT_ALLOWED": FalhaAuth.PROVEDOR_DESATIVADO,
    "API_KEY_INVALID": FalhaAuth.API_KEY_INVALIDA,
    "INVALID_API_KEY": FalhaAuth.API_KEY_INVALIDA,
    "API key not valid. Please pass a valid API key.": FalhaAuth.API_KEY_INVALIDA,
    "API_KEY_SERVICE_BLOCKED": FalhaAuth.API_KEY_RESTRITA,
    "API_KEY_HTTP_REFERRER_BLOCKED": FalhaAuth.API_KEY_RESTRITA,
    "API_KEY_IP_ADDRESS_BLOCKED": FalhaAuth.API_KEY_RESTRITA,
    "API_KEY_ANDROID_APP_BLOCKED": FalhaAuth.API_KEY_RESTRITA,
    "API_KEY_IOS_APP_BLOCKED": FalhaAuth.API_KEY_RESTRITA,
    "APP_NOT_AUTHORIZED": FalhaAuth.API_KEY_RESTRITA,
    "CONFIGURATION_NOT_FOUND": FalhaAuth.PROJETO_INDISPONIVEL,
    "PROJECT_NOT_FOUND": FalhaAuth.PROJETO_INDISPONIVEL,
    "PROJECT_NUMBER_MISMATCH": FalhaAuth.PROJETO_INDISPONIVEL,
    "SERVICE_DISABLED": FalhaAuth.PROJETO_INDISPONIVEL,
    "TOKEN_EXPIRED": FalhaAuth.SESSAO_INVALIDA,
    "INVALID_REFRESH_TOKEN": FalhaAuth.SESSAO_INVALIDA,
    "USER_NOT_FOUND": FalhaAuth.SESSAO_INVALIDA,
}


class _ErroAuth(Exception):
    def __init__(self, codigo: FalhaAuth, status: int | None = None):
        super().__init__(codigo.value)
        self.codigo = codigo
        self.status = status


@dataclass(frozen=True, repr=False)
class _SessaoFirebase:
    id_token: str
    refresh_token: str
    uid: str
    expira_em: float


_AUTH_SESSION: _SessaoFirebase | None = None


class _SemRedirecionamento(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_abrir_auth = build_opener(_SemRedirecionamento()).open


def carregar_api_key(env_path: Path) -> None:
    """Usa o .env como fallback quando a variável do processo está ausente."""
    if _API_KEY_ENV in os.environ:
        return

    try:
        with env_path.open(encoding="utf-8-sig") as arquivo:
            for linha in arquivo:
                nome, separador, valor = linha.strip().removeprefix("export ").partition("=")
                if separador and nome.strip() == _API_KEY_ENV:
                    partes = shlex.split(valor, comments=True)
                    if len(partes) > 1:
                        raise ValueError
                    os.environ.setdefault(_API_KEY_ENV, partes[0] if partes else "")
                    return
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        _LOGGER.warning("Firebase Auth: falha ao carregar a configuração | Código: ENV_INVALIDO")


def api_key_firebase() -> str:
    return os.environ.get(_API_KEY_ENV, "").strip()


def _ler_objeto_json(resposta, limite: int) -> dict:
    corpo = resposta.read(limite + 1)
    if len(corpo) > limite:
        raise _ErroAuth(FalhaAuth.RESPOSTA_INVALIDA)
    try:
        dados = json.loads(corpo.decode("utf-8"))
    except (ValueError, RecursionError):
        raise _ErroAuth(FalhaAuth.RESPOSTA_INVALIDA) from None
    if not isinstance(dados, dict):
        raise _ErroAuth(FalhaAuth.RESPOSTA_INVALIDA)
    return dados


def _codigo_conhecido(valor) -> FalhaAuth | None:
    if not isinstance(valor, str):
        return None
    return _CODIGOS_FIREBASE.get(valor.partition(":")[0].strip())


def _classificar_http(erro: HTTPError) -> FalhaAuth:
    try:
        with erro:
            if erro.code == 429:
                return FalhaAuth.LIMITE_TENTATIVAS
            if 500 <= erro.code <= 599:
                return FalhaAuth.PROVEDOR_INDISPONIVEL
            dados = _ler_objeto_json(erro, _LIMITE_ERRO).get("error")
    except (_ErroAuth, OSError, HTTPException, ValueError):
        return FalhaAuth.RESPOSTA_INVALIDA
    if not isinstance(dados, dict):
        return FalhaAuth.RESPOSTA_INVALIDA

    detalhes = dados.get("details")
    if isinstance(detalhes, list):
        for detalhe in detalhes:
            if (
                isinstance(detalhe, dict)
                and detalhe.get("@type") == "type.googleapis.com/google.rpc.ErrorInfo"
                and detalhe.get("domain") == "googleapis.com"
            ):
                codigo = _codigo_conhecido(detalhe.get("reason"))
                if codigo is not None:
                    return codigo

    codigo = _codigo_conhecido(dados.get("message"))
    if codigo is not None:
        return codigo

    erros = dados.get("errors")
    if isinstance(erros, list):
        for item in erros:
            if isinstance(item, dict):
                codigo = _codigo_conhecido(item.get("message"))
                if codigo is not None:
                    return codigo
    return FalhaAuth.ERRO_HTTP_DESCONHECIDO


def _post(url: str, corpo: bytes, content_type: str) -> dict:
    chave = api_key_firebase()
    if not chave:
        raise _ErroAuth(FalhaAuth.API_KEY_AUSENTE)

    try:
        query = urlencode({"key": chave})
    except UnicodeError:
        raise _ErroAuth(FalhaAuth.API_KEY_INVALIDA) from None

    req = Request(
        f"{url}?{query}", data=corpo, headers={"Content-Type": content_type}, method="POST"
    )
    try:
        with _abrir_auth(req, timeout=20) as resposta:
            dados = _ler_objeto_json(resposta, _LIMITE_RESPOSTA)
            if resposta.status != 200 or "error" in dados:
                raise _ErroAuth(FalhaAuth.RESPOSTA_INVALIDA, resposta.status)
            return dados
    except HTTPError as erro:
        raise _ErroAuth(_classificar_http(erro), erro.code) from None
    except TimeoutError:
        raise _ErroAuth(FalhaAuth.TIMEOUT) from None
    except URLError as erro:
        codigo = (
            FalhaAuth.TIMEOUT if isinstance(erro.reason, TimeoutError)
            else FalhaAuth.REDE_INDISPONIVEL
        )
        raise _ErroAuth(codigo) from None
    except (OSError, HTTPException):
        raise _ErroAuth(FalhaAuth.REDE_INDISPONIVEL) from None


def _texto_sessao(valor, falha: FalhaAuth) -> str:
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        raise _ErroAuth(falha)
    if not isinstance(valor, str) or not valor.isprintable():
        raise _ErroAuth(FalhaAuth.RESPOSTA_INVALIDA)
    return valor


def _expiracao(valor) -> float:
    if type(valor) is not int and not (
        isinstance(valor, str) and valor.isascii() and valor.isdecimal()
    ):
        raise _ErroAuth(FalhaAuth.RESPOSTA_INVALIDA)
    try:
        segundos = int(valor)
    except ValueError:
        raise _ErroAuth(FalhaAuth.RESPOSTA_INVALIDA) from None
    if not 0 < segundos <= _VALIDADE_MAXIMA_TOKEN:
        raise _ErroAuth(FalhaAuth.RESPOSTA_INVALIDA)
    return time.monotonic() + segundos


def _registrar_falha(operacao: str, erro: _ErroAuth) -> None:
    _LOGGER.warning(
        "Firebase Auth: %s | Código: %s | HTTP: %s",
        operacao, erro.codigo, erro.status if erro.status is not None else "-",
    )


def login_firebase(email: str, senha: str) -> FalhaAuth | None:
    """Autentica e publica a sessão validada. Retorna o código interno em caso de falha."""
    global _AUTH_SESSION
    logout_firebase()
    corpo = json.dumps({
        "email": email,
        "password": senha,
        "returnSecureToken": True,
    }).encode("utf-8")
    try:
        dados = _post(_LOGIN_URL, corpo, "application/json")
        _AUTH_SESSION = _SessaoFirebase(
            id_token=_texto_sessao(dados.get("idToken"), FalhaAuth.TOKEN_AUSENTE),
            refresh_token=_texto_sessao(dados.get("refreshToken"), FalhaAuth.RESPOSTA_INVALIDA),
            uid=_texto_sessao(dados.get("localId"), FalhaAuth.RESPOSTA_INVALIDA),
            expira_em=_expiracao(dados.get("expiresIn")),
        )
    except _ErroAuth as erro:
        _registrar_falha("login recusado", erro)
        return erro.codigo
    return None


def _refresh_token(sessao: _SessaoFirebase) -> bool:
    global _AUTH_SESSION
    corpo = urlencode({
        "grant_type": "refresh_token",
        "refresh_token": sessao.refresh_token,
    }).encode("utf-8")
    try:
        dados = _post(_TOKEN_URL, corpo, "application/x-www-form-urlencoded")
        if dados.get("user_id", sessao.uid) != sessao.uid:
            raise _ErroAuth(FalhaAuth.SESSAO_INVALIDA)
        id_token = dados.get("id_token") or dados.get("idToken")
        refresh_token = dados.get("refresh_token", sessao.refresh_token)
        _AUTH_SESSION = _SessaoFirebase(
            id_token=_texto_sessao(id_token, FalhaAuth.TOKEN_AUSENTE),
            refresh_token=_texto_sessao(refresh_token, FalhaAuth.RESPOSTA_INVALIDA),
            uid=sessao.uid,
            expira_em=_expiracao(dados.get("expires_in")),
        )
    except _ErroAuth as erro:
        logout_firebase()
        _registrar_falha("renovação recusada", erro)
        return False
    return True


def sessao_firebase_ativa() -> bool:
    sessao = _AUTH_SESSION
    return sessao is not None and time.monotonic() < sessao.expira_em


def token_firebase() -> str | None:
    sessao = _AUTH_SESSION
    if sessao is not None and time.monotonic() >= sessao.expira_em - 60:
        if not _refresh_token(sessao):
            return None
        sessao = _AUTH_SESSION
    return sessao.id_token if sessao is not None and time.monotonic() < sessao.expira_em else None


def logout_firebase() -> None:
    global _AUTH_SESSION
    _AUTH_SESSION = None
