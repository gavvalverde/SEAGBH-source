"""Testes sintéticos da ferramenta passiva de diagnóstico de manutenção."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.inspecionar_manutencao_sqlite import inspecionar


class InspecaoManutencaoSQLite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="seagbh-inspecao-")
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name).resolve()
        self.banco = self.raiz / "sintetico.db"
        self.banco.write_bytes(b"fixture-segura-sem-conexao")

    def test_inspecao_passiva_nunca_autoriza_execucao(self):
        original = self.banco.read_bytes()
        with patch("sqlite3.connect", side_effect=AssertionError("SQLite aberto")), \
             patch("os.replace", side_effect=AssertionError("replace")), \
             patch("os.unlink", side_effect=AssertionError("unlink")), \
             patch("pathlib.Path.open", side_effect=AssertionError("arquivo aberto")):
            resposta = inspecionar(self.banco)
        self.assertEqual(resposta["resultado"], "inspecionado")
        self.assertFalse(resposta["restauracao_autorizada"])
        self.assertFalse(resposta["coordenacao_adquirida"])
        self.assertEqual(resposta["exclusividade"], "nao_comprovada")
        self.assertEqual(self.banco.read_bytes(), original)
        self.assertEqual(list(self.raiz.iterdir()), [self.banco])
        self.assertNotIn(str(self.raiz), json.dumps(resposta))

    def test_wal_presente_gera_alerta_e_nunca_e_removido(self):
        wal = Path(str(self.banco) + "-wal")
        wal.write_bytes(b"wal-sintetico")
        resposta = inspecionar(self.banco)
        self.assertEqual(resposta["resultado"], "inspecionado_com_avisos")
        self.assertEqual(resposta["auxiliares"]["wal"], "regular")
        self.assertIn("wal", resposta["alertas"])
        self.assertEqual(wal.read_bytes(), b"wal-sintetico")

    def test_coordenacao_existente_nao_prova_exclusividade(self):
        marca = self.raiz / "sintetico.db.seagbh-access-v1.lock"
        marca.write_bytes(b"")
        resposta = inspecionar(self.banco)
        self.assertEqual(resposta["auxiliares"]["arquivo_coordenacao"], "regular")
        self.assertFalse(resposta["coordenacao_adquirida"])
        self.assertEqual(resposta["exclusividade"], "nao_comprovada")

    def test_link_auxiliar_e_recusado_sem_apaga_lo(self):
        wal = Path(str(self.banco) + "-wal")
        try:
            wal.symlink_to(self.banco)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink indisponível nesta plataforma")
        resposta = inspecionar(self.banco)
        self.assertEqual(resposta["resultado"], "recusado")
        self.assertEqual(resposta["auxiliares"]["wal"], "link_ou_reparse")
        self.assertTrue(wal.is_symlink())

    def test_destino_ausente_rejeitado_sem_criar_arquivo(self):
        ausente = self.raiz / "ausente.db"
        resposta = inspecionar(ausente)
        self.assertEqual(resposta["resultado"], "recusado")
        self.assertEqual(resposta["destino"], "ausente")
        self.assertFalse(ausente.exists())

    def test_caminho_relativo_rejeitado_sem_exposicao(self):
        resposta = inspecionar("sintetico.db")
        self.assertEqual(resposta["resultado"], "recusado")
        self.assertFalse(resposta["restauracao_autorizada"])
        self.assertNotIn(str(self.raiz), json.dumps(resposta))

    def test_link_simbolico_rejeitado_quando_disponivel(self):
        link = self.raiz / "alias.db"
        try:
            link.symlink_to(self.banco)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink indisponível nesta plataforma")
        resposta = inspecionar(link)
        self.assertEqual(resposta["resultado"], "recusado")
        self.assertFalse(resposta["restauracao_autorizada"])

    def test_cli_em_subprocesso_sem_caminho_no_json(self):
        raiz_repo = Path(__file__).resolve().parents[2]
        resultado = subprocess.run(
            [sys.executable, "-B", str(raiz_repo / "tools/inspecionar_manutencao_sqlite.py"),
             "--destino", str(self.banco)], cwd=raiz_repo,
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        resposta = json.loads(resultado.stdout)
        self.assertFalse(resposta["restauracao_autorizada"])
        self.assertNotIn(str(self.banco), resultado.stdout)
        self.assertEqual(resultado.stderr, "")


if __name__ == "__main__":
    unittest.main()
