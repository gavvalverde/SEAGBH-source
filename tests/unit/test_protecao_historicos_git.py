"""Testes sinteticos do bloqueio de historicos no Git; nenhum JSON real e lido."""

import subprocess
import unittest
from unittest.mock import patch

from tools.protecao_historicos_git import (
    LEGADOS_IMUTAVEIS, ErroInventario, arquivo_de_historico,
    entradas_git, entradas_proibidas, verificar_head,
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

    def test_execucao_consulta_apenas_arvore_git(self):
        sucesso = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=arvore(*LEGADOS_IMUTAVEIS.items())
        )
        with patch(
            "tools.protecao_historicos_git.subprocess.run", return_value=sucesso
        ) as executar:
            self.assertEqual(verificar_head(), 0)
        self.assertEqual(
            executar.call_args.args[0],
            ["git", "ls-tree", "-r", "-z", "--full-tree", "HEAD"],
        )

    def test_erro_git_e_negado_sem_abrir_historicos(self):
        with patch(
            "tools.protecao_historicos_git.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            self.assertEqual(verificar_head(), 2)


if __name__ == "__main__":
    unittest.main()
