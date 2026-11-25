"""K70-U1: integração do fluxo de atualização só com conteúdo inerte."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from core import atualizacao as app
from core import _instalador_atualizacao as instalador


URL = "https://example.invalid/seagbh-inerte"
BINARIO_NOVO = b"somente uma fixture de dados"
BINARIO_ANTIGO = b"executavel anterior simulado"
ESPERADO = sha256(BINARIO_NOVO).hexdigest()
ANTERIOR = sha256(BINARIO_ANTIGO).hexdigest()
MANIFESTO = {"version": "999.0.0", "url": URL, "sha256": ESPERADO}


class Resposta(BytesIO):
    def __init__(self, corpo, tamanho=None):
        super().__init__(corpo)
        self.headers = {"Content-Length": str(len(corpo) if tamanho is None else tamanho)}

    def geturl(self):
        return URL


class AtualizacaoTest(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(self.enterContext(tempfile.TemporaryDirectory()))
        # O usuário do executor Windows pode ser diferente do dono de %TEMP%.
        # Exercitamos criação e DACL da área nova; isolamos os ancestrais do host.
        self.enterContext(patch.object(app, "_validar_ancestrais"))

    def test_manifestos_faltante_vazio_malformado_e_url_insegura(self):
        casos = [dict(MANIFESTO, sha256=valor) for valor in
                 (None, "", 123, False, "g" * 64, "0" * 63, "0" * 65, " " + ESPERADO)]
        casos.extend(({"version": "999.0.0", "url": URL}, dict(MANIFESTO, url="http://example.invalid/")))
        for dados in casos:
            with self.subTest(dados=dados):
                with patch.object(app, "urlopen", return_value=Resposta(json.dumps(dados).encode())), \
                     self.assertRaises(app.ErroAtualizacao) as erro:
                    app.verificar_atualizacao()
                self.assertEqual(erro.exception.codigo, "manifesto")
        with patch.object(app, "urlopen") as rede, self.assertRaises(app.ErroAtualizacao):
            app.baixar_atualizacao(URL, "", pasta=self.pasta)
        rede.assert_not_called()
        self.assertFalse(list(self.pasta.iterdir()))

    def test_consulta_distingue_manifesto_transporte_e_ausencia(self):
        with patch.object(app, "urlopen", side_effect=URLError("fixture")), \
             self.assertRaises(app.ErroAtualizacao) as erro:
            app.verificar_atualizacao()
        self.assertEqual(erro.exception.codigo, "transporte")
        for dados in (None, dict(MANIFESTO, version=app.__version__)):
            with patch.object(app, "urlopen", return_value=Resposta(json.dumps(dados).encode())):
                self.assertIsNone(app.verificar_atualizacao())
        with patch.object(app, "urlopen", return_value=Resposta(b"\xff")), \
             self.assertRaises(app.ErroAtualizacao) as erro:
            app.verificar_atualizacao()
        self.assertEqual(erro.exception.codigo, "manifesto")
        with patch.object(app, "urlopen", return_value=Resposta(
                json.dumps(dict(MANIFESTO, sha256=ESPERADO.upper())).encode())):
            self.assertEqual(app.verificar_atualizacao(), MANIFESTO)

    def test_download_rejeita_mesmo_tamanho_truncamento_e_falha_de_transporte(self):
        for bytes_falhos in (b"x" * len(BINARIO_NOVO), BINARIO_NOVO[:-1]):
            with self.subTest(bytes_falhos=bytes_falhos), patch.object(
                    app, "urlopen", return_value=Resposta(bytes_falhos, len(BINARIO_NOVO))), \
                 self.assertRaises(app.ErroAtualizacao) as erro:
                app.baixar_atualizacao(URL, ESPERADO, pasta=self.pasta)
            self.assertEqual(erro.exception.codigo, "integridade")
            self.assertFalse(list(self.pasta.iterdir()))
        with patch.object(app, "urlopen", side_effect=URLError("fixture")), \
             self.assertRaises(app.ErroAtualizacao) as erro:
            app.baixar_atualizacao(URL, ESPERADO, pasta=self.pasta)
        self.assertEqual(erro.exception.codigo, "transporte")
        self.assertFalse(list(self.pasta.iterdir()))

    def test_concorrencia_cria_areas_independentes_e_limpeza_limitada(self):
        with patch.object(app, "urlopen", side_effect=lambda *_, **__: Resposta(BINARIO_NOVO)):
            with ThreadPoolExecutor(2) as executor:
                a, b = list(executor.map(lambda _: app.baixar_atualizacao(
                    URL, ESPERADO, pasta=self.pasta), range(2)))
        self.assertNotEqual(a.caminho, b.caminho)
        for download in a, b:
            app._validar_area(download.caminho.parent)
            self.assertEqual(download.caminho.read_bytes(), BINARIO_NOVO)
        app.descartar_download(a)
        self.assertFalse(a.caminho.parent.exists())
        self.assertTrue(b.caminho.exists())

    def test_capacidade_recusada_bloqueia_download_sem_acessar_url(self):
        with patch.object(app, "_criar_area", side_effect=PermissionError), \
             patch.object(app, "urlopen") as rede, self.assertRaises(app.ErroAtualizacao) as erro:
            app.baixar_atualizacao(URL, ESPERADO, pasta=self.pasta)
        self.assertEqual(erro.exception.codigo, "armazenamento")
        rede.assert_not_called()


class InstalacaoTest(unittest.TestCase):
    def setUp(self):
        self.raiz = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.alvo = self.raiz / "alvo.bin"
        self.alvo.write_bytes(BINARIO_ANTIGO)
        self.area = self.raiz / "staging"
        self.area.mkdir()
        self.candidato = self.area / "candidato.exe"
        self.candidato.write_bytes(BINARIO_NOVO)
        self.anterior = self.area / "anterior.exe"
        self.lancar = Mock()

    @staticmethod
    def _troca(alvo, candidato, backup):
        alvo.rename(backup)
        candidato.rename(alvo)

    def _instalar(self, troca=None, aguardar=None):
        instalador._instalar(self.area, self.alvo, ESPERADO, ANTERIOR,
                             lancar=self.lancar, substituir=troca or self._troca,
                             aguardar=aguardar or Mock(), tentativas=2)

    def test_mudanca_entre_download_e_instalacao_recusa_troca(self):
        self.candidato.write_bytes(b"a" * len(BINARIO_NOVO))
        troca = Mock()
        with self.assertRaises(app.ErroAtualizacao) as erro:
            self._instalar(troca)
        self.assertEqual(erro.exception.codigo, "integridade")
        troca.assert_not_called()
        self.lancar.assert_not_called()
        self.assertEqual(self.alvo.read_bytes(), BINARIO_ANTIGO)

    def test_revalida_o_snapshot_nas_tentativas_de_executavel_em_uso(self):
        erro_uso = OSError()
        erro_uso.winerror = 32
        troca = Mock(side_effect=erro_uso)
        with self.assertRaises(app.ErroAtualizacao) as erro:
            self._instalar(troca, aguardar=lambda _: self.candidato.write_bytes(b"mudou"))
        self.assertEqual(erro.exception.codigo, "integridade")
        troca.assert_called_once()
        self.lancar.assert_not_called()
        self.assertEqual(self.alvo.read_bytes(), BINARIO_ANTIGO)

    def test_instalacao_nao_substitui_simultaneamente_e_preserva_backup(self):
        iniciou, prosseguir = Event(), Event()

        def troca(*args):
            iniciou.set()
            if not prosseguir.wait(5):
                raise RuntimeError("timeout fixture")
            self._troca(*args)

        with ThreadPoolExecutor(1) as executor:
            futuro = executor.submit(self._instalar, troca)
            try:
                self.assertTrue(iniciou.wait(5))
                with self.assertRaises(app.ErroAtualizacao) as erro:
                    self._instalar()
                self.assertEqual(erro.exception.codigo, "em_andamento")
            finally:
                prosseguir.set()
            futuro.result(timeout=5)
        self.assertEqual(self.alvo.read_bytes(), BINARIO_NOVO)
        self.assertEqual(self.anterior.read_bytes(), BINARIO_ANTIGO)
        self.lancar.assert_called_once_with(self.alvo, "--updated")

    def test_falha_de_troca_com_backup_restaura_so_alvo_ausente(self):
        for concorrente in (None, b"alteracao independente"):
            def troca(alvo, candidato, backup):
                alvo.rename(backup)
                if concorrente is not None:
                    alvo.write_bytes(concorrente)
                raise OSError("troca parcial fixture")

            with self.subTest(concorrente=concorrente), self.assertRaises(app.ErroAtualizacao):
                self._instalar(troca)
            self.assertEqual(self.alvo.read_bytes(), concorrente or BINARIO_ANTIGO)
            self.assertEqual(self.anterior.read_bytes(), BINARIO_ANTIGO)
            self.anterior.unlink()
        self.lancar.assert_not_called()

    def test_reinicio_falho_preserva_executavel_anterior_para_recuperacao(self):
        self.lancar.side_effect = OSError
        with self.assertRaises(app.ErroAtualizacao) as erro:
            self._instalar()
        self.assertEqual(erro.exception.codigo, "reinicio")
        self.assertEqual(self.alvo.read_bytes(), BINARIO_NOVO)
        self.assertEqual(self.anterior.read_bytes(), BINARIO_ANTIGO)

    def test_capacidade_sem_binario_congelado_nao_agenda(self):
        with self.assertRaises(app.ErroAtualizacao) as erro:
            app.aplicar_atualizacao(app.Download(self.candidato, ESPERADO))
        self.assertEqual(erro.exception.codigo, "plataforma")

    @unittest.skipUnless(os.name == "nt", "Handle exclusivo de remoção só no Windows")
    def test_replacefile_windows_aguarda_liberacao_sem_executar_arquivo(self):
        import ctypes
        from ctypes.wintypes import DWORD, HANDLE, LPCWSTR, LPVOID
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        criar = kernel.CreateFileW
        criar.argtypes = [LPCWSTR, DWORD, DWORD, LPVOID, DWORD, DWORD, HANDLE]
        criar.restype = HANDLE
        kernel.CloseHandle.argtypes = [HANDLE]
        kernel.CloseHandle.restype = ctypes.c_int
        # Permite leitura, sem FILE_SHARE_DELETE: imita a retenção do nome.
        handle = criar(str(self.alvo), 0x80000000, 1, None, 3, 0x80, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        liberou = []

        def aguardar(_):
            self.assertEqual(self.alvo.read_bytes(), BINARIO_ANTIGO)
            self.assertTrue(kernel.CloseHandle(handle))
            liberou.append(True)

        try:
            self._instalar(instalador._substituir, aguardar=aguardar)
        finally:
            if not liberou:
                kernel.CloseHandle(handle)
        self.assertEqual(liberou, [True])
        self.assertEqual(self.anterior.read_bytes(), BINARIO_ANTIGO)
        self.assertEqual(self.alvo.read_bytes(), BINARIO_NOVO)
        self.lancar.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "ReplaceFileW disponível só no Windows")
    def test_replacefile_real_windows_com_arquivos_inertes_sem_lancar(self):
        self._instalar(instalador._substituir)
        self.assertEqual(self.alvo.read_bytes(), BINARIO_NOVO)
        self.assertEqual(self.anterior.read_bytes(), BINARIO_ANTIGO)
        self.lancar.assert_called_once_with(self.alvo, "--updated")


if __name__ == "__main__":
    unittest.main()
