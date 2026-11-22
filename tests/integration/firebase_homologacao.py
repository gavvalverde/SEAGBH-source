"""Verifica uma política RTDB sanitizada usando as fixtures locais do PR #79."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJETO = "demo-seagbh-authz"
_TESTES = {
    "test_escritas_recusadas_sem_sessao_e_para_usuario_comum": "Escritas de conta comum e anônima",
    "test_operacoes_diretas_exigem_papel": "Operações diretas em campos, revogações e atualizações",
    "test_patch_multilocal_remove_os_dois_nos_e_preserva_vizinhos": "PATCH administrativo com nó auxiliar",
    "test_patch_administrativo_sem_no_auxiliar": "PATCH administrativo sem nó auxiliar",
    "test_leituras_legadas_individuais_e_licenca_ausente": "GETs legados e licença ausente com 200 null",
    "test_leituras_privadas_e_listagens_sao_negadas": "Leituras privadas e herança em coleções",
    "test_raiz_nao_concede_escrita_geral": "Escrita geral fora do escopo administrativo",
    "test_token_invalido_nao_autoriza_escrita": "Recusa de token inválido no emulador",
}


def _executar_contratos():
    import test_rtdb as ref

    class PoliticaRTDB(ref.FirebaseAutorizacaoTest):
        def test_operacoes_diretas_exigem_papel(self):
            operacoes = (
                ("PUT", "licenses/normal/auto_renew", False),
                ("PUT", "licenses/normal/revoked_at", ref.REVOGACAO),
                ("PUT", "licenses/normal/alerts_muted_until", "2029-01-01"),
                ("PUT", "licenses/normal/expiry_override", "2031-01-01"),
                ("PUT", "revoked/normal", "2026-02-01T00:00:00"),
                ("PATCH", "updates/latest", {"version": "3.0.0"}),
                ("DELETE", "updates/latest", ref._SEM_CORPO),
            )
            for token in (None, self.comum):
                for metodo, caminho, dados in operacoes:
                    with self.subTest(caminho=caminho):
                        self._assert_permissao_negada(
                            ref._database(metodo, caminho, dados, token=token)
                        )
            self.assertEqual(self._estado(), self.dados)

        def test_patch_administrativo_sem_no_auxiliar(self):
            self.assertEqual(
                ref._database("DELETE", "revoked/normal", preparar_fixture=True)[0], 200
            )
            del self.dados["revoked"]["normal"]
            patch = {"licenses/normal": None, "revoked/normal": None}
            self.assertEqual(ref._database("PATCH", "", patch, token=self.admin)[0], 200)
            del self.dados["licenses"]["normal"]
            self.assertEqual(self._estado(), self.dados)
            self.assertEqual(ref._database("GET", "licenses/normal"), (200, None))

        def test_leituras_privadas_e_listagens_sao_negadas(self):
            for token in (None, self.comum):
                for caminho in ("", "licenses", "revoked", "revoked/normal", "updates", "updates/anterior"):
                    with self.subTest(caminho=caminho):
                        self._assert_permissao_negada(ref._database("GET", caminho, token=token))

        def test_raiz_nao_concede_escrita_geral(self):
            for token in (None, self.comum, self.admin):
                self._assert_permissao_negada(
                    ref._database("PATCH", "", {"controle-homologacao": True}, token=token)
                )
            self.assertEqual(self._estado(), self.dados)

    class Resumo(unittest.TestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            print(f"SEAGBH_TESTE {test._testMethodName} OK")

        def addFailure(self, test, err):
            super().addFailure(test, err)
            print(f"SEAGBH_TESTE {test._testMethodName} FALHA")

        def addError(self, test, err):
            super().addError(test, err)
            nome = getattr(test, "_testMethodName", "preparacao")
            print(f"SEAGBH_TESTE {nome} ERRO")

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err is not None:
                print(f"SEAGBH_TESTE {test._testMethodName} FALHA")

    conjunto = unittest.TestSuite(PoliticaRTDB(nome) for nome in _TESTES)
    return int(not conjunto.run(Resumo()).wasSuccessful())


def _preparar_pasta(pasta, referencia, regras):
    config = json.loads((referencia / "firebase.json").read_text(encoding="utf-8"))
    if config["database"] != {"rules": "rules.fixture.json"}:
        raise ValueError("configuração do banco")
    portas = {"auth": 19199, "database": 19200, "hub": 19440, "logging": 19450}
    emuladores = {}
    for nome, porta in portas.items():
        esperado = {"host": "127.0.0.1", "port": porta}
        if config["emulators"][nome] != esperado:
            raise ValueError("endereço de emulação")
        emuladores[nome] = esperado
    politica = json.loads(regras.read_text(encoding="utf-8-sig"))
    if not isinstance(politica, dict) or set(politica) != {"rules"} or not isinstance(politica["rules"], dict):
        raise ValueError("formato das regras")

    # De toda a configuração externa, reutilizamos apenas os pontos de escuta verificados.
    config_local = {
        "database": config["database"],
        "emulators": {**emuladores, "ui": {"enabled": False}},
    }
    (pasta / "firebase.json").write_text(json.dumps(config_local), encoding="utf-8")
    (pasta / "rules.fixture.json").write_text(json.dumps(politica), encoding="utf-8")
    shutil.copyfile(referencia / "test_rtdb.py", pasta / "test_rtdb.py")
    shutil.copyfile(__file__, pasta / "firebase_homologacao.py")


def _exibir_resultados(saida):
    resultados = {}
    for linha in saida.splitlines():
        partes = linha.split()
        if len(partes) == 3 and partes[0] == "SEAGBH_TESTE":
            _, teste, status = partes
            if teste in (*_TESTES, "preparacao") and status in {"OK", "FALHA", "ERRO"}:
                resultados[teste] = status
    for teste, descricao in _TESTES.items():
        print(f"{descricao}: {resultados.get(teste, 'NÃO EXECUTADA')}")
    if resultados.get("preparacao") == "ERRO":
        print("Erro na preparação das contas ou do ambiente demo.")
    return all(resultados.get(teste) == "OK" for teste in _TESTES)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--referencia", type=Path, required=True, help="Diretório firebase do PR #79.")
    parser.add_argument("--firebase", default="firebase", help="Caminho do Firebase CLI.")
    modo = parser.add_mutually_exclusive_group(required=True)
    modo.add_argument("--regras-sanitizadas", type=Path)
    modo.add_argument("--fixture-referencia", action="store_true")
    args = parser.parse_args()

    firebase = shutil.which(args.firebase)
    if not firebase or not shutil.which("java"):
        parser.error("Disponibilize Firebase CLI e Java antes de executar.")
    regras = args.regras_sanitizadas or args.referencia / "rules.fixture.json"
    print("Entrada: fixture de referência." if args.fixture_referencia else "Entrada: política sanitizada informada.")

    with tempfile.TemporaryDirectory(prefix="seagbh-firebase-homologacao-") as temporario:
        pasta = Path(temporario)
        try:
            _preparar_pasta(pasta, args.referencia, regras)
        except (OSError, ValueError, KeyError, TypeError):
            print("Não foi possível preparar o projeto demo. Confira a referência e o formato das regras.")
            return 1

        chaves = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TMP", "TEMP", "TMPDIR")
        env = {chave: os.environ[chave] for chave in chaves if chave in os.environ}
        env.update({
            "XDG_CONFIG_HOME": str(pasta / "cli-config"),
            "FIREBASE_EMULATORS_PATH": str(pasta / "emuladores"),
            "NO_UPDATE_NOTIFIER": "1",
            "CI": "true",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
            "SEAGBH_TEST_PYTHON": sys.executable,
        })
        python = '"%SEAGBH_TEST_PYTHON%"' if os.name == "nt" else '"$SEAGBH_TEST_PYTHON"'
        try:
            resultado = subprocess.run(
                [
                    str(Path(firebase).resolve()), "--non-interactive", "--project", PROJETO,
                    "--config", "firebase.json", "emulators:exec", "--only", "auth,database",
                    f"{python} -B firebase_homologacao.py --executar-contratos",
                ],
                cwd=pasta, env=env, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
        except OSError:
            print("Não foi possível iniciar o Firebase CLI.")
            return 1
    aprovado = _exibir_resultados(resultado.stdout)
    if resultado.returncode or not aprovado:
        print("Política não validada. Confira os contratos reprovados e a disponibilidade dos emuladores.")
        return 1
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--executar-contratos"]:
        sys.exit(_executar_contratos())
    sys.exit(main())
