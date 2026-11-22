"""Contrato REST com Firebase Auth e RTDB emulados em um projeto demo dedicado."""

import base64
import json
import os
import time
import unittest
from http.client import HTTPConnection
from urllib.parse import urlencode


PROJETO = "demo-seagbh-authz"
HOST = "127.0.0.1"
AUTH_PORTA = 19199
DATABASE_PORTA = 19200
NAMESPACE = f"{PROJETO}-default-rtdb"
SENHA_FIXTURE = "somente-fixture-emulador"
LICENCA = {"auto_renew": True, "expiry_override": "2030-01-01", "alerts_muted_until": "2028-01-01"}
REVOGACAO = "2026-01-01T00:00:00"
ATUALIZACAO = {
    "version": "2.0.0",
    "url": "https://example.invalid/fixture.exe",
    "sha256": "0" * 64,
    "obrigatoria": False,
}
_SEM_CORPO = object()


def _requisicao(porta, metodo, destino, corpo=_SEM_CORPO, *, preparar_fixture=False):
    headers = {"Accept": "application/json"}
    dados = None
    if corpo is not _SEM_CORPO:
        headers["Content-Type"] = "application/json"
        dados = json.dumps(corpo).encode("utf-8")
    if preparar_fixture:
        headers["Authorization"] = "Bearer owner"
    conexao = HTTPConnection(HOST, porta, timeout=10)
    try:
        conexao.request(metodo, destino, body=dados, headers=headers)
        with conexao.getresponse() as resposta:
            return resposta.status, json.load(resposta)
    finally:
        conexao.close()


def _auth(acao, corpo, *, preparar_fixture=False):
    prefixo = "/identitytoolkit.googleapis.com/v1"
    if preparar_fixture:
        prefixo += f"/projects/{PROJETO}"
    return _requisicao(
        AUTH_PORTA, "POST", f"{prefixo}/accounts:{acao}?key=demo-fixture",
        corpo, preparar_fixture=preparar_fixture,
    )


def _database(metodo, caminho, corpo=_SEM_CORPO, *, token=None, preparar_fixture=False):
    query = {"ns": NAMESPACE}
    if token is not None:
        query["auth"] = token
    return _requisicao(
        DATABASE_PORTA, metodo, f"/{caminho}.json?{urlencode(query)}",
        corpo, preparar_fixture=preparar_fixture,
    )


def _expirar_token_emulado(token):
    cabecalho, corpo, assinatura = token.split(".")
    if assinatura:
        raise RuntimeError("Esperava um token sem assinatura emitido pelo emulador local.")
    payload = json.loads(base64.urlsafe_b64decode(corpo + "=" * (-len(corpo) % 4)))
    if payload.get("aud") != PROJETO or payload.get("admin") is not True:
        raise RuntimeError("O token deve ser da conta administrativa da fixture demo.")
    payload["exp"] = int(time.time()) - 1
    payload["iat"] = payload["exp"] - 3600
    prazo_vencido = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{cabecalho}.{prazo_vencido}."


class FirebaseAutorizacaoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ambiente_esperado = {
            "GCLOUD_PROJECT": PROJETO,
            "FIREBASE_AUTH_EMULATOR_HOST": f"{HOST}:{AUTH_PORTA}",
            "FIREBASE_DATABASE_EMULATOR_HOST": f"{HOST}:{DATABASE_PORTA}",
        }
        if any(os.environ.get(nome) != valor for nome, valor in ambiente_esperado.items()):
            raise RuntimeError("Execute a suíte via emulators:exec no projeto demo-seagbh-authz.")

        comum = cls._criar_conta("comum")
        admin = cls._criar_conta("admin")
        status, _ = _auth("update", {
            "localId": admin["localId"],
            "customAttributes": json.dumps({"admin": True}),
        }, preparar_fixture=True)
        if status != 200:
            raise RuntimeError(f"Falha ao preparar o papel administrativo local. HTTP {status}")

        status, conta = _auth("signInWithPassword", {
            "email": "admin@example.invalid",
            "password": SENHA_FIXTURE,
            "returnSecureToken": True,
        })
        if status != 200:
            raise RuntimeError(f"Falha no login administrativo local. HTTP {status}")
        cls.admin = conta["idToken"]
        cls.comum = comum["idToken"]
        cls.expirado = _expirar_token_emulado(cls.admin)

    @staticmethod
    def _criar_conta(nome):
        status, conta = _auth("signUp", {
            "email": f"{nome}@example.invalid",
            "password": SENHA_FIXTURE,
            "returnSecureToken": True,
        })
        if status != 200:
            raise RuntimeError(f"Falha ao criar uma conta de teste local. HTTP {status}")
        return conta

    def setUp(self):
        ids = ("normal", "bloqueio-licenca", "bloqueio-revogacao", "vizinha")
        self.dados = {
            "licenses": {lid: LICENCA for lid in ids},
            "revoked": {lid: REVOGACAO for lid in ids},
            "updates": {"latest": ATUALIZACAO, "anterior": {"version": "1.0.0"}},
        }
        status, _ = _database("PUT", "", self.dados, preparar_fixture=True)
        self.assertEqual(status, 200)

    def _assert_permissao_negada(self, resposta):
        status, dados = resposta
        self.assertIn(status, (401, 403))
        self.assertEqual(dados, {"error": "Permission denied"})

    def _estado(self):
        status, dados = _database("GET", "", preparar_fixture=True)
        self.assertEqual(status, 200)
        return dados

    def test_leituras_legadas_individuais_e_licenca_ausente(self):
        for perfil, token in (("anônimo", None), ("comum", self.comum), ("admin", self.admin)):
            with self.subTest(perfil=perfil):
                self.assertEqual(_database("GET", "licenses/normal", token=token), (200, LICENCA))
                self.assertEqual(_database("GET", "licenses/ausente", token=token), (200, None))
                self.assertEqual(_database("GET", "updates/latest", token=token), (200, ATUALIZACAO))

    def test_colecoes_e_revogacao_legada_nao_sao_publicas(self):
        for token in (None, self.comum, self.admin):
            for caminho in ("", "licenses", "revoked", "updates", "updates/anterior"):
                with self.subTest(caminho=caminho):
                    self._assert_permissao_negada(_database("GET", caminho, token=token))
        for token in (None, self.comum):
            self._assert_permissao_negada(_database("GET", "revoked/normal", token=token))
        self.assertEqual(_database("GET", "revoked/normal", token=self.admin), (200, REVOGACAO))

    def test_escritas_recusadas_sem_sessao_e_para_usuario_comum(self):
        operacoes = (
            ("PUT", "licenses/nova", LICENCA),
            ("PATCH", "licenses/normal", {"auto_renew": False}),
            ("DELETE", "licenses/normal", _SEM_CORPO),
            ("PUT", "licenses/normal/revoked_at", REVOGACAO),
            ("DELETE", "revoked/normal", _SEM_CORPO),
            ("PUT", "updates/latest", {**ATUALIZACAO, "version": "3.0.0"}),
            ("PATCH", "", {"licenses/normal": None, "revoked/normal": None}),
        )
        for perfil, token in (("sem sessão", None), ("comum", self.comum)):
            for metodo, caminho, corpo in operacoes:
                with self.subTest(perfil=perfil, metodo=metodo, caminho=caminho):
                    self._assert_permissao_negada(_database(metodo, caminho, corpo, token=token))
        self.assertEqual(self._estado(), self.dados)

    def test_token_invalido_nao_autoriza_escrita(self):
        status, _ = _database(
            "PATCH", "licenses/normal", {"auto_renew": False}, token="token-invalido-fixture"
        )
        self.assertIn(status, (400, 401, 403))
        self.assertEqual(self._estado(), self.dados)

    def test_regra_da_fixture_recusa_claim_admin_com_expiracao_vencida(self):
        operacoes = (
            ("PATCH", "licenses/normal", {"auto_renew": False}),
            ("DELETE", "revoked/normal", _SEM_CORPO),
            ("PUT", "updates/latest", {**ATUALIZACAO, "version": "3.0.0"}),
        )
        for metodo, caminho, corpo in operacoes:
            with self.subTest(caminho=caminho):
                self._assert_permissao_negada(_database(metodo, caminho, corpo, token=self.expirado))
        self._assert_permissao_negada(_database("GET", "revoked/normal", token=self.expirado))
        self.assertEqual(self._estado(), self.dados)

    def test_usuario_comum_nao_pode_atribuir_claim(self):
        status, dados = _auth("update", {
            "idToken": self.comum,
            "customAttributes": json.dumps({"admin": True}),
        })
        self.assertIn(status, (400, 401, 403))
        self.assertEqual(dados["error"]["message"], "INSUFFICIENT_PERMISSION")
        self._assert_permissao_negada(_database("DELETE", "licenses/normal", token=self.comum))

    def test_admin_cria_altera_revoga_reativa_e_remove_licenca(self):
        operacoes = (
            ("PUT", "licenses/nova", LICENCA, LICENCA),
            ("PATCH", "licenses/nova", {"auto_renew": False}, {**LICENCA, "auto_renew": False}),
            ("PUT", "licenses/nova/revoked_at", REVOGACAO, REVOGACAO),
            ("DELETE", "licenses/nova/revoked_at", _SEM_CORPO, None),
            ("DELETE", "licenses/nova", _SEM_CORPO, None),
        )
        for metodo, caminho, corpo, esperado in operacoes:
            with self.subTest(metodo=metodo, caminho=caminho):
                status, _ = _database(metodo, caminho, corpo, token=self.admin)
                self.assertEqual(status, 200)
                self.assertEqual(_database("GET", caminho), (200, esperado))

    def test_admin_altera_revogacao_legada_e_publica_atualizacao(self):
        nova_data = "2026-02-01T00:00:00"
        self.assertEqual(_database("PUT", "revoked/normal", nova_data, token=self.admin)[0], 200)
        self.assertEqual(_database("GET", "revoked/normal", token=self.admin), (200, nova_data))
        self.assertEqual(_database("DELETE", "revoked/normal", token=self.admin), (200, None))
        atualizacao = {**ATUALIZACAO, "version": "3.0.0"}
        self.assertEqual(_database("PUT", "updates/latest", atualizacao, token=self.admin), (200, atualizacao))
        self.assertEqual(_database("GET", "updates/latest"), (200, atualizacao))
        self.assertEqual(self._estado()["updates"]["anterior"], {"version": "1.0.0"})

    def test_patch_multilocal_remove_os_dois_nos_e_preserva_vizinhos(self):
        patch = {"licenses/normal": None, "revoked/normal": None}
        self.assertEqual(_database("PATCH", "", patch, token=self.admin)[0], 200)
        del self.dados["licenses"]["normal"]
        del self.dados["revoked"]["normal"]
        self.assertEqual(self._estado(), self.dados)
        self.assertEqual(_database("GET", "licenses/normal"), (200, None))

    def test_patch_multilocal_recusado_em_um_no_preserva_ambos(self):
        for lid, permitido in (
            ("bloqueio-licenca", "revoked"),
            ("bloqueio-revogacao", "licenses"),
        ):
            with self.subTest(caminho_permitido=permitido):
                patch = {f"licenses/{lid}": None, f"revoked/{lid}": None}
                self._assert_permissao_negada(_database("PATCH", "", patch, token=self.admin))
                self.assertEqual(self._estado(), self.dados)

                self.assertEqual(_database("DELETE", f"{permitido}/{lid}", token=self.admin)[0], 200)
                status, _ = _database("PUT", "", self.dados, preparar_fixture=True)
                self.assertEqual(status, 200)

    def test_patch_tambem_verifica_permissao_quando_no_auxiliar_ja_esta_ausente(self):
        for lid in ("normal", "bloqueio-revogacao"):
            with self.subTest(lid=lid):
                status, _ = _database("DELETE", f"revoked/{lid}", preparar_fixture=True)
                self.assertEqual(status, 200)
                del self.dados["revoked"][lid]

                patch = {f"licenses/{lid}": None, f"revoked/{lid}": None}
                resposta = _database("PATCH", "", patch, token=self.admin)
                if lid == "normal":
                    self.assertEqual(resposta[0], 200)
                    del self.dados["licenses"][lid]
                else:
                    self._assert_permissao_negada(resposta)
                self.assertEqual(self._estado(), self.dados)


if __name__ == "__main__":
    unittest.main(verbosity=2)
