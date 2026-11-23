"""Testes sinteticos do bloqueio de historicos no Git; nenhum JSON real e lido."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools import protecao_historicos_git as verificador
from tools.protecao_historicos_git import (
    LEGADOS_IMUTAVEIS, ErroInventario, arquivo_de_historico, entradas_git, entradas_proibidas,
)


def arvore(*itens):
    return b"".join(
        (f"100644 blob {oid}\t{caminho}".encode("utf-8") + b"\0")
        for caminho, oid in itens
    )


class ProtecaoHistoricosGitTests(unittest.TestCase):
    def test_blobs_legados_originais_sao_tolerados_sem_abrir_arquivos(self):
        entradas = entradas_git(arvore(*LEGADOS_IMUTAVEIS.items()))
        self.assertEqual(entradas_proibidas(entradas), [])

    def test_mudanca_de_blob_legado_e_bloqueada(self):
        entradas = entradas_git(arvore(
            ("tools/historico_chaves.json", "a" * 40),
            ("tools/historico_excluidos.json",
             LEGADOS_IMUTAVEIS["tools/historico_excluidos.json"]),
        ))
        self.assertEqual(entradas_proibidas(entradas), ["tools/historico_chaves.json"])

    def test_novo_historico_ou_copia_versionada_e_bloqueado(self):
        casos = (
            "tools/historico_chaves.json.bak",
            "tools/HISTORICO_EXCLUIDOS.JSON",
            "outro/diretorio/historico_clientes.json",
            "outro/historico.sqlite3",
            "outro/originais.sqlite3-journal",
            "tools/historico_extra.db",
        )
        for caminho in casos:
            with self.subTest(caminho=caminho):
                self.assertTrue(arquivo_de_historico(caminho))
                self.assertEqual(
                    entradas_proibidas(entradas_git(arvore((caminho, "b" * 40)))),
                    [caminho],
                )

    def test_outros_arquivos_nao_sao_bloqueados(self):
        self.assertFalse(arquivo_de_historico("src/core/historico_licencas.py"))
        self.assertFalse(arquivo_de_historico("docs/historico-administrativo-privado.md"))
        self.assertEqual(entradas_proibidas(entradas_git(
            arvore(("src/core/historico_licencas.py", "a" * 40))
        )), [])

    def test_desrastrear_antigos_arquivos_passa_sem_excluir_dados_locais(self):
        self.assertEqual(entradas_proibidas(entradas_git(arvore())), [])

    def test_modo_desconhecido_ou_objeto_nao_blob_nao_autoriza_legado(self):
        caminho = "tools/historico_chaves.json"
        sha = LEGADOS_IMUTAVEIS[caminho]
        raw = f"160000 commit {sha}\t{caminho}\0".encode()
        self.assertEqual(entradas_proibidas(entradas_git(raw)), [caminho])

    def test_metadados_malformados_falham_fechado(self):
        for raw in (b"100644 blob sem_sha\tx\0", b"100644 blob aaaaa\0",
                    b"100644 blob " + b"a" * 40 + b"\t\0"):
            with self.subTest(raw=raw), self.assertRaises(ErroInventario):
                entradas_git(raw)

    def test_erro_git_e_negado_sem_abrir_historicos(self):
        with (
            patch.object(subprocess, "run", side_effect=subprocess.CalledProcessError(1, "git")),
            redirect_stderr(StringIO()),
        ):
            self.assertEqual(verificador.verificar_intervalo("a" * 40, "b" * 40, evento="push"), 2)


class HistoricoDeCommitsTests(unittest.TestCase):
    def setUp(self):
        self.raiz = Path(self.enterContext(TemporaryDirectory(prefix="seagbh-git-fixture-")))
        self.repo = self.raiz / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "--initial-branch=main", "--object-format=sha1", "--template=")
        self.legados = {nome: f'{{"fixture": {indice}}}' for indice, nome in enumerate(LEGADOS_IMUTAVEIS)}
        for nome, texto in self.legados.items():
            self.escrever(nome, texto)
        self.base = self.commit("Base sintetica")
        self.enterContext(patch.object(verificador, "LEGADOS_IMUTAVEIS", {
            nome: self.git("rev-parse", f"{self.base}:{nome}") for nome in self.legados
        }))

    def git(self, *args):
        return subprocess.check_output([
            "git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "-c", "core.hooksPath=.git/sem-hooks", "-c", "gc.auto=0", *args,
        ], cwd=self.repo, stderr=subprocess.PIPE).decode("utf-8").strip()

    def escrever(self, nome, texto="dados sinteticos"):
        arquivo = self.repo / nome
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(texto, encoding="utf-8")

    def commit(self, titulo):
        self.git("add", "-A")
        self.git("commit", "-qm", titulo)
        return self.git("rev-parse", "HEAD")

    def verificar(self, base, head, *, evento="pull_request", repo=None):
        saida, erro = StringIO(), StringIO()
        with redirect_stdout(saida), redirect_stderr(erro):
            status = verificador.verificar_intervalo(base, head, evento=evento, cwd=repo or self.repo)
        return status, saida.getvalue() + erro.getvalue()

    def indisponibilizar_objeto(self, oid):
        objeto = self.repo / ".git" / "objects" / oid[:2] / oid[2:]
        objeto.chmod(0o600)  # O Git grava objetos avulsos como somente leitura.
        objeto.unlink()

    def historico_temporario(self, nome):
        self.escrever(nome, '{"dado":"marcador-sintetico"}')
        insercao = self.commit("Insere fixture temporaria")
        self.git("rm", "-q", "--", nome)
        self.git("commit", "-qm", "Remove fixture temporaria")
        return insercao, self.git("rev-parse", "HEAD")

    def test_pr_recusa_historico_adicionado_e_removido(self):
        nome = "tools/historico_temporario.json"
        insercao, head = self.historico_temporario(nome)
        self.assertTrue(self.git("ls-tree", insercao, "--", nome))
        self.assertEqual(self.git("ls-tree", head, "--", nome), "")
        status, saida = self.verificar(self.base, head)
        self.assertEqual(status, 1)
        self.assertIn("1 de 2 commit(s)", saida)
        self.assertNotIn(nome, saida)
        self.assertNotIn("marcador-sintetico", saida)

    def test_pr_recusa_json_legado_alterado_e_restaurado(self):
        for nome, original in self.legados.items():
            with self.subTest(nome=nome):
                base = self.git("rev-parse", "HEAD")
                self.escrever(nome, '{"fixture":999}')
                self.commit("Altera legado sintetico")
                self.escrever(nome, original)
                head = self.commit("Restaura legado sintetico")
                self.assertEqual(self.git("rev-parse", f"{head}:{nome}"), verificador.LEGADOS_IMUTAVEIS[nome])
                status, saida = self.verificar(base, head)
                self.assertEqual(status, 1)
                self.assertIn("1 de 2 commit(s)", saida)

    def test_pr_limpo_inspeciona_todos_os_commits_com_legados_intactos(self):
        commits = []
        for indice, nome in enumerate(("docs/guia.md", "src/historico_licencas.py", "docs/notas.md")):
            self.escrever(nome)
            commits.append(self.commit(f"Documento ou codigo sintetico {indice}"))
        for oid in verificador.LEGADOS_IMUTAVEIS.values():
            self.indisponibilizar_objeto(oid)
        with patch.object(subprocess, "run", wraps=subprocess.run) as executar:
            status, saida = self.verificar(self.base, commits[-1])
        self.assertEqual(status, 0)
        self.assertIn("OK em 3 commit(s)", saida)
        comandos = [chamada.args[0] for chamada in executar.call_args_list]
        arvores = [cmd[-1] for cmd in comandos if cmd[2] == "ls-tree"]
        self.assertCountEqual(arvores, commits)
        for comando in comandos:
            self.assertEqual(comando[:2], ["git", "--no-replace-objects"])
            self.assertIn(comando[2], ("rev-parse", "cat-file", "merge-base", "rev-list", "ls-tree"))
            if comando[2] == "cat-file":
                self.assertEqual(comando[3], "-t")

    def test_push_inspeciona_intervalo_inteiro(self):
        self.git("rm", "-q", "--", *self.legados)
        self.escrever("docs/guia.md")
        antes = self.commit("Estado antes do push")
        _, head = self.historico_temporario("backup/historico_push.sqlite3")
        resultado = subprocess.run([
            sys.executable, "-B", str(Path(verificador.__file__).resolve()),
            "--evento", "push", "--base", antes, "--head", head,
        ], cwd=self.repo, capture_output=True, timeout=30)
        self.assertEqual(resultado.returncode, 1)
        self.assertIn(b"1 de 2 commit(s)", resultado.stderr)
        self.assertNotIn(b"historico_push", resultado.stderr)

    def test_pr_subtrai_a_base_atual_ao_atualizar_com_merge(self):
        self.git("switch", "-qc", "contribuicao")
        self.escrever("docs/guia.md")
        contribuicao = self.commit("Contribuicao sintetica")
        self.git("switch", "-q", "main")
        _, base_atual = self.historico_temporario("historico_ja_na_base.json")
        self.assertEqual(self.verificar(base_atual, contribuicao)[0], 0)
        self.assertEqual(self.verificar(base_atual, contribuicao, evento="push")[0], 2)
        self.git("switch", "-q", "contribuicao")
        self.git("merge", "--no-ff", "-qm", "Incorpora base atual", "main")
        status, saida = self.verificar(base_atual, self.git("rev-parse", "HEAD"))
        self.assertEqual(status, 0)
        self.assertIn("OK em 2 commit(s)", saida)

    def test_merge_introduzido_inclui_commits_do_segundo_pai(self):
        self.git("switch", "-qc", "ramo-lateral")
        self.historico_temporario("historico_em_ramo_lateral.json")
        self.git("switch", "-q", "main")
        self.escrever("docs/guia.md")
        antes = self.commit("Base para a integracao")
        self.git("merge", "--no-ff", "-qm", "Integra ramo lateral", "ramo-lateral")
        verifica = self.git("rev-parse", "HEAD")
        for evento in ("pull_request", "push"):
            with self.subTest(evento=evento):
                status, saida = self.verificar(antes, verifica, evento=evento)
                self.assertEqual(status, 1)
                self.assertIn("1 de 3 commit(s)", saida)

    def test_excluir_legados_sinteticos_e_permitido(self):
        self.git("rm", "-q", "--", *self.legados)
        self.git("commit", "-qm", "Retira legados sinteticos")
        status, saida = self.verificar(self.base, self.git("rev-parse", "HEAD"))
        self.assertEqual(status, 0)
        self.assertIn("OK em 1 commit(s)", saida)

    def test_referencias_invalidas_e_zero_falham_sem_inspecionar_arvores(self):
        blob = self.git("rev-parse", f"{self.base}:{next(iter(self.legados))}")
        for ref in ("HEAD", "--all", "0" * 40, "f" * 40, blob):
            with self.subTest(ref=ref):
                self.assertEqual(self.verificar(ref, self.base)[0], 2)
                self.assertEqual(self.verificar(self.base, ref)[0], 2)

    def test_clone_raso_falha_mesmo_com_os_dois_oids_presentes(self):
        self.escrever("docs/base.md")
        base = self.commit("Base visivel")
        self.escrever("docs/guia.md")
        head = self.commit("Referencia visivel")
        clone = self.raiz / "raso"
        self.git("-c", "protocol.file.allow=always", "clone", "--depth=2", "-q", self.repo.as_uri(), str(clone))
        for oid in (base, head):
            self.assertEqual(subprocess.check_output(["git", "cat-file", "-t", oid], cwd=clone).strip(), b"commit")
        status, mensagem = self.verificar(base, head, repo=clone)
        self.assertEqual(status, 2)
        self.assertIn("falha ao verificar referencias", mensagem)

    def test_pr_sem_ancestral_comum_falha(self):
        # --orphan elimina a ancestralidade e os arquivos apenas da fixture.
        self.git("switch", "-q", "--orphan", "sem-ancestral")
        self.escrever("docs/guia.md")
        self.assertEqual(self.verificar(self.base, self.commit("Raiz independente"))[0], 2)

    def test_pai_indisponivel_em_repositorio_completo_falha(self):
        self.escrever("docs/guia.md")
        pai = self.commit("Pai sintetico")
        self.escrever("docs/notas.md")
        head = self.commit("Head sintetico")
        self.indisponibilizar_objeto(pai)
        self.assertEqual(self.git("rev-parse", "--is-shallow-repository"), "false")
        self.assertEqual(self.verificar(self.base, head, evento="push")[0], 2)


if __name__ == "__main__":
    unittest.main()
