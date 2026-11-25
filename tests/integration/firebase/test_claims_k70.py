"""Contrato de autorização da proposta K70 exclusivamente no projeto demo."""

import json
import unittest

from test_rtdb import (
    ATUALIZACAO, LICENCA, REVOGACAO, SENHA_FIXTURE, _auth, _database,
    _expirar_token_emulado, _verificar_emuladores,
)


ALVO = "0012abcd34ef"
VIZINHA = "a1b2c3d4e5f6"
AUSENTE = "fedcba987654"


def _token(nome, claims=None):
    credenciais = {"email": f"{nome}@example.invalid", "password": SENHA_FIXTURE,
                   "returnSecureToken": True}
    status, conta = _auth("signUp", credenciais)
    if status != 200:
        raise RuntimeError(f"Falha preparando identidade sintética. HTTP {status}")
    if claims is not None:
        status, _ = _auth("update", {"localId": conta["localId"],
                                   "customAttributes": json.dumps(claims)}, preparar_fixture=True)
        if status != 200:
            raise RuntimeError(f"Falha preparando papel sintético. HTTP {status}")
        status, conta = _auth("signInWithPassword", credenciais)
        if status != 200:
            raise RuntimeError(f"Falha na autenticação sintética. HTTP {status}")
    return conta["idToken"]


def _exclusao(lid):
    return {f"licenses/{lid}": None, f"revoked/{lid}": None}


class ClaimsK70RTDBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _verificar_emuladores()
        cls.comum = _token("k70-usuario")
        cls.licencas = _token("k70-licencas", {"seagbh_license_admin": True})
        cls.atualizacoes = _token("k70-atualizacoes", {"seagbh_release_admin": True})
        cls.ambos = _token("k70-ambos", {"seagbh_license_admin": True, "seagbh_release_admin": True})
        # The shared helper verifies the old demo "admin" marker before
        # shortening the lifetime. K70 itself never trusts that marker.
        cls.expirado = _expirar_token_emulado(_token("k70-expirado", {
            "admin": True, "seagbh_license_admin": True, "seagbh_release_admin": True,
        }))
        cls.admin_generico = _token("k70-admin-generico", {"admin": True})
        cls.roles_textuais = _token("k70-claims-textuais", {
            "seagbh_license_admin": "true", "seagbh_release_admin": "true",
        })
        status, conta = _auth("signUp", {"returnSecureToken": True})
        if status != 200:
            raise RuntimeError(f"Falha preparando identidade anônima sintética. HTTP {status}")
        cls.anonimo = conta["idToken"]

    def setUp(self):
        self._preparar_dados()

    def _preparar_dados(self, auxiliar_presente=True):
        self.dados = {
            "licenses": {ALVO: LICENCA, VIZINHA: LICENCA},
            "revoked": {ALVO: REVOGACAO, VIZINHA: REVOGACAO},
            "updates": {"latest": ATUALIZACAO, "anterior": {"version": "1.0.0"}},
        }
        if not auxiliar_presente:
            del self.dados["revoked"][ALVO]
        self.assertEqual(_database("PUT", "", self.dados, preparar_fixture=True)[0], 200)

    def _estado(self):
        status, dados = _database("GET", "", preparar_fixture=True)
        self.assertEqual(status, 200)
        return dados

    def _negar(self, resposta):
        self.assertIn(resposta[0], (401, 403))
        self.assertEqual(resposta[1], {"error": "Permission denied"})

    def test_gets_legados_individuais_sem_permissao_de_listagem(self):
        for token in (None, self.anonimo, self.comum, self.licencas, self.atualizacoes, self.ambos):
            self.assertEqual(_database("GET", f"licenses/{ALVO}", token=token), (200, LICENCA))
            self.assertEqual(_database("GET", f"licenses/{AUSENTE}", token=token), (200, None))
            self.assertEqual(_database("GET", "updates/latest", token=token), (200, ATUALIZACAO))
            self.assertEqual(_database("GET", "updates/anterior", token=token), (200, {"version": "1.0.0"}))
            for caminho in ("", "licenses", "revoked", f"revoked/{ALVO}", "updates"):
                with self.subTest(caminho=caminho):
                    self._negar(_database("GET", caminho, token=token))

    def test_todas_as_escritas_administrativas_exigem_papel_valido(self):
        for perfil, token in {
            "sem-token": None, "auth-anonimo": self.anonimo, "comum": self.comum,
            "expirado": self.expirado, "claim-generica": self.admin_generico,
            "claim-textual": self.roles_textuais,
        }.items():
            for metodo, caminho, corpo in (
                ("PUT", f"licenses/{AUSENTE}/auto_renew", True),
                ("PATCH", f"licenses/{ALVO}", {"expiry_override": "2031-01-01"}),
                ("PUT", f"licenses/{ALVO}/revoked_at", REVOGACAO),
                ("DELETE", f"licenses/{ALVO}/revoked_at", None),
                ("DELETE", f"licenses/{ALVO}", None),
                ("DELETE", f"revoked/{ALVO}", None),
                ("PUT", "updates/latest", ATUALIZACAO),
                ("PATCH", "", _exclusao(ALVO)),
            ):
                with self.subTest(perfil=perfil, metodo=metodo, caminho=caminho):
                    self._negar(_database(metodo, caminho, corpo, token=token))
        status, _ = _database("PATCH", "", _exclusao(ALVO), token="identidade-invalida-fixture")
        self.assertIn(status, (400, 401, 403))
        self.assertEqual(self._estado(), self.dados)

    def test_papeis_de_licencas_e_atualizacoes_sao_independentes(self):
        caminho = f"licenses/{AUSENTE}"
        nova = dict(ATUALIZACAO, version="3.0.0")
        self._negar(_database("PUT", "updates/latest", nova, token=self.licencas))
        self._negar(_database("PUT", caminho, LICENCA, token=self.atualizacoes))
        self._negar(_database("PATCH", "", _exclusao(ALVO), token=self.atualizacoes))
        self._negar(_database("PUT", "updates/anterior", nova, token=self.ambos))
        self.assertEqual(self._estado(), self.dados)

        self.assertEqual(_database("PUT", caminho + "/auto_renew", False, token=self.licencas)[0], 200)
        self.assertEqual(_database("PUT", caminho, LICENCA, token=self.licencas)[0], 200)
        self.assertEqual(_database("PUT", caminho + "/alerts_muted_until", "2030-01-01",
                                   token=self.licencas)[0], 200)
        self.assertEqual(_database("PUT", caminho + "/expiry_override", "2031-01-01",
                                   token=self.licencas)[0], 200)
        self.assertEqual(_database("PUT", caminho + "/revoked_at", REVOGACAO, token=self.licencas)[0], 200)
        self.assertEqual(_database("DELETE", caminho + "/revoked_at", token=self.licencas)[0], 200)
        self.assertEqual(_database("GET", caminho), (
            200, dict(LICENCA, alerts_muted_until="2030-01-01", expiry_override="2031-01-01"),
        ))
        self.assertEqual(_database("PUT", "updates/latest", nova, token=self.atualizacoes)[0], 200)
        self.assertEqual(_database("GET", "updates/latest"), (200, nova))
        self.assertEqual(_database("PUT", caminho + "/auto_renew", False, token=self.ambos)[0], 200)
        self.assertEqual(_database("PUT", "updates/latest", ATUALIZACAO, token=self.ambos)[0], 200)
        self.assertEqual(_database("GET", "updates/latest"), (200, ATUALIZACAO))

    def test_patch_atomico_preserva_vizinhos_com_no_auxiliar_presente_ou_ausente(self):
        # Recoloca o estado a cada subcaso; só o papel em teste autoriza o PATCH.
        for presente in (True, False):
            with self.subTest(no_auxiliar_presente=presente):
                self._preparar_dados(auxiliar_presente=presente)
                self.assertEqual(_database("PATCH", "", _exclusao(ALVO), token=self.licencas)[0], 200)
                del self.dados["licenses"][ALVO]
                self.dados["revoked"].pop(ALVO, None)
                self.assertEqual(self._estado(), self.dados)

    def test_patch_recusado_se_uma_operacao_nao_pertence_ao_papel(self):
        lote = {**_exclusao(ALVO), "updates/latest": dict(ATUALIZACAO, version="3.0.0")}
        for token in (self.licencas, self.atualizacoes):
            self._negar(_database("PATCH", "", lote, token=token))
            self.assertEqual(self._estado(), self.dados)

    def test_identidade_comum_nao_pode_adquirir_papeis_administrativos(self):
        status, resultado = _auth("update", {
            "idToken": self.comum,
            "customAttributes": json.dumps({
                "seagbh_license_admin": True, "seagbh_release_admin": True,
            }),
        })
        self.assertIn(status, (400, 401, 403))
        self.assertEqual(resultado["error"]["message"], "INSUFFICIENT_PERMISSION")
        self._negar(_database("PATCH", "", _exclusao(ALVO), token=self.comum))
        self.assertEqual(self._estado(), self.dados)

    def test_permissao_de_escrita_nao_ignora_validacao_de_dados(self):
        self._negar(_database("PUT", f"licenses/{ALVO}/auto_renew", "true", token=self.licencas))
        self._negar(_database("PUT", f"licenses/{ALVO}/expiry_override", "formato-invalido",
                               token=self.licencas))
        self.assertEqual(self._estado(), self.dados)

    def test_papeis_nao_concedem_escrita_ampla(self):
        for caminho in ("", "licenses", "revoked", "updates", "registro-privado"):
            with self.subTest(caminho=caminho):
                self._negar(_database("PUT", caminho, {"sintetico": True}, token=self.ambos))
        self.assertEqual(self._estado(), self.dados)


if __name__ == "__main__":
    unittest.main(verbosity=2)
