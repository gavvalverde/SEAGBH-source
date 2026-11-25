"""Contratos dos insumos K70-U1. O spec e o lançador são inspecionados por AST."""

import ast
from hashlib import sha256
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.homologacao_k70_u1 import preparar as insumos


RAIZ = Path(__file__).absolute().parents[2]
PASTA = RAIZ / insumos.PASTA
MODULOS_CORE = {
    "core",
    "core.atualizacao",
    "core._instalador_atualizacao",
    "core._acl_historico_windows",
    "core.versao",
}


def _arvore(nome):
    return ast.parse(nome.read_text(encoding="utf-8"))


def _constante(arvore, nome):
    return next(ast.literal_eval(no.value) for no in arvore.body if isinstance(no, ast.Assign)
                and any(isinstance(alvo, ast.Name) and alvo.id == nome for alvo in no.targets))


def _chamada(arvore, nome):
    return next(no for no in ast.walk(arvore) if isinstance(no, ast.Call)
                and isinstance(no.func, ast.Name) and no.func.id == nome)


class ArvoreSinteticaTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(self.enterContext(TemporaryDirectory(prefix="k70-h2a-")))
        self.anterior = self.base / "anterior"
        insumos.preparar(RAIZ, self.anterior, "anterior")

    def test_prepara_somente_o_inventario_aprovado_em_duas_variantes(self):
        candidata = self.base / "candidata"
        insumos.preparar(RAIZ, candidata, "candidata")
        identidades = []
        for destino in (self.anterior, candidata):
            pacote = insumos.validar_arvore(destino)
            existentes = {p.relative_to(pacote).as_posix() for p in pacote.rglob("*") if p.is_file()}
            self.assertEqual(existentes, set(insumos.FONTES) | {"variante.json"})
            for nome, (origem, digest) in insumos.FONTES.items():
                dados = (pacote / nome).read_bytes()
                self.assertEqual(dados, (RAIZ / origem).read_bytes().replace(b"\r\n", b"\n"))
                self.assertEqual(sha256(dados).hexdigest(), digest)
            identidades.append(json.loads((pacote / "variante.json").read_bytes()))
        self.assertEqual([d["variante"] for d in identidades], ["anterior", "candidata"])
        self.assertNotEqual(identidades[0]["versao"], identidades[1]["versao"])
        lancador = _arvore(PASTA / "lancador.py")
        self.assertEqual({d["produto"] for d in identidades}, {_constante(lancador, "PRODUTO")})
        self.assertEqual({d["variante"]: d["versao"] for d in identidades}, _constante(lancador, "VERSOES"))

    def test_recusa_extras_dentro_e_fora_do_pacote(self):
        for nome in (".env", "pacote/dados.db", "pacote/core/licenca.py",
                     "pacote/historico_chaves.json", "pacote/chave.pem"):
            with self.subTest(nome=nome):
                extra = self.anterior / nome
                extra.write_bytes(b"fixture sem dados reais")
                with self.assertRaisesRegex(ValueError, "entradas ausentes ou extras"):
                    insumos.validar_arvore(self.anterior)
                extra.unlink()
        (self.anterior / "pacote" / "Backups").mkdir()
        with self.assertRaisesRegex(ValueError, "entradas ausentes ou extras"):
            insumos.validar_arvore(self.anterior)

    def test_recusa_conteudo_alterado_com_nome_permitido(self):
        alvo = self.anterior / "pacote" / "core" / "atualizacao.py"
        alvo.write_bytes(b"# fonte de teste fora do manifesto\n")
        with self.assertRaisesRegex(ValueError, "conteúdo fora do inventário"):
            insumos.validar_arvore(self.anterior)

    def test_recusa_variante_e_lock_alterados(self):
        for nome in ("pacote/variante.json", insumos.REQUISITOS):
            with self.subTest(nome=nome):
                alvo = self.anterior / nome
                antes = alvo.read_bytes()
                alvo.write_bytes(antes + b"\n")
                with self.assertRaises(ValueError):
                    insumos.validar_arvore(self.anterior)
                alvo.write_bytes(antes)

    def test_recusa_hardlink_mesmo_com_os_bytes_aprovados(self):
        alvo = self.anterior / "pacote" / "variante.json"
        original = self.base / "fixture.json"
        original.write_bytes(alvo.read_bytes())
        alvo.unlink()
        os.link(original, alvo)
        with self.assertRaisesRegex(ValueError, "tipo de caminho"):
            insumos.validar_arvore(self.anterior)

    def test_recusa_juncao_antes_de_listar_o_diretorio(self):
        pacote = self.anterior / "pacote"
        lstat = Path.lstat
        iterdir = Path.iterdir

        def reparse(caminho, *args, **kwargs):
            if caminho == pacote:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return lstat(caminho, *args, **kwargs)

        def listar(caminho):
            self.assertNotEqual(caminho, pacote)
            return iterdir(caminho)

        with patch.object(Path, "lstat", reparse), patch.object(Path, "iterdir", listar):
            with self.assertRaisesRegex(ValueError, "tipo de caminho"):
                insumos.validar_arvore(self.anterior)

    def test_recusa_link_no_ancestral_antes_de_acessar_os_insumos(self):
        lstat = Path.lstat

        def link(caminho, *args, **kwargs):
            if caminho == self.base:
                return SimpleNamespace(st_mode=stat.S_IFLNK)
            self.assertNotEqual(caminho, self.anterior)
            return lstat(caminho, *args, **kwargs)

        with patch.object(Path, "lstat", link):
            with self.assertRaisesRegex(ValueError, "tipo de caminho"):
                insumos.validar_arvore(self.anterior)

    def test_recusa_destino_relativo_ou_existente(self):
        with self.assertRaisesRegex(ValueError, "destino inválido"):
            insumos.preparar(RAIZ, Path("relativo"), "anterior")
        with self.assertRaises(FileExistsError):
            insumos.preparar(RAIZ, self.anterior, "candidata")
        self.assertEqual(json.loads((self.anterior / "pacote/variante.json").read_bytes())["variante"], "anterior")


class ConfiguracaoEstaticaTest(unittest.TestCase):
    def test_inventario_aponta_apenas_para_as_origens_revisadas(self):
        origens = {nome: origem.as_posix() for nome, (origem, _) in insumos.FONTES.items()}
        self.assertEqual(origens, {
            "lancador.py": "tools/homologacao_k70_u1/lancador.py",
            "core/__init__.py": "src/core/__init__.py",
            "core/atualizacao.py": "src/core/atualizacao.py",
            "core/_instalador_atualizacao.py": "src/core/_instalador_atualizacao.py",
            "core/_acl_historico_windows.py": "src/core/_acl_historico_windows.py",
            "core/versao.py": "src/core/versao.py",
        })

    def test_spec_restringe_a_analise_ao_pacote_e_monta_onefile(self):
        spec = _arvore(PASTA / "homologacao.spec")
        analise = _chamada(spec, "Analysis")
        valores = {kw.arg: kw.value for kw in analise.keywords}
        self.assertEqual(ast.unparse(analise.args[0]), "[str(pacote / 'lancador.py')]")
        self.assertEqual(ast.unparse(valores["pathex"]), "[str(pacote)]")
        self.assertEqual(ast.unparse(valores["datas"]), "[(str(pacote / 'variante.json'), '.')]")
        for nome in ("binaries", "hookspath", "runtime_hooks"):
            self.assertEqual(ast.literal_eval(valores[nome]), [])
        self.assertEqual(ast.literal_eval(valores["hiddenimports"]), ["core._acl_historico_windows"])
        self.assertFalse(any(isinstance(no, ast.Name) and no.id == "COLLECT" for no in ast.walk(spec)))
        fixadas = {kw.arg: ast.literal_eval(kw.value) for kw in _chamada(spec, "EXE").keywords}
        self.assertFalse(fixadas["exclude_binaries"])
        self.assertFalse(fixadas["upx"])
        self.assertEqual([ast.unparse(x) for x in _chamada(spec, "EXE").args[1:4]],
                         ["analise.scripts", "analise.binaries", "analise.datas"])
        self.assertEqual(fixadas["name"], _constante(_arvore(PASTA / "lancador.py"), "PRODUTO"))
        self.assertGreater(analise.lineno, _chamada(spec, "exec").lineno)
        self.assertFalse(any(isinstance(no, ast.Constant) and isinstance(no.value, str)
                             and PureWindowsPath(no.value).is_absolute() for no in ast.walk(spec)))

    def test_spec_pina_o_validador_antes_da_analise(self):
        dados = (PASTA / "preparar.py").read_bytes().replace(b"\r\n", b"\n")
        spec = _arvore(PASTA / "homologacao.spec")
        comparacao = next(no for no in ast.walk(spec) if isinstance(no, ast.Compare)
                          and ast.unparse(no.left) == "sha256(codigo).hexdigest()")
        self.assertEqual(ast.literal_eval(comparacao.comparators[0]), sha256(dados).hexdigest())
        self.assertLess(comparacao.lineno, _chamada(spec, "exec").lineno)
        validacao = next(no for no in ast.walk(spec) if isinstance(no, ast.Call)
                         and ast.unparse(no.func) == "escopo['validar_arvore']")
        self.assertLess(validacao.lineno, _chamada(spec, "Analysis").lineno)

    def test_grafo_so_usa_stdlib_e_os_modulos_autorizados(self):
        spec = _chamada(_arvore(PASTA / "homologacao.spec"), "Analysis")
        excluidos = ast.literal_eval(next(kw.value for kw in spec.keywords if kw.arg == "excludes"))
        self.assertTrue({"core.licenca", "core.firebase_auth", "core.database", "core.historico_licencas",
                         "sqlite3", "ui", "tools", "PyQt6"}.issubset(excluidos))
        esperada = _arvore(RAIZ / "src/core/atualizacao.py")
        consulta = next(no for no in esperada.body if isinstance(no, ast.FunctionDef)
                       and no.name == "verificar_atualizacao")
        for nome, (origem, _) in insumos.FONTES.items():
            for no in ast.walk(_arvore(RAIZ / origem)):
                if isinstance(no, ast.ImportFrom):
                    self.assertEqual(no.level, 0)
                    if no.module == "core":
                        modulos = ["core." + item.name for item in no.names]
                    elif no.module == "core.licenca":
                        # Referência preguiçosa do componente intacto. O spec
                        # exclui a dependência e o ensaio nunca chama a consulta.
                        self.assertEqual(nome, "core/atualizacao.py")
                        self.assertTrue(any(ast.dump(no, include_attributes=True) ==
                                            ast.dump(item, include_attributes=True)
                                            for item in consulta.body if isinstance(item, ast.ImportFrom)))
                        self.assertEqual([item.name for item in no.names], ["FIREBASE_URL"])
                        continue
                    else:
                        modulos = [no.module]
                elif isinstance(no, ast.Import):
                    modulos = [item.name for item in no.names]
                else:
                    continue
                for modulo in modulos:
                    self.assertTrue(modulo in MODULOS_CORE or modulo.split(".")[0] in sys.stdlib_module_names,
                                    f"{nome} importa módulo fora do inventário")

    def test_lancador_preserva_o_despacho_e_evoca_o_instalador_real(self):
        modo = _constante(_arvore(RAIZ / "src/core/_instalador_atualizacao.py"), "MODO_WORKER")
        producao = _arvore(RAIZ / "src/main_qt.py")
        self.assertTrue(any(isinstance(no, ast.Compare) and ast.unparse(no.left) == "sys.argv[1:]"
                            and ast.literal_eval(no.comparators[0]) == [modo] for no in ast.walk(producao)))
        lancador = _arvore(PASTA / "lancador.py")
        self.assertIn("argumentos == [MODO_WORKER]", ast.unparse(lancador))
        self.assertEqual(_chamada(lancador, "executar_worker").args, [])
        chamadas = {ast.unparse(no.func) for no in ast.walk(lancador) if isinstance(no, ast.Call)}
        self.assertIn("atualizacao.aplicar_atualizacao", chamadas)
        self.assertTrue(chamadas.isdisjoint({"atualizacao.verificar_atualizacao", "atualizacao.baixar_atualizacao",
                                            "eval", "exec", "__import__"}))

    def test_lock_conta_com_todas_as_dependencias_fixadas_e_com_hash(self):
        nome = PASTA / insumos.REQUISITOS
        linhas = [linha for linha in nome.read_text(encoding="utf-8").splitlines()
                  if linha and not linha.startswith("#")]
        formato = r"([a-z0-9-]+)==[0-9.]+ --hash=sha256:[0-9a-f]{64}"
        for linha in linhas:
            self.assertRegex(linha, r"^" + formato + r"$")
        pacotes = {"altgraph", "packaging", "pefile", "pyinstaller", "pyinstaller-hooks-contrib",
                   "pywin32-ctypes", "setuptools"}
        self.assertEqual(len(linhas), len(pacotes))
        self.assertEqual({re.fullmatch(formato, linha)[1] for linha in linhas}, pacotes)


if __name__ == "__main__":
    unittest.main()
