"""Política administrativa por UID no Auth e no RTDB de um projeto demo local."""

import unittest

from test_rtdb import (
    ATUALIZACAO, AUTH_PORTA, LICENCA, PROJETO, REVOGACAO, SENHA_FIXTURE,
    _auth, _database, _requisicao, _verificar_emuladores,
)


UID_ADMIN = "uid-admin-sintetica"
ALVO = "0012abcd34ef"
VIZINHA = "a1b2c3d4e5f6"
AUSENTE = "fedcba987654"


def _conta(nome, uid=None):
    credenciais = {"email": f"{nome}@example.invalid", "password": SENHA_FIXTURE}
    if uid is None:
        status, conta = _auth("signUp", dict(credenciais, returnSecureToken=True))
    else:
        status, conta = _requisicao(
            AUTH_PORTA, "POST", f"/identitytoolkit.googleapis.com/v1/projects/{PROJETO}/accounts",
            dict(credenciais, localId=uid), preparar_fixture=True,
        )
    if status != 200 or (uid is not None and conta.get("localId") != uid):
        raise RuntimeError(f"Falha ao preparar conta da fixture. HTTP {status}")
    status, conta = _auth("signInWithPassword", dict(credenciais, returnSecureToken=True))
    if status != 200 or (uid is not None and conta.get("localId") != uid):
        raise RuntimeError(f"Falha no login da fixture. HTTP {status}")
    return conta


def _renovar(refresh_token):
    return _requisicao(
        AUTH_PORTA, "POST", "/securetoken.googleapis.com/v1/token?key=demo-fixture",
        {"grant_type": "refresh_token", "refresh_token": refresh_token}, formulario=True,
    )


def _exclusao(lid):
    return {f"licenses/{lid}": None, f"revoked/{lid}": None}


class PoliticaUidRTDBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _verificar_emuladores()
        cls.admin = _conta("admin-uid", UID_ADMIN)
        cls.comum = _conta("conta-comum")
        status, anonima = _auth("signUp", {"returnSecureToken": True})
        if status != 200:
            raise RuntimeError(f"Falha ao preparar conta anônima da fixture. HTTP {status}")
        cls.anonimo = anonima["idToken"]

    def setUp(self):
        self.dados = {
            "licenses": {ALVO: LICENCA, VIZINHA: LICENCA},
            "revoked": {ALVO: REVOGACAO, VIZINHA: REVOGACAO},
            "updates": {"latest": ATUALIZACAO},
        }
        self.assertEqual(_database("PUT", "", self.dados, preparar_fixture=True)[0], 200)

    def _estado(self):
        # A inspeção privilegiada só observa o estado sintético. Operações sob
        # teste usam exclusivamente o token da conta, ou nenhum token.
        status, estado = _database("GET", "", preparar_fixture=True)
        self.assertEqual(status, 200)
        return estado

    def _negar(self, resposta):
        status, dados = resposta
        self.assertIn(status, (401, 403))
        self.assertEqual(dados, {"error": "Permission denied"})

    def test_anonimo_e_conta_comum_nao_escrevem(self):
        for token in (None, self.anonimo, self.comum["idToken"]):
            for metodo, caminho, dados in (
                ("PUT", f"licenses/{AUSENTE}/auto_renew", True),
                ("PUT", f"licenses/{ALVO}/revoked_at", REVOGACAO),
                ("DELETE", f"licenses/{ALVO}", None),
                ("PUT", f"revoked/{ALVO}", "outra-data"),
                ("DELETE", f"revoked/{ALVO}", None),
                ("PUT", "updates/latest", dict(ATUALIZACAO, version="3.0.0")),
                ("DELETE", "updates/latest", None),
            ):
                self._negar(_database(metodo, caminho, dados, token=token))
        self.assertEqual(self._estado(), self.dados)

    def test_admin_cria_altera_revoga_e_reativa_licenca(self):
        token = self.admin["idToken"]
        caminho = f"licenses/{AUSENTE}"
        for metodo, sufixo, corpo, esperado in (
            ("PUT", "/auto_renew", True, {"auto_renew": True}),
            ("PUT", "/expiry_override", "2031-01-01", {"auto_renew": True, "expiry_override": "2031-01-01"}),
            ("PUT", "/revoked_at", REVOGACAO, {
                "auto_renew": True, "expiry_override": "2031-01-01", "revoked_at": REVOGACAO,
            }),
            ("DELETE", "/revoked_at", None, {"auto_renew": True, "expiry_override": "2031-01-01"}),
        ):
            self.assertEqual(_database(metodo, caminho + sufixo, corpo, token=token)[0], 200)
            self.assertEqual(_database("GET", caminho), (200, esperado))

    def test_admin_publica_atualizacao_e_escreve_revogacao_legada(self):
        token = self.admin["idToken"]
        nova = dict(ATUALIZACAO, version="3.0.0")
        self.assertEqual(_database("PUT", "updates/latest", nova, token=token), (200, nova))
        self.assertEqual(_database("GET", "updates/latest"), (200, nova))
        self.assertEqual(_database("PUT", "updates/anterior", ATUALIZACAO, token=token)[0], 200)
        self.assertEqual(_database("GET", "updates/anterior"), (200, ATUALIZACAO))
        self.assertEqual(_database("PUT", f"revoked/{AUSENTE}", REVOGACAO, token=token)[0], 200)
        self.assertEqual(self._estado()["revoked"][AUSENTE], REVOGACAO)

    def test_patch_admin_remove_ambos_os_nos_com_revogacao_presente_ou_ausente(self):
        token = self.admin["idToken"]
        for possui_revogacao in (True, False):
            with self.subTest(possui_revogacao=possui_revogacao):
                self.assertEqual(_database("PUT", f"licenses/{ALVO}", LICENCA, token=token)[0], 200)
                if possui_revogacao:
                    self.assertEqual(self._estado()["revoked"][ALVO], REVOGACAO)
                else:
                    self.assertNotIn(ALVO, self._estado()["revoked"])
                self.assertEqual(_database("PATCH", "", _exclusao(ALVO), token=token)[0], 200)
                self.assertEqual(_database("GET", f"licenses/{ALVO}"), (200, None))
                estado = self._estado()
                self.assertEqual(estado["licenses"], {VIZINHA: LICENCA})
                self.assertEqual(estado["revoked"], {VIZINHA: REVOGACAO})

    def test_patch_comum_ou_anonimo_preserva_ambos_os_nos(self):
        for token in (None, self.anonimo, self.comum["idToken"]):
            self._negar(_database("PATCH", "", _exclusao(ALVO), token=token))
        self.assertEqual(self._estado(), self.dados)

    def test_leitura_publica_individual_inclusive_ausencia(self):
        self.assertEqual(_database("GET", f"licenses/{ALVO}"), (200, LICENCA))
        self.assertEqual(_database("GET", f"licenses/{AUSENTE}"), (200, None))
        self.assertEqual(_database("GET", "updates/latest"), (200, ATUALIZACAO))
        self.assertEqual(_database("GET", "updates/ausente"), (200, None))

    def test_raiz_colecoes_e_revogacao_ilegiveis_para_todos(self):
        for token in (None, self.anonimo, self.comum["idToken"], self.admin["idToken"]):
            for caminho in ("", "licenses", "revoked", "updates", f"revoked/{ALVO}", f"revoked/{AUSENTE}"):
                self._negar(_database("GET", caminho, token=token))
            self._negar(_database("PUT", "licenses", {ALVO: LICENCA}, token=token))
        self.assertEqual(self._estado(), self.dados)

    def test_admin_nao_escreve_na_raiz_ou_em_outra_colecao(self):
        token = self.admin["idToken"]
        self._negar(_database("PUT", "", self.dados, token=token))
        self._negar(_database("PATCH", "", {**_exclusao(ALVO), "outro/item": True}, token=token))
        self.assertEqual(self._estado(), self.dados)

    def test_ids_invalidos_e_chave_incompativel_com_rtdb(self):
        token = self.admin["idToken"]
        for lid in ("0012ABCD34EF", "0012abcd34e", "0012abcd34ef0", "0012abcd34eg"):
            with self.subTest(id_sintetico=lid):
                self._negar(_database("PUT", f"licenses/{lid}/auto_renew", True, token=token))
                self._negar(_database("GET", f"licenses/{lid}"))
                self._negar(_database("PATCH", "", _exclusao(lid), token=token))
        self.assertEqual(_database("PUT", f"licenses/{ALVO}", {"campo.invalido": True}, token=token)[0], 400)
        self.assertEqual(self._estado(), self.dados)

    def test_tokens_invalidos_e_renovacao_preservam_papeis(self):
        status, _ = _database("PATCH", "", _exclusao(ALVO), token="token-sintetico-invalido")
        self.assertIn(status, (401, 403))
        self.assertEqual(_renovar("refresh-sintetico-invalido")[0], 400)

        for conta in (self.comum, self.admin):
            status, renovada = _renovar(conta["refreshToken"])
            self.assertEqual(status, 200)
            self.assertEqual(renovada["user_id"], conta["localId"])
            resposta = _database("PUT", f"licenses/{ALVO}/auto_renew", False, token=renovada["id_token"])
            if conta is self.comum:
                self._negar(resposta)
                self.assertEqual(self._estado(), self.dados)
            else:
                self.assertEqual(resposta[0], 200)
                self.assertEqual(_database("GET", f"licenses/{ALVO}/auto_renew"), (200, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
