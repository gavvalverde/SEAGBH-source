"""Regressões do histórico privado com dados sintéticos de ponta a ponta."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
from time import monotonic
import unittest
from unittest.mock import patch

from src.core import historico_licencas as modulo
from src.core.historico_licencas import ErroHistorico, HistoricoLicencas


def registro(lid="000000000001", **campos):
    return dict({
        "id": lid, "cliente": "Cliente sintético", "plano": "vitalicio",
        "emissao": "2026-11-23", "expira": "9999-12-31", "chave": "chave-sintetica",
        "status": "ativa",
    }, **campos)


def json_bytes(registros):
    return json.dumps(registros, ensure_ascii=False, indent=2).encode("utf-8")


class HistoricoLicencasTest(unittest.TestCase):
    def setUp(self):
        # A área temporária padrão pode conceder acesso a outras identidades no
        # Windows. O diretório sintético no perfil não usa o destino operacional.
        base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else None
        self.tmp = self.enterContext(TemporaryDirectory(prefix="seagbh-historico-test-", dir=base))
        self.raiz = Path(self.tmp)
        self.pasta = self.raiz / "privado # sintético"
        self.historico = HistoricoLicencas(self.pasta)
        self.ativo = registro(extra={"categorias": ["um", "dois"], "numero": 12.25})
        self.excluido = registro("000000000002", status="revogada")

    def assert_erro(self, codigo, acao, *args, **kwargs):
        with self.assertRaises(ErroHistorico) as contexto:
            acao(*args, **kwargs)
        self.assertEqual(contexto.exception.codigo, codigo)

    def migrar(self):
        return self.historico.migrar_fixtures(ativos=json_bytes([self.ativo]), excluidos=json_bytes([self.excluido]))

    def sql(self, nome, comando, parametros=()):
        with closing(sqlite3.connect(self.pasta / nome)) as conexao, conexao:
            return conexao.execute(comando, parametros).fetchall()

    def test_ausencia_nao_cria_diretorio_ou_confunde_com_historico_vazio(self):
        self.assert_erro("armazenamento_ausente", self.historico.ler_ativos)
        self.assertFalse(self.pasta.exists())
        resultado = self.historico.migrar_fixtures(ativos=b"[]", excluidos=b"[]")
        self.assertEqual((resultado.ativos, resultado.excluidos), (0, 0))
        self.assertEqual(self.historico.ler_ativos(), [])
        self.assertEqual(self.historico.ler_excluidos(), [])
        self.assertEqual(self.sql("historico.sqlite3", "PRAGMA user_version"), [(1,)])

    def test_migracao_preserva_origens_ordem_campos_e_copia_exata(self):
        outros = [registro("legado-02", cliente="Outro sintético"), self.ativo]
        original_a = self.raiz / "fixture-a.json"
        original_b = self.raiz / "fixture-b.json"
        a = b"\xef\xbb\xbf" + json_bytes(outros)
        b = json_bytes([self.excluido])
        original_a.write_bytes(a)
        original_b.write_bytes(b)
        resultado = self.historico.migrar_fixtures(ativos=original_a.read_bytes(), excluidos=original_b.read_bytes())
        self.assertEqual((resultado.ativos, resultado.excluidos, resultado.ja_importado), (2, 1, False))
        self.assertEqual(self.historico.ler_ativos(), outros)
        self.assertEqual(self.historico.ler_excluidos(), [self.excluido])
        self.assertEqual(self.sql("originais.sqlite3", "SELECT ativos, excluidos FROM originais"), [(a, b)])
        self.assertEqual((original_a.read_bytes(), original_b.read_bytes()), (a, b))

    def test_migracao_recusa_invalido_duplicidade_e_estado_contraditorio(self):
        casos = (
            ("origem_invalida", b"{", b"[]"),
            ("origem_invalida", b"[]", b"{}"),
            ("origem_invalida", b'[{"x":0,"x":1}]', b"[]"),
            ("origem_invalida", b"[1e999]", b"[]"),
            ("origem_invalida", b"[0.100000000000000001]", b"[]"),
            ("registro_invalido", b"[{}]", b"[]"),
            ("registro_invalido", json_bytes([registro(expira="2026-02-30")]), b"[]"),
            ("registro_invalido", json_bytes([registro("../id")]), b"[]"),
            ("id_duplicado", json_bytes([self.ativo, self.ativo]), b"[]"),
            ("id_duplicado", json_bytes([self.ativo]), json_bytes([self.ativo])),
            ("estado_contraditorio", b"[]", json_bytes([registro(status="excluida")])),
        )
        for codigo, a, b in casos:
            with self.subTest(codigo=codigo):
                self.assert_erro(codigo, self.historico.migrar_fixtures, ativos=a, excluidos=b)
                self.assertFalse(self.pasta.exists())

    def test_repetir_importacao_nao_reverte_alteracoes_e_recusa_outras_origens(self):
        self.migrar()
        self.historico.mover(self.ativo, para_excluidos=True)
        self.assertTrue(self.migrar().ja_importado)
        self.assertEqual(self.historico.ler_ativos(), [])
        self.assertEqual(self.historico.ler_excluidos(), [self.excluido, self.ativo])
        self.assert_erro("destino_em_uso", self.historico.migrar_fixtures, ativos=b"[]", excluidos=b"[]")
        self.assertEqual(self.historico.ler_excluidos(), [self.excluido, self.ativo])
        self.assertTrue(self.historico.retomar_fixtures_da_copia().ja_importado)

    def test_importacao_nao_substitui_outro_banco(self):
        modulo.PastaHistorico(self.pasta, criar=True)
        # O SQLite cria o arquivo com o modo do processo. Restrinja o arquivo
        # sintético antes de testar a recusa por identidade de esquema.
        caminho = self.pasta / "historico.sqlite3"
        fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        self.sql("historico.sqlite3", "CREATE TABLE anterior (id INTEGER)")
        self.assert_erro("esquema_incompativel", self.migrar)
        self.assertEqual(self.sql("historico.sqlite3", "SELECT name FROM sqlite_schema"), [("anterior",)])
        self.assertFalse((self.pasta / "originais.sqlite3").exists())
        self.sql("historico.sqlite3", "DROP TABLE anterior")
        self.assert_erro("esquema_incompativel", self.migrar)
        self.assertEqual(self.sql("historico.sqlite3", "PRAGMA application_id"), [(0,)])

    def test_copia_original_sozinha_nao_recria_historico_em_uso(self):
        self.migrar()
        self.historico.atualizar(self.ativo, status="revogada")
        principal = self.pasta / "historico.sqlite3"
        principal.rename(self.pasta / "banco-sintetico-deslocado.sqlite3")
        self.assert_erro("principal_ausente_com_copia", self.historico.retomar_fixtures_da_copia)
        self.assert_erro("principal_ausente_com_copia", self.migrar)
        self.assertFalse(principal.exists())

    def test_gravacao_interrompida_por_erro_sqlite_restaura_registro_original(self):
        self.migrar()
        self.sql("historico.sqlite3", """CREATE TRIGGER erro_sintetico BEFORE INSERT ON registros
            WHEN NEW.excluido = 1 BEGIN SELECT RAISE(ABORT, 'falha-sintetica'); END""")
        with self.assertRaisesRegex(ErroHistorico, "^falha_armazenamento$"):
            self.historico.mover(self.ativo, para_excluidos=True)
        self.assertEqual(self.historico.ler_ativos(), [self.ativo])
        self.assertEqual(self.historico.ler_excluidos(), [self.excluido])

    def test_processo_interrompido_entre_copia_e_commit_permite_retomada_da_copia(self):
        codigo = """
import os, sys
from pathlib import Path
from src.core import historico_licencas as modulo
from tests.unit.test_historico_licencas import json_bytes, registro
integridade = modulo._integridade
def interromper(conexao):
    integridade(conexao)
    if conexao.execute('PRAGMA application_id').fetchone() == (modulo._APP_HISTORICO,):
        os._exit(27)
modulo._integridade = interromper
modulo.HistoricoLicencas(Path(sys.argv[1])).migrar_fixtures(
    ativos=json_bytes([registro()]), excluidos=b'[]',
)
"""
        filho = subprocess.run([sys.executable, "-B", "-c", codigo, str(self.pasta)],
                               capture_output=True, timeout=20, check=False)
        self.assertEqual(filho.returncode, 27, filho.stderr)
        with self.assertRaises(ErroHistorico):
            self.historico.ler_ativos()
        self.assert_erro("copia_original_divergente", self.migrar)
        self.assertEqual(self.historico.retomar_fixtures_da_copia().ativos, 1)
        self.assertEqual(self.historico.ler_ativos(), [registro()])
        self.assertTrue(self.historico.retomar_fixtures_da_copia().ja_importado)

    def test_importadores_concorrentes_serializam_uma_unica_importacao(self):
        inicio = Barrier(2)

        def importar(_indice):
            inicio.wait(10)
            return self.migrar().ja_importado

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertCountEqual(list(executor.map(importar, range(2))), [False, True])
        self.assertEqual(self.historico.ler_ativos(), [self.ativo])

    def test_escritores_concorrentes_nao_perdem_dados_ou_duplicam_id(self):
        self.migrar()
        inicio = Barrier(2)

        def arquivar(_indice):
            inicio.wait(10)
            try:
                self.historico.mover(self.ativo, para_excluidos=True)
            except ErroHistorico as erro:
                return erro.codigo
            return "ok"

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertCountEqual(list(executor.map(arquivar, range(2))), ["ok", "registro_alterado"])
        self.assertEqual(self.historico.ler_ativos(), [])
        self.assertEqual(self.historico.ler_excluidos(), [self.excluido, self.ativo])

    def test_editar_com_registro_desatualizado_falha_e_preserva_dados(self):
        self.migrar()
        self.historico.atualizar(self.ativo, status="revogada")
        self.assert_erro("registro_alterado", self.historico.atualizar, self.ativo, expira="2040-01-01")
        self.assert_erro("registro_alterado", self.historico.mover, self.ativo, para_excluidos=True)
        self.assert_erro("id_duplicado", self.historico.adicionar, self.excluido)
        self.assertEqual(self.historico.ler_ativos(), [dict(self.ativo, status="revogada")])

    def test_leitura_recusa_bloqueio_exclusivo_sem_espera_de_escritor(self):
        self.migrar()
        with closing(sqlite3.connect(self.pasta / "historico.sqlite3")) as concorrente:
            concorrente.execute("BEGIN EXCLUSIVE")
            inicio = monotonic()
            self.assert_erro("armazenamento_ocupado", self.historico.ler_ativos)
            self.assertLess(monotonic() - inicio, 3)
        self.assertEqual(self.historico.ler_ativos(), [self.ativo])

    def test_perda_de_permissoes_de_arquivo_e_diretorio_recusa_acesso(self):
        self.migrar()
        caminho = self.pasta / "originais.sqlite3"
        if os.name == "nt":
            def ampliar(alvo):
                subprocess.run(["icacls", str(alvo), "/grant", "*S-1-1-0:(R)"],
                               check=True, capture_output=True, timeout=10)
        else:
            def ampliar(alvo):
                alvo.chmod(alvo.stat().st_mode | 0o044)
        ampliar(caminho)
        self.assert_erro("permissoes_inseguras", self.historico.ler_ativos)
        ampliar(self.pasta)
        self.assert_erro("permissoes_inseguras", self.historico.ler_ativos)

    def test_recusa_banco_corrompido_sem_trata_lo_como_vazio(self):
        self.migrar()
        caminho = self.pasta / "historico.sqlite3"
        caminho.write_bytes(b"banco sintetico danificado")
        self.assert_erro("armazenamento_corrompido", self.historico.ler_ativos)
        self.assert_erro("armazenamento_corrompido", self.historico.retomar_fixtures_da_copia)

    def test_rejeita_caminho_relativo_e_hardlink(self):
        self.assert_erro("caminho_inseguro", HistoricoLicencas, Path("relativo"))
        self.migrar()
        os.link(self.pasta / "historico.sqlite3", self.raiz / "alias-sintetico")
        self.assert_erro("permissoes_inseguras", self.historico.ler_ativos)

    @unittest.skipUnless(sys.platform == "linux", "link simbólico POSIX")
    def test_linux_rejeita_link_simbolico_e_montagem_remota(self):
        self.migrar()
        link = self.raiz / "pasta-redirecionada"
        link.symlink_to(self.pasta, target_is_directory=True)
        self.assert_erro("permissoes_inseguras", HistoricoLicencas(link).ler_ativos)
        montagem = "1 0 0:1 / / rw - nfs exemplo:/fixture rw\n"
        with patch.object(Path, "read_text", return_value=montagem):
            self.assert_erro("permissoes_inseguras", self.historico.ler_ativos)

    def test_falha_ao_gravar_copia_nao_confirma_importacao(self):
        abrir = os.open

        def falhar_copia(caminho, *args, **kwargs):
            if Path(caminho).name == "originais.sqlite3":
                raise OSError("falha sintética")
            return abrir(caminho, *args, **kwargs)

        with patch("src.core.historico_licencas.os.open", side_effect=falhar_copia):
            self.assert_erro("falha_armazenamento", self.migrar)
        with self.assertRaises(ErroHistorico):
            self.historico.ler_ativos()
        self.assertFalse((self.pasta / "originais.sqlite3").exists())
        self.assertEqual(self.migrar().ativos, 1)
