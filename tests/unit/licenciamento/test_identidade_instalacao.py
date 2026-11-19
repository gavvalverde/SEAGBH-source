"""Testes offline da identidade local, sem Firebase/Supabase/Windows APIs."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from src.core.identidade_instalacao import (
    IdentidadeInstalacaoInvalida,
    carregar_ou_criar_id_instalacao,
    ler_id_instalacao,
)


class IdentidadeInstalacaoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.arquivo = Path(self._tmp.name) / "dispositivo.id"

    def test_primeiro_uso_gera_uuid4_e_reutiliza(self):
        primeiro = carregar_ou_criar_id_instalacao(self.arquivo)
        self.assertIsInstance(primeiro, UUID)
        self.assertEqual(primeiro.version, 4)
        self.assertEqual(primeiro, carregar_ou_criar_id_instalacao(self.arquivo))
        self.assertEqual(primeiro, ler_id_instalacao(self.arquivo))

    def test_duas_instalacoes_distintas_geram_ids_distintos(self):
        um = carregar_ou_criar_id_instalacao(self.arquivo)
        dois = carregar_ou_criar_id_instalacao(
            Path(self._tmp.name) / "outro-dispositivo.id")
        self.assertNotEqual(um, dois)

    def test_multiplas_inicializacoes_concorrentes_compartilham_uuid(self):
        with ThreadPoolExecutor(max_workers=12) as executor:
            ids = list(executor.map(
                carregar_ou_criar_id_instalacao, [self.arquivo] * 32))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(ler_id_instalacao(self.arquivo), ids[0])
        temporarios = list(Path(self._tmp.name).glob(".seagbh-device-*"))
        self.assertEqual(temporarios, [])

    def test_armazena_apenas_uuid_canonico_sem_segredos(self):
        identidade = carregar_ou_criar_id_instalacao(self.arquivo)
        self.assertEqual(self.arquivo.read_bytes(),
                         (str(identidade) + "\n").encode("ascii"))

    def test_identidade_corrompida_nao_gera_nova_ativacao(self):
        for invalida in (b"", b"nao-uuid\n", b"00000000-0000-0000-0000-000000000000\n",
                         b"x" * 129, b"\xff", b" " + str(uuid4()).encode()):
            with self.subTest(invalida=invalida[:20]):
                self.arquivo.write_bytes(invalida)
                with self.assertRaises(IdentidadeInstalacaoInvalida):
                    carregar_ou_criar_id_instalacao(self.arquivo)
                self.assertEqual(self.arquivo.read_bytes(), invalida)

    def test_rejeita_uuid_v1_em_vez_de_aceitar_instalacao_invalida(self):
        self.arquivo.write_text(
            "00000000-0000-1000-8000-000000000001\n", encoding="ascii")
        with self.assertRaises(IdentidadeInstalacaoInvalida):
            ler_id_instalacao(self.arquivo)

    def test_rejeita_formato_nao_canonico(self):
        valor = uuid4()
        for texto in (str(valor).upper(), valor.hex, str(valor) + "\n\n"):
            with self.subTest(texto=texto):
                self.arquivo.write_text(texto, encoding="ascii")
                with self.assertRaises(IdentidadeInstalacaoInvalida):
                    carregar_ou_criar_id_instalacao(self.arquivo)

    def test_diretorio_nao_existente_nao_e_criado_automaticamente(self):
        caminho = self.arquivo / "nao-existe" / "device.id"
        with self.assertRaises(FileNotFoundError):
            carregar_ou_criar_id_instalacao(caminho)
        self.assertFalse(caminho.parent.exists())

    def test_arquivo_regular_e_obrigatorio(self):
        self.arquivo.mkdir()
        with self.assertRaises(IdentidadeInstalacaoInvalida):
            carregar_ou_criar_id_instalacao(self.arquivo)

    def test_sistema_sem_hardlink_falha_sem_criar_dispositivo(self):
        with patch("src.core.identidade_instalacao.os.link",
                   side_effect=OSError("hard links unavailable")):
            with self.assertRaises(OSError):
                carregar_ou_criar_id_instalacao(self.arquivo)
        self.assertFalse(self.arquivo.exists())
        self.assertFalse(list(Path(self._tmp.name).glob(".seagbh-device-*")))

    def test_arquivo_valido_persistente_entre_processos(self):
        original = uuid4()
        self.arquivo.write_text(str(original) + "\n", encoding="ascii")
        self.assertEqual(carregar_ou_criar_id_instalacao(self.arquivo), original)


if __name__ == "__main__":
    unittest.main()
