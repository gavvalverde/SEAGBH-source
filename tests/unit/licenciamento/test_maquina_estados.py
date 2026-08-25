"""Testes da maquina de estados de licenca.

Cobre licenca_ativa() e _consultar_firebase() para todos os estados:
VALID, REVOKED_GRACE, REVOKED_EXPIRED, DELETED, OFFLINE.

Todos os testes usam mocks - nenhuma chamada real ao Firebase.
"""
import base64
import json
import socket

from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import src.core.licenca as lic


# Helpers

def _dados(chave, ultima_verificacao_iso=None):
    if ultima_verificacao_iso is None:
        ultima_verificacao_iso = datetime.now().isoformat()
    return {
        "chave": chave,
        "ultima_verificacao": ultima_verificacao_iso,
        "firebase": {},
    }


def _salvar(chave, ultima_verificacao_iso=None):
    lic._salvar_dados_brutos(_dados(chave, ultima_verificacao_iso))


def _fb(estado, **extras):
    result = {"estado": estado}
    result.update(extras)
    return result


def _chave_mensal_expirada():
    """Monta chave mensal com expiracao no passado (assinatura valida)."""
    payload = {
        "id": "000000000001",
        "c": "Cliente Expirado",
        "p": "mensal",
        "i": "2020-01-01",
        "e": "2020-01-20",
    }
    payload_json = json.dumps(payload, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode()
    assinatura = lic._assinar(payload_json)
    return f"{payload_b64}.{assinatura}"


# ── VALID ────────────────────────────────────────────────────────────────

class TestValid:
    def test_retorna_true(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_VALID)):
            valido, msg, payload = lic.licenca_ativa()
        assert valido is True
        assert payload is not None
        assert payload["p"] == "vitalicio"
        assert payload["c"] == "Cliente Teste"

    def test_mensagem_contem_cliente(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_VALID)):
            _, msg, _ = lic.licenca_ativa()
        assert "Cliente Teste" in msg

    def test_nao_chama_segundos_desde_verificacao(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_VALID)), \
             patch.object(lic, "_segundos_desde_verificacao",
                          wraps=lic._segundos_desde_verificacao) as spy:
            lic.licenca_ativa()
        spy.assert_not_called()

    def test_atualiza_ultima_verificacao(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia, "2020-01-01T00:00:00")
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_VALID)):
            lic.licenca_ativa()
        dados = lic.carregar_licenca()
        assert dados["ultima_verificacao"] != "2020-01-01T00:00:00"


# ── REVOKED_GRACE ────────────────────────────────────────────────────────

class TestRevokedGrace:
    def _fb_grace(self):
        revoked = datetime.now() - timedelta(hours=23, minutes=30)
        return _fb(
            lic.ESTADO_REVOKED_GRACE,
            revoked_at=revoked.isoformat(),
            prazo_iso=(revoked + timedelta(hours=24)).isoformat(),
            auto_renew=False,
            alerts_muted_until=None,
            expiry_override=None,
        )

    def test_retorna_true(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        with patch.object(lic, "_consultar_firebase",
                          return_value=self._fb_grace()):
            valido, _, _ = lic.licenca_ativa()
        assert valido is True

    def test_alerta_revogacao_existe(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        with patch.object(lic, "_consultar_firebase",
                          return_value=self._fb_grace()):
            _, _, payload = lic.licenca_ativa()
        alerta = payload.get("_alerta_revogacao")
        assert alerta is not None
        assert "revogada" in alerta["mensagem"]
        assert alerta["prazo_iso"] != ""
        assert alerta["prazo_fmt"] != ""

    def test_prazo_iso_existe(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        with patch.object(lic, "_consultar_firebase",
                          return_value=self._fb_grace()):
            _, _, payload = lic.licenca_ativa()
        assert payload["_alerta_revogacao"]["prazo_iso"] != ""

    def test_prazo_fmt_existe(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        with patch.object(lic, "_consultar_firebase",
                          return_value=self._fb_grace()):
            _, _, payload = lic.licenca_ativa()
        assert payload["_alerta_revogacao"]["prazo_fmt"] != ""

    def test_nao_chama_segundos_desde_verificacao(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        with patch.object(lic, "_consultar_firebase",
                          return_value=self._fb_grace()), \
             patch.object(lic, "_segundos_desde_verificacao",
                          wraps=lic._segundos_desde_verificacao) as spy:
            lic.licenca_ativa()
        spy.assert_not_called()

    def test_mensagem_contem_24_horas(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        with patch.object(lic, "_consultar_firebase",
                          return_value=self._fb_grace()):
            _, _, payload = lic.licenca_ativa()
        assert "24 horas" in payload["_alerta_revogacao"]["mensagem"]


# ── REVOKED_EXPIRED ─────────────────────────────────────────────────────

class TestRevokedExpired:
    def test_retorna_false(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        revoked = datetime.now() - timedelta(hours=25)
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_REVOKED_EXPIRED,
                                           revoked_at=revoked.isoformat())):
            valido, msg, payload = lic.licenca_ativa()
        assert valido is False
        assert payload is None
        assert "expirou" in msg.lower()

    def test_exatamente_24h_bloqueia(self, lic_dir, chave_vitalicia):
        """Limite exato de 24h apos a revogacao deve bloquear."""
        _salvar(chave_vitalicia)
        revoked_exato = datetime.now() - timedelta(hours=24)
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_REVOKED_EXPIRED,
                                           revoked_at=revoked_exato.isoformat())):
            valido, msg, payload = lic.licenca_ativa()
        assert valido is False
        assert payload is None

    def test_remove_dat(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        assert (lic_dir / ".licenca.dat").exists()
        revoked = datetime.now() - timedelta(hours=25)
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_REVOKED_EXPIRED,
                                           revoked_at=revoked.isoformat())):
            lic.licenca_ativa()
        assert not (lic_dir / ".licenca.dat").exists()

    def test_remove_backup(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        lic.carregar_licenca()
        assert (lic_dir / ".licenca.dat.backup").exists()
        revoked = datetime.now() - timedelta(hours=25)
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_REVOKED_EXPIRED,
                                           revoked_at=revoked.isoformat())):
            lic.licenca_ativa()
        assert not (lic_dir / ".licenca.dat.backup").exists()


# ── DELETED ──────────────────────────────────────────────────────────────

class TestDeleted:
    def test_retorna_false(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_DELETED)):
            valido, msg, payload = lic.licenca_ativa()
        assert valido is False
        assert payload is None

    def test_mensagem_excluida(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_DELETED)):
            _, msg, _ = lic.licenca_ativa()
        assert "exclu" in msg.lower()

    def test_remove_dat(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        assert (lic_dir / ".licenca.dat").exists()
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_DELETED)):
            lic.licenca_ativa()
        assert not (lic_dir / ".licenca.dat").exists()

    def test_remove_backup(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        lic.carregar_licenca()
        assert (lic_dir / ".licenca.dat.backup").exists()
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_DELETED)):
            lic.licenca_ativa()
        assert not (lic_dir / ".licenca.dat.backup").exists()


# ── OFFLINE ──────────────────────────────────────────────────────────────

class TestOffline:
    def test_71h59m59s_true(self, lic_dir, chave_vitalicia):
        now = datetime.now()
        _salvar(chave_vitalicia, (now - timedelta(seconds=259199)).isoformat())
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_OFFLINE)):
            valido, _, _ = lic.licenca_ativa()
        assert valido is True

    def test_72h_exatas_false(self, lic_dir, chave_vitalicia):
        now = datetime.now()
        _salvar(chave_vitalicia, (now - timedelta(seconds=259200)).isoformat())
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_OFFLINE)):
            valido, _, _ = lic.licenca_ativa()
        assert valido is False

    def test_72h_1s_false(self, lic_dir, chave_vitalicia):
        now = datetime.now()
        _salvar(chave_vitalicia, (now - timedelta(seconds=259201)).isoformat())
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_OFFLINE)):
            valido, _, _ = lic.licenca_ativa()
        assert valido is False


# ── _consultar_firebase (unitario direto) ────────────────────────────────

class TestConsultarFirebase:
    def test_fb_missing_retorna_deleted(self, lic_dir):
        with patch.object(lic, "_firebase_get",
                          return_value=(lic.FB_MISSING, None)):
            result = lic._consultar_firebase("id_teste")
        assert result["estado"] == lic.ESTADO_DELETED

    def test_fb_error_retorna_offline(self, lic_dir):
        with patch.object(lic, "_firebase_get",
                          return_value=(lic.FB_ERROR, None)):
            result = lic._consultar_firebase("id_teste")
        assert result["estado"] == lic.ESTADO_OFFLINE

    def test_fb_ok_sem_revoked_retorna_valid(self, lic_dir):
        with patch.object(lic, "_firebase_get",
                          return_value=(lic.FB_OK, {"auto_renew": False})):
            result = lic._consultar_firebase("id_teste")
        assert result["estado"] == lic.ESTADO_VALID

    def test_revoked_at_ha_23h_retorna_grace(self, lic_dir):
        revoked_at = (datetime.now() - timedelta(hours=23)).isoformat()
        with patch.object(lic, "_firebase_get",
                          return_value=(lic.FB_OK, {"revoked_at": revoked_at})):
            result = lic._consultar_firebase("id_teste")
        assert result["estado"] == lic.ESTADO_REVOKED_GRACE

    def test_revoked_at_ha_25h_retorna_expired(self, lic_dir):
        revoked_at = (datetime.now() - timedelta(hours=25)).isoformat()
        with patch.object(lic, "_firebase_get",
                          return_value=(lic.FB_OK, {"revoked_at": revoked_at})):
            result = lic._consultar_firebase("id_teste")
        assert result["estado"] == lic.ESTADO_REVOKED_EXPIRED

    def test_revoked_at_exatamente_24h_retorna_expired(self, lic_dir):
        """Limite exato de 24h: agora == prazo, logo nao e grace."""
        revoked_exato = datetime.now() - timedelta(hours=24)
        revoked_at = revoked_exato.isoformat()
        with patch.object(lic, "_firebase_get",
                          return_value=(lic.FB_OK, {"revoked_at": revoked_at})):
            result = lic._consultar_firebase("id_teste")
        assert result["estado"] == lic.ESTADO_REVOKED_EXPIRED

    def test_revoked_at_invalido_retorna_expired(self, lic_dir):
        with patch.object(lic, "_firebase_get",
                          return_value=(lic.FB_OK,
                                        {"revoked_at": "data-invalida"})):
            result = lic._consultar_firebase("id_teste")
        assert result["estado"] == lic.ESTADO_REVOKED_EXPIRED

    def test_fb_ok_nao_e_dict_retorna_offline(self, lic_dir):
        with patch.object(lic, "_firebase_get",
                          return_value=(lic.FB_OK, "nao_e_dict")):
            result = lic._consultar_firebase("id_teste")
        assert result["estado"] == lic.ESTADO_OFFLINE


# ── EXPIRACAO LOCAL (sem depender do Firebase) ───────────────────────────

class TestExpiracaoLocal:
    """Licenca localmente vencida: mesmo consistente com Firebase VALID,
    o passo de expiracao local de licenca_ativa() deve bloquear e remover."""

    def test_plano_mensal_expirado_bloqueia(self, lic_dir):
        _salvar(_chave_mensal_expirada())
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_VALID)):
            valido, msg, payload = lic.licenca_ativa()
        assert valido is False
        assert payload is None
        assert "expirada" in msg.lower()

    def test_plano_mensal_expirado_remove_dat(self, lic_dir):
        _salvar(_chave_mensal_expirada())
        assert (lic_dir / ".licenca.dat").exists()
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_VALID)):
            lic.licenca_ativa()
        assert not (lic_dir / ".licenca.dat").exists()

    def test_plano_mensal_expirado_remove_backup(self, lic_dir):
        _salvar(_chave_mensal_expirada())
        lic.carregar_licenca()  # cria backup
        assert (lic_dir / ".licenca.dat.backup").exists()
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_VALID)):
            lic.licenca_ativa()
        assert not (lic_dir / ".licenca.dat.backup").exists()


# ── _firebase_get (captura real de excecoes) ────────────────────────────

class TestFirebaseGetExcecao:
    """Exercita a captura real de excecoes em _firebase_get().

    Nao mocka _firebase_get; apenas patcha urlopen para levantar erros
    e verifica que a funcao real retorna (FB_ERROR, None).
    """

    def test_urlopen_httperror_retorna_fb_error(self):
        with patch.object(lic, "urlopen",
                          side_effect=HTTPError("http://example", 404,
                                                "Not Found", {}, None)):
            status, data = lic._firebase_get("licenses/cliente_x")
        assert status == lic.FB_ERROR
        assert data is None

    def test_urlopen_urlerror_retorna_fb_error(self):
        with patch.object(lic, "urlopen",
                          side_effect=URLError("sem internet")):
            status, data = lic._firebase_get("licenses/cliente_x")
        assert status == lic.FB_ERROR
        assert data is None

    def test_urlopen_timeout_retorna_fb_error(self):
        with patch.object(lic, "urlopen",
                          side_effect=socket.timeout("timeout")):
            status, data = lic._firebase_get("licenses/cliente_x")
        assert status == lic.FB_ERROR
        assert data is None


# ── Sem licenca local ───────────────────────────────────────────────────

class TestSemLicencaLocal:
    """licenca_ativa() quando nao existe .licenca.dat local."""

    def test_retorna_false_sem_erro(self, lic_dir):
        valido, msg, payload = lic.licenca_ativa()
        assert valido is False
        assert payload is None

    def test_mensagem_licenca_nao_encontrada(self, lic_dir):
        _, msg, _ = lic.licenca_ativa()
        assert "nenhuma licença" in msg.lower()
