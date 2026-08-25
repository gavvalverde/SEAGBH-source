"""Testes do ciclo de backup e remocao de licenca.

Cobre: criacao de backup, preservacao, remocao parcial/total,
HMAC invalido, e estados DELETED/EXPIRED que removem ambos.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import src.core.licenca as lic


# ── Helpers ──────────────────────────────────────────────────────────────

def _salvar(chave):
    lic._salvar_dados_brutos({
        "chave": chave,
        "ultima_verificacao": datetime.now().isoformat(),
        "firebase": {},
    })


def _fb(estado, **extras):
    result = {"estado": estado}
    result.update(extras)
    return result


# ── Backup: criacao e preservacao ───────────────────────────────────────

class TestBackupCriacaoPreservacao:
    def test_primeiro_carregamento_cria_backup(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        assert not (lic_dir / ".licenca.dat.backup").exists()
        lic.carregar_licenca()
        assert (lic_dir / ".licenca.dat.backup").exists()

    def test_segundo_carregamento_nao_recria_backup(self, lic_dir, chave_vitalicia):
        """Backup criado na primeira leitura nao e sobrescrito na segunda."""
        dados1 = {"chave": chave_vitalicia,
                   "ultima_verificacao": "2025-01-01T00:00:00", "firebase": {}}
        lic._salvar_dados_brutos(dados1)
        lic.carregar_licenca()
        backup1 = (lic_dir / ".licenca.dat.backup").read_text(encoding="utf-8")

        # Sobrescrever .licenca.dat com dados diferentes
        dados2 = {"chave": chave_vitalicia,
                   "ultima_verificacao": "2099-12-31T23:59:59", "firebase": {}}
        lic._salvar_dados_brutos(dados2)
        lic.carregar_licenca()
        backup2 = (lic_dir / ".licenca.dat.backup").read_text(encoding="utf-8")

        assert backup1 == backup2  # backup nao foi re-criado


# ── Remocao ──────────────────────────────────────────────────────────────

class TestRemocao:
    def test_remover_licenca_remove_ambos(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        lic.carregar_licenca()  # cria backup
        assert (lic_dir / ".licenca.dat").exists()
        assert (lic_dir / ".licenca.dat.backup").exists()
        lic.remover_licenca()
        assert not (lic_dir / ".licenca.dat").exists()
        assert not (lic_dir / ".licenca.dat.backup").exists()

    def test_remover_sem_backup(self, lic_dir, chave_vitalicia):
        """remover_backup=False preserva o .backup (para recuperacao manual)."""
        _salvar(chave_vitalicia)
        lic.carregar_licenca()
        assert (lic_dir / ".licenca.dat.backup").exists()
        lic.remover_licenca(remover_backup=False)
        assert not (lic_dir / ".licenca.dat").exists()
        assert (lic_dir / ".licenca.dat.backup").exists()

    def test_remover_inexistente_nao_erro(self, lic_dir):
        """Chamar remover_licenca quando nao existe nao deve gerar excecao."""
        lic.remover_licenca()  # nao deve levantar excecao


# ── HMAC invalido ───────────────────────────────────────────────────────

class TestHmacInvalido:
    def test_remove_dat_preserva_backup(self, lic_dir, chave_vitalicia):
        """Arquivo corrompido: remove .dat mas preserva .backup."""
        _salvar(chave_vitalicia)
        lic.carregar_licenca()  # cria backup
        # Corromper o .licenca.dat
        dat_path = lic_dir / ".licenca.dat"
        dat_path.write_text("dados_corrompidos", encoding="utf-8")
        resultado = lic.carregar_licenca()
        assert resultado is None
        assert not dat_path.exists()
        assert (lic_dir / ".licenca.dat.backup").exists()


# ── Estados que removem ambos ───────────────────────────────────────────

class TestEstadosRemocao:
    def test_deleted_remove_ambos(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        lic.carregar_licenca()
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_DELETED)):
            lic.licenca_ativa()
        assert not (lic_dir / ".licenca.dat").exists()
        assert not (lic_dir / ".licenca.dat.backup").exists()

    def test_expired_remove_ambos(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        lic.carregar_licenca()
        revoked = datetime.now() - timedelta(hours=25)
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_REVOKED_EXPIRED,
                                           revoked_at=revoked.isoformat())):
            lic.licenca_ativa()
        assert not (lic_dir / ".licenca.dat").exists()
        assert not (lic_dir / ".licenca.dat.backup").exists()

    def test_valid_nao_remove(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        lic.carregar_licenca()
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_VALID)):
            lic.licenca_ativa()
        assert (lic_dir / ".licenca.dat").exists()
        assert (lic_dir / ".licenca.dat.backup").exists()

    def test_grace_nao_remove(self, lic_dir, chave_vitalicia):
        _salvar(chave_vitalicia)
        lic.carregar_licenca()
        revoked = datetime.now() - timedelta(hours=23)
        with patch.object(lic, "_consultar_firebase",
                          return_value=_fb(lic.ESTADO_REVOKED_GRACE,
                                           revoked_at=revoked.isoformat(),
                                           prazo_iso=(revoked + timedelta(hours=24)).isoformat(),
                                           auto_renew=False,
                                           alerts_muted_until=None,
                                           expiry_override=None)):
            lic.licenca_ativa()
        assert (lic_dir / ".licenca.dat").exists()
        assert (lic_dir / ".licenca.dat.backup").exists()
