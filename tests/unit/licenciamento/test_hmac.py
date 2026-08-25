"""Testes de geracao e validacao de chaves HMAC-SHA256.

Cobre: gerar_chave, validar_chave, chaves malformadas, dados corrompidos.
"""
import base64
import json

import pytest

import src.core.licenca as lic


class TestGerarChave:
    def test_gera_chave_no_formato_correto(self):
        chave = lic.gerar_chave("Cliente Teste", "vitalicio")
        partes = chave.split(".")
        assert len(partes) == 2

    def test_payload_base64_decodifica(self):
        chave = lic.gerar_chave("Cliente Teste", "vitalicio")
        payload_b64 = chave.split(".")[0]
        payload_json = base64.urlsafe_b64decode(payload_b64).decode()
        payload = json.loads(payload_json)
        assert isinstance(payload, dict)

    def test_assinatura_hex_40_chars(self):
        chave = lic.gerar_chave("Cliente Teste", "vitalicio")
        assinatura = chave.split(".")[1]
        assert len(assinatura) == 40
        assert all(c in "0123456789abcdef" for c in assinatura)

    def test_plano_invalido_erro(self):
        with pytest.raises(ValueError, match=r"Plano inválido"):
            lic.gerar_chave("Cliente", "plano_inexistente")


class TestValidarChave:
    def test_aceita_chave_valida_vitalicio(self):
        chave = lic.gerar_chave("Cliente Teste", "vitalicio")
        payload = lic.validar_chave(chave)
        assert payload is not None
        assert payload["c"] == "Cliente Teste"
        assert payload["p"] == "vitalicio"

    def test_aceita_chave_valida_anual(self):
        chave = lic.gerar_chave("Outro Cliente", "anual")
        payload = lic.validar_chave(chave)
        assert payload is not None
        assert payload["p"] == "anual"

    def test_campos_obrigatorios_presentes(self):
        chave = lic.gerar_chave("Teste", "mensal")
        payload = lic.validar_chave(chave)
        for campo in ("id", "c", "p", "i", "e"):
            assert campo in payload

    def test_modificar_cliente_invalida(self):
        """Alterar 'c' (cliente) sem atualizar assinatura invalida a chave."""
        chave = lic.gerar_chave("Cliente Original", "vitalicio")
        payload_b64, assinatura = chave.split(".")
        payload_json = base64.urlsafe_b64decode(payload_b64).decode()
        payload = json.loads(payload_json)
        payload["c"] = "Cliente Hackeado"
        novo_b64 = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode()
        chave_modificada = f"{novo_b64}.{assinatura}"
        assert lic.validar_chave(chave_modificada) is None

    def test_modificar_plano_invalida(self):
        """Alterar 'p' (plano) sem atualizar assinatura invalida a chave."""
        chave = lic.gerar_chave("Teste", "mensal")
        payload_b64, assinatura = chave.split(".")
        payload_json = base64.urlsafe_b64decode(payload_b64).decode()
        payload = json.loads(payload_json)
        payload["p"] = "vitalicio"
        novo_b64 = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode()
        chave_modificada = f"{novo_b64}.{assinatura}"
        assert lic.validar_chave(chave_modificada) is None

    def test_modificar_expiracao_invalida(self):
        """Alterar 'e' (expiracao) sem atualizar assinatura invalida a chave."""
        chave = lic.gerar_chave("Teste", "anual")
        payload_b64, assinatura = chave.split(".")
        payload_json = base64.urlsafe_b64decode(payload_b64).decode()
        payload = json.loads(payload_json)
        payload["e"] = "2099-12-31"
        novo_b64 = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode()
        chave_modificada = f"{novo_b64}.{assinatura}"
        assert lic.validar_chave(chave_modificada) is None

    def test_assinatura_trocada_invalida(self):
        """Trocar a assinatura por outra invalida a chave."""
        chave = lic.gerar_chave("Teste", "vitalicio")
        payload_b64, _ = chave.split(".")
        chave_falsa = f"{payload_b64}.0000000000000000000000000000000000000000"
        assert lic.validar_chave(chave_falsa) is None


class TestChavesMalformadas:
    def test_string_vazia(self):
        assert lic.validar_chave("") is None

    def test_sem_ponto(self):
        assert lic.validar_chave("naotemแยกponto") is None

    def test_mais_de_um_ponto(self):
        assert lic.validar_chave("a.b.c") is None

    def test_base64_invalido(self):
        assert lic.validar_chave("!!!invalido!!!.assinatura") is None

    def test_assinatura_vazia(self):
        chave = lic.gerar_chave("Teste", "vitalicio")
        payload_b64 = chave.split(".")[0]
        assert lic.validar_chave(f"{payload_b64}.") is None


class TestDadosCorrompidos:
    def test_json_invalido_no_payload(self):
        """Payload decodifica de base64 mas nao e JSON valido."""
        payload_fake = base64.urlsafe_b64encode(b"{invalid json").decode()
        chave = f"{payload_fake}.0000000000000000000000000000000000000000"
        assert lic.validar_chave(chave) is None

    def test_payload_sem_campos_obrigatorios(self):
        """JSON valido mas sem campos obrigatorios (id, c, p, i, e)."""
        payload_incompleto = json.dumps({"c": "Teste"}).encode()
        payload_b64 = base64.urlsafe_b64encode(payload_incompleto).decode()
        # Gerar assinatura correta para este payload
        assinatura = lic._assinar(payload_incompleto.decode())
        chave = f"{payload_b64}.{assinatura}"
        assert lic.validar_chave(chave) is None

    def test_base64_truncado(self):
        """Metade de um payload base64 valido."""
        chave_completa = lic.gerar_chave("Teste", "vitalicio")
        metade = len(chave_completa) // 2
        chave_truncada = chave_completa[:metade]
        assert lic.validar_chave(chave_truncada) is None
