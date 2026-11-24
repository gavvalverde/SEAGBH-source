"""Pré-validação e restauração experimental offline com bancos sintéticos."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, ExitStack
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.core import backup_sqlite as backup
from src.core import executor_restauracao_sqlite as executor
from src.core import restauracao_sqlite as servico
from src.core.barreira_sqlite import ErroBarreira, adquirir_uso_operacional
from src.core.conexao_sqlite_protegida import abrir_conexao_operacional
from src.core.database import Database
from tests.unit.test_backup_sqlite import DDL

DDL_MINIMO = """
CREATE TABLE equipamentos(id INTEGER PRIMARY KEY,codigo_barras TEXT,nome TEXT,data_cadastro TEXT);
CREATE TABLE eventos(id INTEGER PRIMARY KEY,nome_evento TEXT,responsavel TEXT,data_inicio TEXT,data_fim TEXT,status TEXT);
CREATE TABLE movimentacoes(id INTEGER PRIMARY KEY,evento_id INTEGER REFERENCES eventos(id),
 equipamento_id INTEGER REFERENCES equipamentos(id),data_movimentacao TEXT,tipo TEXT);
CREATE TABLE manutencao(id INTEGER PRIMARY KEY,equipamento_id INTEGER REFERENCES equipamentos(id),
 data_saida TEXT,local_manutencao TEXT,status TEXT);
CREATE TABLE auditoria(id INTEGER PRIMARY KEY,tabela TEXT,item_id INTEGER,acao TEXT,data TEXT);
"""


def sha(caminho):
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


class RestauracaoSQLite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="seagbh-preflight-")
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name).resolve()
        self.pasta = self.raiz / "backups"
        self.pasta.mkdir()
        self.resgate = self.raiz / "resgate"
        self.fonte = self.raiz / "fonte.db"
        self.destino = self.raiz / "operacional-sintetico.db"
        self.banco(self.fonte)
        self.banco(self.destino)
        self.candidato = backup.criar_backup(backend="sqlite", origem=self.fonte,
                                             pasta=self.pasta, manter=None).caminho

    def banco(self, caminho, ddl=DDL):
        with closing(sqlite3.connect(caminho)) as conn:
            conn.executescript(ddl)
            conn.commit()
        return caminho

    def argumentos(self, **alteracoes):
        return dict(dict(backend="sqlite", backup=self.candidato, destino=self.destino,
                         pasta_backups=self.pasta, pasta_resgate=self.resgate), **alteracoes)

    def planejar(self, **alteracoes):
        return servico.planejar_restauracao(**self.argumentos(**alteracoes))

    def inspecionar(self, **alteracoes):
        return servico.inspecionar_destino_restauracao(**self.argumentos(**alteracoes))

    def validar(self, caminho=None, **kwargs):
        return servico.validar_candidato_restauracao(backend="sqlite", backup=caminho or self.candidato, **kwargs)

    def estado(self):
        return {str(p.relative_to(self.raiz)): (p.stat().st_size, p.stat().st_mtime_ns, sha(p))
                for p in self.raiz.rglob("*") if p.is_file()}

    def erro(self, codigo, chamada):
        with self.assertRaises(servico.ErroRestauracao) as contexto:
            chamada()
        self.assertEqual(contexto.exception.codigo, codigo)
        return contexto.exception

    def test_backup_atual_reutiliza_validador_e_separa_compatibilidade(self):
        with patch.object(servico, "validar_backup", wraps=backup.validar_backup) as validar:
            resultado = self.validar()
        validar.assert_called_once()
        self.assertEqual(resultado.validacao.sha256, sha(self.candidato))
        self.assertEqual(resultado.validacao.tamanho, self.candidato.stat().st_size)
        self.assertTrue(resultado.validacao.integridade_fisica)
        self.assertTrue(resultado.validacao.integridade_referencial)
        self.assertTrue(resultado.validacao.esquema_reconhecido)
        self.assertTrue(resultado.validacao.independente)
        self.assertEqual(resultado.compatibilidade, servico.Compatibilidade.NAO_COMPROVADA)

    def test_plano_sem_riscos_detectados_ainda_exige_compatibilidade_e_exclusividade(self):
        plano = self.planejar()
        self.assertEqual(plano.estado_candidato, servico.EstadoCandidato.VALIDADO)
        self.assertIsNone(plano.falha_candidato)
        self.assertEqual(plano.inspecao_destino.impedimentos, ())
        self.assertEqual(plano.exclusividade, servico.Exclusividade.NAO_COMPROVADA)
        self.assertEqual(plano.inspecao_destino.exclusividade, servico.Exclusividade.NAO_COMPROVADA)
        self.assertIn("compatibilidade_nao_comprovada", plano.impedimentos)
        self.assertIn("exclusividade_nao_comprovada", plano.impedimentos)
        self.assertIn("plano_nao_autoriza_restauracao", plano.avisos)
        self.assertIn("revalidar_caminhos_identidades_hashes_e_condicoes", plano.etapas_pendentes)
        self.assertFalse(hasattr(plano, "seguro_para_restaurar"))
        with self.assertRaises(FrozenInstanceError):
            plano.exclusividade = True
        self.assertFalse(self.resgate.exists())

    def test_estrutura_minima_reconhecida_pode_ser_incompativel(self):
        minimo = self.banco(self.raiz / "minimo.db", DDL_MINIMO)
        resultado = self.validar(minimo)
        self.assertTrue(resultado.validacao.esquema_reconhecido)
        self.assertEqual(resultado.compatibilidade, servico.Compatibilidade.INCOMPATIVEL)
        self.assertIn("eventos.local_evento", resultado.colunas_ausentes)
        self.assertIn("eventos.notas", resultado.colunas_ausentes)
        plano = self.planejar(backup=minimo)
        self.assertEqual(plano.estado_candidato, servico.EstadoCandidato.VALIDADO)
        self.assertIn("colunas_da_aplicacao_ausentes", plano.impedimentos)

    def test_colunas_de_migracao_ausentes_nao_sao_criadas(self):
        with closing(sqlite3.connect(self.candidato)) as conn:
            conn.execute("ALTER TABLE eventos DROP COLUMN notas")
            conn.commit()
        antes = self.estado()
        resultado = self.validar()
        self.assertEqual(resultado.colunas_ausentes, ("eventos.notas",))
        self.assertEqual(resultado.compatibilidade, servico.Compatibilidade.INCOMPATIVEL)
        self.assertEqual(self.estado(), antes)

    def test_auditoria_sem_usuario_mantem_integridade_e_compatibilidade_nao_comprovada(self):
        with closing(sqlite3.connect(self.candidato)) as conn:
            conn.execute("ALTER TABLE auditoria DROP COLUMN usuario")
            conn.commit()
        antes = self.estado()
        resultado = self.validar()
        self.assertTrue(resultado.validacao.integridade_fisica)
        self.assertTrue(resultado.validacao.integridade_referencial)
        self.assertTrue(resultado.validacao.esquema_reconhecido)
        self.assertEqual(resultado.colunas_ausentes, ())
        self.assertEqual(resultado.compatibilidade, servico.Compatibilidade.NAO_COMPROVADA)
        plano = self.planejar()
        self.assertEqual(plano.estado_candidato, servico.EstadoCandidato.VALIDADO)
        self.assertIn("compatibilidade_nao_comprovada", plano.impedimentos)
        self.assertNotIn("colunas_da_aplicacao_ausentes", plano.impedimentos)
        self.assertEqual(self.estado(), antes)
        with closing(sqlite3.connect(self.candidato)) as conn:
            self.assertNotIn("usuario", {r[1] for r in conn.execute("PRAGMA table_info(auditoria)")})

    def test_tabelas_adicionais_e_conteudo_sao_preservados(self):
        antes = self.estado()
        resultado = self.validar()
        self.assertEqual(resultado.tabelas_adicionais, ("futuro",))
        self.assertIn("tabelas_adicionais_nao_avaliadas", resultado.avisos)
        self.assertEqual(self.estado(), antes)
        with closing(sqlite3.connect(self.candidato)) as conn:
            self.assertEqual(conn.execute("SELECT hex(payload) FROM futuro").fetchall(), [("010203",)])

    def test_user_version_nao_inventa_contrato_de_compatibilidade(self):
        with closing(sqlite3.connect(self.candidato)) as conn:
            conn.execute("PRAGMA user_version=123")
        antes = self.estado()
        self.assertEqual(self.validar().compatibilidade, servico.Compatibilidade.NAO_COMPROVADA)
        self.assertEqual(self.estado(), antes)

    def test_esquema_desconhecido_e_diferenciado_de_compatibilidade(self):
        outro = self.banco(self.raiz / "outro.db", "CREATE TABLE outro(id INTEGER PRIMARY KEY)")
        self.erro("backup_esquema_nao_reconhecido", lambda: self.validar(outro))
        plano = self.planejar(backup=outro)
        self.assertEqual(plano.estado_candidato, servico.EstadoCandidato.RECUSADO)
        self.assertIsNone(plano.candidato)
        self.assertEqual(plano.falha_candidato.codigo, "backup_esquema_nao_reconhecido")

    def test_arquivo_corrompido_e_recusado(self):
        invalido = self.raiz / "invalido.db"
        invalido.write_bytes(b"nao e sqlite")
        self.erro("backup_arquivo_nao_independente", lambda: self.validar(invalido))

    def test_pagina_corrompida_e_recusada(self):
        with self.candidato.open("r+b") as arquivo:
            arquivo.seek(100)
            arquivo.write(b"\xff" * 100)
        with self.assertRaises(servico.ErroRestauracao) as contexto:
            self.validar()
        self.assertIn(contexto.exception.codigo, {"backup_integridade_fisica", "backup_sqlite_invalido"})

    def test_integridade_referencial_invalida_e_recusada(self):
        with closing(sqlite3.connect(self.candidato)) as conn:
            conn.execute("UPDATE movimentacoes SET evento_id=999")
            conn.commit()
        self.erro("backup_integridade_referencial", self.validar)

    def test_ausencia_da_origem_nao_cria_arquivo(self):
        ausente = self.raiz / "ausente.db"
        plano = self.planejar(backup=ausente)
        self.assertEqual(plano.estado_candidato, servico.EstadoCandidato.RECUSADO)
        self.assertEqual(plano.falha_candidato.codigo, "arquivo_ausente")
        self.assertFalse(ausente.exists())

    def test_ausencia_do_destino_e_do_diretorio_nao_cria_nada(self):
        destino = self.raiz / "inexistente" / "banco.db"
        plano = self.planejar(destino=destino)
        self.assertIn("destino_ausente", plano.impedimentos)
        self.assertIn("pasta_destino_ausente", plano.impedimentos)
        self.assertFalse(destino.parent.exists())

    def test_destino_diretorio_e_recusado_sem_abrir_sqlite(self):
        self.assertIn("destino_nao_regular", self.inspecionar(destino=self.raiz).impedimentos)

    def test_candidato_nao_regular_e_recusado_antes_da_leitura(self):
        with patch.object(Path, "open", side_effect=AssertionError("leitura proibida")) as abrir:
            self.erro("arquivo_nao_regular", lambda: self.validar(self.pasta))
        abrir.assert_not_called()

    def test_origem_destino_identicos_nao_abrem_candidato(self):
        antes = self.estado()
        with patch.object(servico, "validar_backup") as validar:
            plano = self.planejar(destino=self.candidato)
        validar.assert_not_called()
        self.assertIn("origem_destino_identicos", plano.impedimentos)
        self.assertEqual(plano.falha_candidato.codigo, "origem_destino_sem_separacao")
        self.assertEqual(self.estado(), antes)

    def test_identidade_fisica_por_hardlink_bloqueia_plano(self):
        alias = self.raiz / "alias.db"
        os.link(self.candidato, alias)
        antes = self.estado()
        with patch.object(servico, "validar_backup") as validar:
            plano = self.planejar(destino=alias)
        validar.assert_not_called()
        self.assertIn("identidade_fisica_origem_destino", plano.impedimentos)
        self.assertEqual(self.estado(), antes)

    def test_candidato_com_outro_hardlink_e_recusado(self):
        os.link(self.candidato, self.raiz / "alias.db")
        self.erro("arquivo_com_hardlinks", self.validar)

    def test_caminhos_relativos_e_parent_traversal_sao_recusados(self):
        for caminho in (Path("relativo.db"), self.raiz / ".." / self.raiz.name / "banco.db"):
            with self.subTest(caminho=str(caminho)):
                self.erro("caminho_inseguro", lambda: self.validar(caminho))
        for nome in ("backup", "destino", "pasta_backups", "pasta_resgate"):
            with self.subTest(argumento=nome):
                self.erro("caminho_inseguro", lambda: self.planejar(**{nome: Path("relativo")}))

    def test_caminho_com_caracteres_uri_e_unicode(self):
        caminho = self.banco(self.raiz / "candidato # % sintético.db")
        self.assertEqual(self.validar(caminho).validacao.sha256, sha(caminho))

    def test_reparse_point_simulado_em_arquivo_e_ancestral_e_recusado(self):
        lstat = Path.lstat
        for alvo in (self.candidato, self.pasta):
            def obter(caminho, *args, **kwargs):
                info = lstat(caminho, *args, **kwargs)
                if caminho == alvo:
                    return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
                return info
            with self.subTest(alvo=alvo.name), patch.object(Path, "lstat", new=obter):
                self.erro("link_ou_reparse_point", self.validar)

    def test_symlinks_reais_quando_suportados(self):
        alias = self.raiz / "link.db"
        try:
            alias.symlink_to(self.candidato)
        except OSError as exc:
            self.skipTest("symlink real indisponivel: " + type(exc).__name__)
        self.erro("link_ou_reparse_point", lambda: self.validar(alias))
        diretorio = self.raiz / "link-pasta"
        diretorio.symlink_to(self.pasta, target_is_directory=True)
        self.erro("link_ou_reparse_point", lambda: self.validar(diretorio / self.candidato.name))

    def test_sidecars_do_candidato_sao_recusados_e_preservados(self):
        for sufixo in ("-wal", "-shm", "-journal"):
            auxiliar = Path(str(self.candidato) + sufixo)
            auxiliar.write_bytes(b"auxiliar sintetico")
            antes = self.estado()
            with self.subTest(sufixo=sufixo), patch.object(servico, "validar_backup") as validar:
                self.erro("arquivo_nao_independente", self.validar)
                validar.assert_not_called()
                self.assertEqual(self.estado(), antes)
            auxiliar.unlink()  # Somente limpeza da fixture, fora do serviço.

    def test_cabecalho_wal_sem_sidecars_nao_e_artefato_independente(self):
        with closing(sqlite3.connect(self.candidato)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
        self.erro("backup_arquivo_nao_independente", self.validar)

    def test_sidecars_e_locks_do_destino_sao_sinais_e_nunca_removidos(self):
        for sufixo in ("-wal", "-shm", "-journal", ".lock"):
            Path(str(self.destino) + sufixo).write_bytes(b"sinal sintetico")
        (self.pasta / ".seagbh-backup.lock").write_bytes(b"sinal sintetico")
        antes = self.estado()
        inspecao = self.inspecionar()
        self.assertEqual({a.tipo for a in inspecao.auxiliares if a.presenca == servico.Presenca.PRESENTE},
                         {"wal", "shm", "journal", "lock_legado", "lock_backups"})
        self.assertEqual(len(inspecao.impedimentos), 5)
        self.assertEqual(self.estado(), antes)
        self.assertEqual(inspecao.exclusividade, servico.Exclusividade.NAO_COMPROVADA)

    def test_destino_com_conexao_wal_aberta_nao_e_consultado_nem_alterado(self):
        with closing(sqlite3.connect(self.destino)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("INSERT INTO equipamentos VALUES(2,'COD-2','Outro',NULL,NULL,'2026-01-01')")
            conn.commit()
            antes = self.estado()
            consultas = []
            conn.set_trace_callback(consultas.append)
            plano = self.planejar()
            self.assertEqual(consultas, [])
            conn.set_trace_callback(None)
            self.assertIn("wal_presente", plano.impedimentos)
            self.assertIn("shm_presente", plano.impedimentos)
            self.assertEqual(self.estado(), antes)

    def test_destino_aberto_sem_sidecars_nao_comprova_exclusividade(self):
        with closing(sqlite3.connect(self.destino)) as conn:
            conn.execute("BEGIN")
            conn.execute("SELECT * FROM equipamentos").fetchall()
            inspecao = self.inspecionar()
            self.assertEqual(inspecao.impedimentos, ())
            self.assertEqual(inspecao.exclusividade, servico.Exclusividade.NAO_COMPROVADA)
            self.assertTrue(conn.in_transaction)

    def test_arvores_de_backups_destino_e_resgate_sao_separadas(self):
        casos = ((dict(destino=self.pasta / "operacional.db"), "destino_na_arvore_de_backups"),
                 (dict(pasta_resgate=self.raiz), "resgate_e_backups_sobrepostos"),
                 (dict(pasta_resgate=self.pasta / "resgate"), "resgate_e_backups_sobrepostos"),
                 (dict(destino=self.resgate / "banco.db"), "banco_na_arvore_de_resgate"))
        for kwargs, codigo in casos:
            with self.subTest(codigo=codigo):
                self.assertIn(codigo, self.inspecionar(**kwargs).impedimentos)
        self.assertFalse(self.resgate.exists())

    def test_destino_descendente_de_backups_e_resgate_existente_ou_ausente(self):
        self.resgate.mkdir()  # Preparação da fixture; o serviço não cria pastas.
        for pasta, codigo in ((self.pasta, "destino_na_arvore_de_backups"),
                              (self.resgate, "banco_na_arvore_de_resgate")):
            for existente in (False, True):
                destino = pasta / ("existente.db" if existente else "ausente.db")
                if existente:
                    self.banco(destino)
                antes = self.estado()
                with self.subTest(pasta=pasta.name, existente=existente):
                    self.assertIn(codigo, self.inspecionar(destino=destino).impedimentos)
                    self.assertEqual(destino.exists(), existente)
                    self.assertEqual(self.estado(), antes)

    def test_destino_ancestral_de_backups_existente_ou_ausente(self):
        for existente in (False, True):
            destino = self.raiz / ("ancestral-existente" if existente else "ancestral-ausente")
            backups = destino / "backups"
            if existente:
                backups.mkdir(parents=True)  # Destino existente como diretório.
            antes = self.estado()
            with self.subTest(existente=existente), \
                    patch.object(Path, "mkdir", side_effect=AssertionError("criação proibida")):
                inspecao = self.inspecionar(destino=destino, pasta_backups=backups)
            self.assertIn("backups_na_arvore_do_destino", inspecao.impedimentos)
            self.assertIn("destino_nao_regular" if existente else "destino_ausente", inspecao.impedimentos)
            self.assertEqual(destino.exists(), existente)
            self.assertEqual(backups.exists(), existente)
            self.assertEqual(self.estado(), antes)

    def test_destino_ancestral_de_resgate_existente_ou_ausente(self):
        for existente in (False, True):
            destino = self.raiz / ("resgate-ancestral-existente" if existente else "resgate-ancestral-ausente")
            if existente:
                destino.mkdir()
            resgate = destino / "resgate"
            antes = self.estado()
            with self.subTest(existente=existente), \
                    patch.object(Path, "mkdir", side_effect=AssertionError("criação proibida")):
                inspecao = self.inspecionar(destino=destino, pasta_resgate=resgate)
            self.assertIn("resgate_na_arvore_do_destino", inspecao.impedimentos)
            self.assertIn("destino_nao_regular" if existente else "destino_ausente", inspecao.impedimentos)
            self.assertEqual(destino.exists(), existente)
            self.assertFalse(resgate.exists())
            self.assertEqual(self.estado(), antes)

    def test_sobreposicao_backups_resgate_nos_dois_sentidos(self):
        for existente in (False, True):
            destino = self.destino if existente else self.raiz / "destino-ausente.db"
            for resgate in (self.pasta / "resgate", self.raiz, self.pasta):
                antes = self.estado()
                with self.subTest(existente=existente, resgate=str(resgate)), \
                        patch.object(Path, "mkdir", side_effect=AssertionError("criação proibida")):
                    inspecao = self.inspecionar(destino=destino, pasta_resgate=resgate)
                self.assertIn("resgate_e_backups_sobrepostos", inspecao.impedimentos)
                self.assertEqual(destino.exists(), existente)
                self.assertEqual(self.estado(), antes)

    def test_destino_arquivo_recusa_pastas_descendentes_sem_criar_nada(self):
        antes = self.estado()
        for argumento in ("pasta_backups", "pasta_resgate"):
            with self.subTest(argumento=argumento), \
                    patch.object(Path, "mkdir", side_effect=AssertionError("criação proibida")):
                self.erro("componente_nao_diretorio", lambda: self.inspecionar(
                    **{argumento: self.destino / "pasta"}))
        self.assertEqual(self.estado(), antes)

    def test_permissoes_negadas_ou_nao_verificadas_nao_sao_sucesso(self):
        for efeito in (False, PermissionError("detalhe privado sintetico")):
            kwargs = {"return_value": efeito} if efeito is False else {"side_effect": efeito}
            with self.subTest(efeito=type(efeito).__name__), patch.object(servico.os, "access", **kwargs):
                inspecao = self.inspecionar()
            self.assertIs(inspecao.permissoes.escrita_arquivo, False if efeito is False else None)
            self.assertIn("permissao_escrita_arquivo_nao_confirmada", inspecao.impedimentos)

    def test_falha_de_metadados_e_sanitizada(self):
        lstat = Path.lstat
        def obter(caminho, *args, **kwargs):
            if caminho == self.destino:
                raise PermissionError("SQL e dados privados sinteticos")
            return lstat(caminho, *args, **kwargs)
        with patch.object(Path, "lstat", new=obter):
            erro = self.erro("caminho_nao_verificado", self.inspecionar)
        self.assertNotIn("privados", str(erro))
        self.assertEqual(erro.causa, "PermissionError")

    def test_falha_de_leitura_e_sanitizada_e_nao_altera_arquivos(self):
        antes = self.estado()
        with patch.object(Path, "open", side_effect=PermissionError("detalhe privado sintetico")):
            erro = self.erro("inspecao_falhou", self.validar)
        self.assertEqual(erro.causa, "PermissionError")
        self.assertNotIn("privado", str(erro))
        self.assertEqual(self.estado(), antes)

    def test_falha_de_hash_nao_retorna_validacao(self):
        with patch.object(servico.hashlib, "sha256", side_effect=ValueError("detalhe privado sintetico")):
            erro = self.erro("inspecao_falhou", self.validar)
        self.assertEqual(erro.causa, "ValueError")
        self.assertNotIn("privado", str(erro))

    def test_falha_durante_leitura_do_hash_fecha_arquivo(self):
        abrir = Path.open
        arquivos = []
        class LeitorComFalha:
            def __init__(self, arquivo):
                self.arquivo = arquivo
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.arquivo.close()
            def fileno(self):
                return self.arquivo.fileno()
            def read(self, *args):
                raise OSError("leitura privada sintetica")
        def leitor(caminho, mode="r", *args, **kwargs):
            arquivo = abrir(caminho, mode, *args, **kwargs)
            arquivos.append(arquivo)
            return LeitorComFalha(arquivo)
        with patch.object(Path, "open", new=leitor):
            erro = self.erro("inspecao_falhou", self.validar)
        self.assertEqual(erro.causa, "OSError")
        self.assertEqual(len(arquivos), 1)
        self.assertTrue(arquivos[0].closed)
        self.assertNotIn("privada", str(erro))

    def test_alteracao_durante_validador_e_detectada(self):
        def validar(**kwargs):
            resultado = backup.validar_backup(**kwargs)
            with self.candidato.open("ab") as arquivo:
                arquivo.write(b"alterado")
            return resultado
        with patch.object(servico, "validar_backup", side_effect=validar) as validar_mock:
            self.erro("arquivo_alterado", self.validar)
        validar_mock.assert_called_once()

    def test_alteracao_durante_inspecao_estrutural_e_detectada(self):
        estrutura = servico._estrutura
        def obter(*args):
            resultado = estrutura(*args)
            info = self.candidato.stat()
            os.utime(self.candidato, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
            return resultado
        with patch.object(servico, "_estrutura", side_effect=obter) as estrutura_mock:
            self.erro("arquivo_alterado", self.validar)
        estrutura_mock.assert_called_once()

    def test_alteracao_apos_hash_final_e_detectada(self):
        ler_hash = servico._hash
        chamadas = []
        def obter(*args):
            resultado = ler_hash(*args)
            chamadas.append(True)
            if len(chamadas) == 2:
                info = self.candidato.stat()
                os.utime(self.candidato, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
            return resultado
        with patch.object(servico, "_hash", side_effect=obter):
            self.erro("arquivo_alterado", self.validar)
        self.assertEqual(len(chamadas), 2)

    def test_mudanca_do_destino_durante_inspecao_e_detectada(self):
        acesso = servico._acesso
        alterado = []
        def obter(*args):
            if not alterado:
                info = self.destino.stat()
                os.utime(self.destino, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
                alterado.append(True)
            return acesso(*args)
        with patch.object(servico, "_acesso", side_effect=obter):
            self.assertIn("destino_alterado_durante_inspecao", self.inspecionar().impedimentos)

    def test_sidecar_aparece_no_destino_durante_validacao_do_candidato(self):
        def validar(**kwargs):
            resultado = backup.validar_backup(**kwargs)
            Path(str(self.destino) + "-wal").write_bytes(b"concorrencia sintetica")
            return resultado
        with patch.object(servico, "validar_backup", side_effect=validar):
            plano = self.planejar()
        self.assertIn("destino_alterado_durante_planejamento", plano.impedimentos)
        self.assertIn("wal_presente", plano.impedimentos)
        self.assertEqual(Path(str(self.destino) + "-wal").read_bytes(), b"concorrencia sintetica")

    def test_falha_destino_deixa_candidato_nao_inspecionado(self):
        with patch.object(servico, "inspecionar_destino_restauracao",
                          side_effect=servico.ErroRestauracao("destino", "inspecao_falhou")), \
                patch.object(servico, "validar_backup") as validar:
            plano = self.planejar()
        validar.assert_not_called()
        self.assertEqual(plano.estado_candidato, servico.EstadoCandidato.NAO_INSPECIONADO)
        self.assertIsNone(plano.falha_candidato)
        self.assertEqual(plano.falha_destino.codigo, "inspecao_falhou")

    def test_postgres_e_recusado_antes_de_qualquer_acesso(self):
        with patch.object(Path, "lstat") as lstat, patch.object(servico, "validar_backup") as validar:
            self.erro("backend_nao_suportado", lambda: servico.validar_candidato_restauracao(
                backend="postgres", backup=self.candidato))
            for funcao in (servico.planejar_restauracao, servico.inspecionar_destino_restauracao):
                self.erro("backend_nao_suportado", lambda: funcao(**self.argumentos(backend="postgres")))
        lstat.assert_not_called()
        validar.assert_not_called()

    def test_prazo_invalido_e_expirado(self):
        for prazo in (0, -1, True, float("inf"), float("nan")):
            with self.subTest(prazo=prazo):
                self.erro("prazo_invalido", lambda: self.validar(prazo=prazo))
        with patch.object(servico.time, "monotonic", side_effect=[0, 100]):
            self.erro("prazo_excedido", lambda: self.validar(prazo=1))

    def test_progress_handler_real_preserva_codigo_de_prazo_e_fecha_conexao(self):
        conectar, estrutura = sqlite3.connect, servico._estrutura
        conexoes, sinais = [], []
        class ConexaoComPrazo(sqlite3.Connection):
            def set_progress_handler(self, callback, intervalo):
                def expirar():
                    with patch.object(servico.time, "monotonic", return_value=float("inf")):
                        sinal = callback()
                    sinais.append((intervalo, sinal))
                    return sinal
                # Callback chamado pelo SQLite real, sem espera ou banco grande.
                super().set_progress_handler(expirar, 1)
        def abrir(*args, **kwargs):
            conn = conectar(*args, factory=ConexaoComPrazo, **kwargs)
            conexoes.append(conn)
            return conn
        def inspecionar(*args):
            with patch.object(sqlite3, "connect", side_effect=abrir):
                return estrutura(*args)
        antes = self.estado()
        with patch.object(servico, "_estrutura", side_effect=inspecionar):
            self.erro("prazo_excedido", self.validar)
            plano = self.planejar()
        self.assertEqual(plano.falha_candidato.codigo, "prazo_excedido")
        self.assertEqual(plano.estado_candidato, servico.EstadoCandidato.RECUSADO)
        self.assertEqual(sinais, [(1000, 1), (1000, 1)])
        self.assertEqual(len(conexoes), 2)
        for conn in conexoes:
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")
        self.assertEqual(self.estado(), antes)

    def test_operational_error_real_sem_prazo_nao_e_classificado_como_timeout(self):
        conectar, estrutura = sqlite3.connect, servico._estrutura
        conexoes, falhas = [], []
        class ConexaoComErro(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if sql.startswith("PRAGMA table_info"):
                    falhas.append(True)
                    # Erro produzido pelo SQLite real, sem inventar OperationalError.
                    return super().execute("SELECT * FROM tabela_sintetica_inexistente")
                return super().execute(sql, *args, **kwargs)
        def abrir(*args, **kwargs):
            conn = conectar(*args, factory=ConexaoComErro, **kwargs)
            conexoes.append(conn)
            return conn
        def inspecionar(*args):
            with patch.object(sqlite3, "connect", side_effect=abrir):
                return estrutura(*args)
        antes = self.estado()
        with patch.object(servico, "_estrutura", side_effect=inspecionar):
            erro = self.erro("inspecao_falhou", self.validar)
            plano = self.planejar()
        self.assertEqual(erro.causa, "OperationalError")
        self.assertNotIn("tabela_sintetica_inexistente", str(erro))
        self.assertEqual(plano.falha_candidato.codigo, "inspecao_falhou")
        self.assertEqual(plano.estado_candidato, servico.EstadoCandidato.RECUSADO)
        self.assertEqual(len(falhas), 2)
        self.assertEqual(len(conexoes), 2)
        for conn in conexoes:
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")
        self.assertEqual(self.estado(), antes)

    def test_inspecao_destino_nao_abre_arquivos_ou_sqlite(self):
        with patch.object(Path, "open", side_effect=AssertionError("arquivo nao deve ser aberto")) as abrir, \
                patch.object(sqlite3, "connect", side_effect=AssertionError("SQLite proibido")) as conectar:
            resultado = self.inspecionar()
        abrir.assert_not_called()
        conectar.assert_not_called()
        self.assertTrue(resultado.existente)

    def test_planejamento_nao_executa_escritas_e_fecha_conexoes(self):
        antes = self.estado()
        abrir, conectar = Path.open, sqlite3.connect
        conexoes, consultas = [], []
        def ler(caminho, mode="r", *args, **kwargs):
            self.assertEqual(mode, "rb")
            return abrir(caminho, mode, *args, **kwargs)
        def somente_leitura(endereco, *args, **kwargs):
            self.assertIn("mode=ro", endereco)
            self.assertNotIn(self.destino.name, endereco)
            conn = conectar(endereco, *args, **kwargs)
            conn.set_trace_callback(consultas.append)
            conexoes.append(conn)
            return conn
        with ExitStack() as stack:
            stack.enter_context(patch.object(Path, "open", new=ler))
            stack.enter_context(patch.object(sqlite3, "connect", side_effect=somente_leitura))
            for objeto, nome in ((os, "rename"), (os, "replace"), (os, "unlink"),
                                 (shutil, "copy2"), (Path, "mkdir")):
                stack.enter_context(patch.object(objeto, nome, side_effect=AssertionError("escrita proibida")))
            plano = self.planejar()
        self.assertEqual(plano.estado_candidato, servico.EstadoCandidato.VALIDADO)
        self.assertEqual(self.estado(), antes)
        self.assertFalse(self.resgate.exists())
        self.assertEqual(len(conexoes), 2)
        self.assertFalse(any(q.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE", "COMMIT", "ROLLBACK"))
                             for q in consultas))
        for conn in conexoes:
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def test_falha_estrutural_fecha_conexao(self):
        conectar = sqlite3.connect
        conexoes = []
        def abrir(*args, **kwargs):
            conn = conectar(*args, **kwargs)
            conexoes.append(conn)
            conn.set_authorizer(lambda acao, *resto: sqlite3.SQLITE_DENY
                                if acao == sqlite3.SQLITE_PRAGMA and resto[0] == "table_info" else sqlite3.SQLITE_OK)
            return conn
        with patch.object(sqlite3, "connect", side_effect=abrir):
            with self.assertRaises(servico.ErroRestauracao):
                self.validar()
        self.assertGreater(len(conexoes), 0)
        for conn in conexoes:
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def test_ctime_distinto_entre_lstat_e_fstat_nao_inventa_alteracao(self):
        fstat = os.fstat
        def observar(fd):
            info = fstat(fd)
            campos = {n: getattr(info, n) for n in ("st_dev", "st_ino", "st_size", "st_mtime_ns",
                                                   "st_ctime_ns", "st_mode", "st_nlink")}
            campos["st_ctime_ns"] += 1000000
            return SimpleNamespace(**campos)
        with patch.object(servico.os, "fstat", side_effect=observar):
            self.assertEqual(self.validar().validacao.sha256, sha(self.candidato))

    def test_mudanca_de_ctime_do_handle_durante_hash_e_recusada(self):
        fstat = os.fstat
        chamadas = []
        def observar(fd):
            info = fstat(fd)
            campos = {n: getattr(info, n) for n in ("st_dev", "st_ino", "st_size", "st_mtime_ns",
                                                   "st_ctime_ns", "st_mode", "st_nlink")}
            chamadas.append(True)
            campos["st_ctime_ns"] += len(chamadas) * 1000000
            return SimpleNamespace(**campos)
        with patch.object(servico.os, "fstat", side_effect=observar):
            self.erro("arquivo_alterado", self.validar)
        self.assertEqual(len(chamadas), 2)

    def test_auxiliar_inacessivel_nao_e_considerado_ausente(self):
        obter = servico._info
        def observar(caminho):
            if caminho == Path(str(self.destino) + "-wal"):
                raise PermissionError("detalhe privado sintetico")
            return obter(caminho)
        with patch.object(servico, "_info", side_effect=observar):
            inspecao = self.inspecionar()
        wal = next(a for a in inspecao.auxiliares if a.tipo == "wal")
        self.assertEqual(wal.presenca, servico.Presenca.NAO_VERIFICADA)
        self.assertIsNone(wal.inseguro)
        self.assertIsNone(wal.regular)
        self.assertIn("wal_nao_verificada", inspecao.impedimentos)

    def test_importacao_isolada_sem_ui_database_env_ou_rede(self):
        programa = r'''
import builtins,sys
original=builtins.__import__
defender=('PyQt6','src.core.database','core.database','src.core.licenca','requests','socket','urllib.request','psycopg')
def importar(nome,*args,**kwargs):
    if any(nome==p or nome.startswith(p+'.') for p in defender):
        raise AssertionError('importacao operacional proibida')
    return original(nome,*args,**kwargs)
builtins.__import__=importar
from src.core import restauracao_sqlite
assert not any(n in sys.modules for n in defender)
assert not hasattr(restauracao_sqlite,'Database')
assert not hasattr(restauracao_sqlite,'restaurar')
'''
        resultado = subprocess.run([sys.executable, "-B", "-c", programa],
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)


class ExecutorRestauracaoSQLite(unittest.TestCase):
    def setUp(self):
        self.ambiente = executor.criar_ambiente_restauracao_teste()
        self.raiz = self.ambiente.raiz
        self.addCleanup(shutil.rmtree, self.raiz)
        for nome in ("backups", "dados", "resgates"):
            (self.raiz / nome).mkdir()
        self.origem = self.raiz / "backups" / "candidato.db"
        self.destino = self.raiz / "dados" / "destino.db"
        self.resgate = self.raiz / "resgates" / "anterior.db"
        self.diario = self.destino.with_name(self.destino.name + ".seagbh-restauracao-teste.json")
        self.substituto = self.destino.with_name(self.destino.name + ".seagbh-substituto-teste")
        self._banco(self.origem, "Novo")
        self._banco(self.destino, "Antigo")
        self.hash_antigo = sha(self.destino)
        self.hash_novo = sha(self.origem)

    @staticmethod
    def _banco(caminho, nome):
        # Exercita somente o DDL real em conexão temporária explícita.
        with closing(sqlite3.connect(caminho)) as conn:
            with closing(conn.cursor()) as cursor:
                Database._criar_tabelas(SimpleNamespace(backend="sqlite", cursor=cursor, conn=conn))
            conn.execute(f"PRAGMA application_id={executor.APPLICATION_ID_TESTE}")
            conn.execute("INSERT INTO equipamentos (codigo_barras,nome,data_cadastro) VALUES(?,?,?)",
                         ("1", nome, "01/01/2026 10:00"))
            conn.execute(
                """INSERT INTO eventos (nome_evento,responsavel,data_inicio,data_fim)
                   VALUES ('Evento','Sintético','01/01/2026','02/01/2026')"""
            )
            conn.execute("INSERT INTO movimentacoes (evento_id,equipamento_id,data_movimentacao,tipo) "
                         "VALUES (1,1,'01/01/2026 10:01','saida')")
            conn.commit()

    def _argumentos(self):
        return dict(ambiente=self.ambiente, destino=self.destino, resgate=self.resgate)

    def _restaurar(self, **mudancas):
        argumentos = dict(self._argumentos(), origem=self.origem)
        argumentos.update(mudancas)
        return executor.restaurar_sqlite_teste(**argumentos)

    def _recuperar(self, **mudancas):
        return executor.recuperar_restauracao_teste(**dict(self._argumentos(), **mudancas))

    def _estado(self, resultado, estado, codigo=None):
        self.assertEqual(resultado.estado, estado, resultado)
        if codigo:
            self.assertEqual(resultado.codigo, codigo)

    def _interromper_processo(self, apos_publicacao):
        caminho = Path(__file__).resolve().parents[2]
        codigo = """
import os, sys
from pathlib import Path
from unittest.mock import patch
from src.core import executor_restauracao_sqlite as e
raiz, identificador, origem, destino, resgate, apos = sys.argv[1:]
publicar_real = e._publicar
def interromper(copia, alvo):
    if alvo == Path(destino):
        if apos == 'depois':
            publicar_real(copia, alvo)
        os._exit(87)
    publicar_real(copia, alvo)
with patch.object(e, "_publicar", side_effect=interromper):
    e.restaurar_sqlite_teste(ambiente=e.AmbienteRestauracaoTeste(Path(raiz), identificador),
                             origem=origem, destino=destino, resgate=resgate)
raise SystemExit(88)
"""
        resultado = subprocess.run(
            [sys.executable, "-B", "-c", codigo, str(self.raiz), self.ambiente.identificador,
             str(self.origem), str(self.destino), str(self.resgate),
             "depois" if apos_publicacao else "antes"],
            timeout=20, cwd=caminho, capture_output=True,
        )
        self.assertEqual(resultado.returncode, 87)
        self.assertTrue(self.diario.is_file())

    def test_publica_candidato_e_preserva_resgate_independente(self):
        resultado = self._restaurar()
        self._estado(resultado, executor.EstadoRestauracao.SUCESSO)
        self.assertTrue(resultado.resgate_validado)
        self.assertEqual(sha(self.destino), self.hash_novo)
        self.assertEqual(sha(self.origem), self.hash_novo)
        self.assertEqual(sha(self.resgate), self.hash_antigo)
        self.assertFalse(os.path.samefile(self.resgate, self.destino))
        self.assertTrue(backup.validar_backup(backend="sqlite", caminho=self.resgate).independente)
        self.assertFalse(self.diario.exists())
        self.assertFalse(self.substituto.exists())

    def test_origem_corrompida_recusa_e_preserva_destino(self):
        self.origem.write_bytes(b"invalido")
        self._estado(self._restaurar(), executor.EstadoRestauracao.RECUSADA)
        self.assertEqual(sha(self.destino), self.hash_antigo)
        self.assertFalse(self.resgate.exists())

    def test_revalida_esquema_apos_preflight_anterior(self):
        plano = servico.planejar_restauracao(
            backend="sqlite", backup=self.origem, destino=self.destino,
            pasta_backups=self.origem.parent, pasta_resgate=self.resgate.parent,
        )
        self.assertEqual(plano.estado_candidato, servico.EstadoCandidato.VALIDADO)
        with closing(sqlite3.connect(self.origem)) as conn:
            conn.execute("CREATE INDEX idx_estranho ON equipamentos(nome)")
        self._estado(self._restaurar(), executor.EstadoRestauracao.RECUSADA,
                     "esquema_fora_do_contrato_teste")
        self.assertEqual(sha(self.destino), self.hash_antigo)
        self.assertFalse(self.resgate.exists())

    def test_rejeita_tipo_de_valor_incompativel_mesmo_com_ddl_valido(self):
        with closing(sqlite3.connect(self.origem)) as conn:
            conn.execute("UPDATE equipamentos SET nome = ?", (b"binario",))
            conn.commit()
        self._estado(self._restaurar(), executor.EstadoRestauracao.RECUSADA, "dados_incompativeis")
        self.assertEqual(sha(self.destino), self.hash_antigo)

    def test_so_aceita_bancos_marcados_como_sinteticos(self):
        with closing(sqlite3.connect(self.destino)) as conn:
            conn.execute("PRAGMA application_id=0")
        antes = sha(self.destino)
        self._estado(self._restaurar(), executor.EstadoRestauracao.RECUSADA,
                     "banco_sem_marca_sintetica")
        self.assertEqual(sha(self.destino), antes)
        self.assertFalse(self.resgate.exists())

    def test_destino_com_conexao_cooperativa_em_uso_e_recusado(self):
        with abrir_conexao_operacional(banco=self.destino) as sessao:
            self.assertEqual(sessao.executar("SELECT nome FROM equipamentos"), [("Antigo",)])
            self._estado(self._restaurar(), executor.EstadoRestauracao.RECUSADA, "ocupado")
        self.assertEqual(sha(self.destino), self.hash_antigo)
        self.assertFalse(self.resgate.exists())

    def test_backup_protegido_ativo_impede_restauracao_e_recuperacao(self):
        iniciou_copia = Event()
        liberar_copia = Event()

        def avancar(_restantes, _total):
            iniciou_copia.set()
            if not liberar_copia.wait(10):
                raise TimeoutError("restauracao nao verificou a barreira")

        with ThreadPoolExecutor(max_workers=1) as pool:
            tarefa = pool.submit(
                backup.criar_backup, backend="sqlite", origem=self.destino,
                pasta=self.origem.parent, protegido=True, progresso=avancar, paginas=1,
            )
            try:
                self.assertTrue(iniciou_copia.wait(10), "backup nao iniciou a copia")
                for tentar in (self._restaurar, self._recuperar):
                    with self.subTest(operacao=tentar.__name__):
                        resultado = tentar()
                        self._estado(resultado, executor.EstadoRestauracao.RECUSADA, "ocupado")
                        self.assertEqual(resultado.fase, "barreira_aquisicao")
                self.assertEqual(sha(self.destino), self.hash_antigo)
                self.assertFalse(self.resgate.exists())
                self.assertFalse(self.diario.exists())
            finally:
                liberar_copia.set()
            snapshot = tarefa.result(timeout=10)

        with closing(sqlite3.connect(snapshot.caminho)) as conn:
            self.assertEqual(conn.execute("SELECT nome FROM equipamentos").fetchone(), ("Antigo",))
        self._estado(self._restaurar(), executor.EstadoRestauracao.SUCESSO)
        self.assertEqual(sha(self.destino), self.hash_novo)
        self.assertEqual(sha(self.resgate), self.hash_antigo)

    def test_restauracao_impede_backup_protegido_ate_confirmar(self):
        finalizar = executor._finalizar
        argumentos = dict(backend="sqlite", origem=self.destino, pasta=self.origem.parent,
                          protegido=True)
        antes = set(self.origem.parent.iterdir())

        def conferir(arqs):
            with self.assertRaises(backup.ErroBackup) as negado:
                backup.criar_backup(**argumentos)
            self.assertEqual(negado.exception.fase, "barreira")
            self.assertEqual(negado.exception.codigo, "barreira_ocupado")
            self.assertEqual(set(self.origem.parent.iterdir()), antes)
            self.assertEqual(sha(self.destino), self.hash_novo)
            finalizar(arqs)

        with patch.object(executor, "_finalizar", side_effect=conferir) as confirmar:
            self._estado(self._restaurar(), executor.EstadoRestauracao.SUCESSO)
        confirmar.assert_called_once()
        snapshot = backup.criar_backup(**argumentos)
        with closing(sqlite3.connect(snapshot.caminho)) as conn:
            self.assertEqual(conn.execute("SELECT nome FROM equipamentos").fetchone(), ("Novo",))

    def test_barreira_permanece_exclusiva_ate_publicar(self):
        publicar = executor._publicar

        def conferir(origem, destino):
            with self.assertRaises(ErroBarreira) as negado:
                adquirir_uso_operacional(backend="sqlite", banco=self.destino)
            self.assertEqual(negado.exception.codigo, "ocupado")
            publicar(origem, destino)

        with patch.object(executor, "_publicar", side_effect=conferir) as chamadas:
            self._estado(self._restaurar(), executor.EstadoRestauracao.SUCESSO)
        self.assertEqual(chamadas.call_count, 2)
        with adquirir_uso_operacional(backend="sqlite", banco=self.destino):
            self.assertEqual(sha(self.destino), self.hash_novo)

    def test_falha_ao_criar_resgate_preserva_destino(self):
        abrir = Path.open

        def recusar(caminho, modo="r", *args, **kwargs):
            if caminho == self.resgate and modo == "xb":
                raise PermissionError("detalhe privado")
            return abrir(caminho, modo, *args, **kwargs)

        with patch.object(Path, "open", autospec=True, side_effect=recusar):
            resultado = self._restaurar()
        self._estado(resultado, executor.EstadoRestauracao.FALHA_RECUPERAVEL, "PermissionError")
        self.assertEqual(resultado.fase, "resgate")
        self.assertFalse(resultado.resgate_validado)
        self.assertEqual(sha(self.destino), self.hash_antigo)
        self.assertFalse(self.diario.exists())

    def test_falha_na_preparacao_preserva_resgate_e_destino(self):
        copiar = executor._copiar

        def falhar(origem, novo, esperado, controle):
            if novo == self.substituto:
                novo.write_bytes(b"parcial")
                raise OSError("gravacao incompleta")
            copiar(origem, novo, esperado, controle)

        with patch.object(executor, "_copiar", side_effect=falhar):
            resultado = self._restaurar()
        self._estado(resultado, executor.EstadoRestauracao.FALHA_RECUPERAVEL)
        self.assertEqual(resultado.fase, "preparacao")
        self.assertTrue(resultado.resgate_validado)
        self.assertEqual(sha(self.resgate), self.hash_antigo)
        self.assertEqual(sha(self.destino), self.hash_antigo)
        self.assertFalse(self.substituto.exists())
        self.assertFalse(self.diario.exists())

    def test_falha_imediatamente_antes_de_publicar_mantem_estado_antigo(self):
        publicar = executor._publicar

        def falhar(origem, destino):
            if destino == self.destino:
                raise OSError("publicacao interrompida")
            publicar(origem, destino)

        with patch.object(executor, "_publicar", side_effect=falhar):
            resultado = self._restaurar()
        self._estado(resultado, executor.EstadoRestauracao.FALHA_RECUPERAVEL)
        self.assertEqual(resultado.fase, "publicacao")
        self.assertEqual(sha(self.destino), self.hash_antigo)
        self.assertTrue(self.diario.exists())
        self._estado(self._recuperar(), executor.EstadoRestauracao.FALHA_RECUPERAVEL,
                     "original_preservado")

    def test_falha_de_sincronizacao_pos_publicacao_requer_reconciliacao(self):
        sincronizar = executor._sincronizar_pasta

        def falhar_depois(pasta):
            if self.diario.exists() and not self.substituto.exists():
                raise OSError("metadados nao sincronizados")
            sincronizar(pasta)

        with patch.object(executor, "_sincronizar_pasta", side_effect=falhar_depois):
            resultado = self._restaurar()
        self._estado(resultado, executor.EstadoRestauracao.INTERVENCAO_MANUAL)
        self.assertEqual(resultado.fase, "confirmacao")
        self.assertTrue(self.diario.exists())
        self.assertEqual(sha(self.resgate), self.hash_antigo)
        self._estado(self._recuperar(), executor.EstadoRestauracao.SUCESSO, "publicacao_confirmada")

    def test_interrupcao_real_do_processo_antes_da_troca_recupera_original(self):
        self._interromper_processo(apos_publicacao=False)
        self._estado(self._restaurar(), executor.EstadoRestauracao.INTERVENCAO_MANUAL,
                     "operacao_anterior_pendente")
        self._estado(self._recuperar(), executor.EstadoRestauracao.FALHA_RECUPERAVEL,
                     "original_preservado")
        self.assertEqual(sha(self.destino), self.hash_antigo)
        self.assertEqual(sha(self.resgate), self.hash_antigo)
        self.assertFalse(self.diario.exists())
        self.assertFalse(self.substituto.exists())

    def test_interrupcao_real_do_processo_depois_da_troca_confirma_substituto(self):
        self._interromper_processo(apos_publicacao=True)
        self._estado(self._recuperar(), executor.EstadoRestauracao.SUCESSO, "publicacao_confirmada")
        self.assertEqual(sha(self.destino), self.hash_novo)
        self.assertEqual(sha(self.resgate), self.hash_antigo)
        self.assertFalse(self.diario.exists())

        self._estado(self._recuperar(), executor.EstadoRestauracao.RECUSADA, "sem_diario")
        self.assertEqual(sha(self.destino), self.hash_novo)
        self.assertEqual(sha(self.resgate), self.hash_antigo)

    def test_recuperacao_preserva_terceiro_estado_para_intervencao(self):
        self._interromper_processo(apos_publicacao=True)
        with closing(sqlite3.connect(self.destino)) as conn:
            conn.execute("UPDATE equipamentos SET nome='Mais recente'")
            conn.commit()
        terceiro = sha(self.destino)
        self._estado(self._recuperar(), executor.EstadoRestauracao.INTERVENCAO_MANUAL,
                     "destino_diverge_do_diario")
        self.assertEqual(sha(self.destino), terceiro)
        self.assertEqual(sha(self.resgate), self.hash_antigo)
        self.assertTrue(self.diario.exists())

    def test_publicacao_incompleta_com_destino_ausente_preserva_evidencias(self):
        self._interromper_processo(apos_publicacao=False)
        self.destino.unlink()
        self._estado(self._recuperar(), executor.EstadoRestauracao.INTERVENCAO_MANUAL)
        self.assertFalse(self.destino.exists())
        self.assertEqual(sha(self.resgate), self.hash_antigo)
        self.assertEqual(sha(self.substituto), self.hash_novo)
        self.assertTrue(self.diario.exists())

    def test_sidecars_sao_preservados_sem_abrir_banco(self):
        for banco in (self.destino, self.origem):
            for sufixo in ("-wal", "-shm", "-journal", ".lock") if banco == self.destino else (
                "-wal", "-shm", "-journal",
            ):
                with self.subTest(banco=banco.name, sufixo=sufixo):
                    auxiliar = banco.with_name(banco.name + sufixo)
                    auxiliar.write_bytes(b"nao apagar")
                    try:
                        with patch.object(sqlite3, "connect") as abrir:
                            self._estado(self._restaurar(), executor.EstadoRestauracao.RECUSADA)
                        abrir.assert_not_called()
                        self.assertEqual(auxiliar.read_bytes(), b"nao apagar")
                        self.assertEqual(sha(self.destino), self.hash_antigo)
                        self.assertFalse(self.resgate.exists())
                    finally:
                        auxiliar.unlink()

    def test_wal_com_commit_pendente_de_checkpoint_permanece_legivel(self):
        with closing(sqlite3.connect(self.destino)) as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode=WAL").fetchone(), ("wal",))
            conn.execute("PRAGMA wal_autocheckpoint=0")
            conn.execute("UPDATE equipamentos SET nome='Commit no WAL'")
            conn.commit()
            wal = self.destino.with_name(self.destino.name + "-wal")
            shm = self.destino.with_name(self.destino.name + "-shm")
            antes = (sha(self.destino), sha(wal), sha(shm))
            self._estado(self._restaurar(), executor.EstadoRestauracao.RECUSADA, "wal_presente")
            self.assertEqual((sha(self.destino), sha(wal), sha(shm)), antes)
            self.assertEqual(conn.execute("SELECT nome FROM equipamentos").fetchone(), ("Commit no WAL",))
            self.assertFalse(self.resgate.exists())

    def test_resgate_adulterado_impede_concluir_a_recuperacao(self):
        self._interromper_processo(apos_publicacao=True)
        self.resgate.write_bytes(b"resgate adulterado")
        self._estado(self._recuperar(), executor.EstadoRestauracao.INTERVENCAO_MANUAL)
        self.assertTrue(self.diario.exists())
        self.assertEqual(sha(self.destino), self.hash_novo)
        self.assertEqual(self.resgate.read_bytes(), b"resgate adulterado")

    def test_diario_adulterado_preserva_evidencias(self):
        self._interromper_processo(apos_publicacao=False)
        original = self.diario.read_bytes()
        registro = json.loads(original)
        registro["novo"] = "0" * 64
        casos = ((b"{", "JSONDecodeError"),
                 (json.dumps(registro).encode(), "substituto_diverge_do_diario"))
        for dados, codigo in casos:
            with self.subTest(codigo=codigo):
                self.diario.write_bytes(dados)
                resultado = self._recuperar()
                self._estado(resultado, executor.EstadoRestauracao.INTERVENCAO_MANUAL, codigo)
                self.assertEqual(resultado.fase, "recuperacao")
                self.assertEqual(self.diario.read_bytes(), dados)
                self.assertEqual(sha(self.destino), self.hash_antigo)
                self.assertEqual(sha(self.resgate), self.hash_antigo)
                self.assertEqual(sha(self.substituto), self.hash_novo)

        self.diario.write_bytes(original)
        self._estado(self._recuperar(), executor.EstadoRestauracao.FALHA_RECUPERAVEL,
                     "original_preservado")
        self._estado(self._recuperar(), executor.EstadoRestauracao.RECUSADA, "sem_diario")
        self.assertEqual(sha(self.destino), self.hash_antigo)
        self.assertEqual(sha(self.resgate), self.hash_antigo)
        self.assertFalse(self.substituto.exists())

    def test_caminhos_sobrepostos_ou_fora_do_ambiente_sao_recusados(self):
        casos = (
            dict(origem=self.destino),
            dict(resgate=self.origem.parent / "resgate.db"),
            dict(resgate=self.destino),
            dict(destino=Path("relativo.db")),
            dict(origem=self.raiz / "backups" / ".." / "dados" / self.destino.name),
            dict(ambiente=executor.AmbienteRestauracaoTeste(self.raiz, "0" * 32)),
        )
        with tempfile.TemporaryDirectory(prefix="seagbh-fora-") as fora:
            outro = Path(fora) / "candidato.db"
            self._banco(outro, "Fora")
            for caso in (*casos, dict(origem=outro)):
                with self.subTest(campos=tuple(caso)):
                    self._estado(self._restaurar(**caso), executor.EstadoRestauracao.RECUSADA)
            self.assertFalse(Path(fora, "candidato.db.seagbh-access-v1.lock").exists())
        self.assertEqual(sha(self.destino), self.hash_antigo)

    def test_hardlink_e_recusado_antes_de_publicar(self):
        os.link(self.origem, self.raiz / "backups" / "alias.db")
        self._estado(self._restaurar(), executor.EstadoRestauracao.RECUSADA)
        self.assertEqual(sha(self.destino), self.hash_antigo)


if __name__ == "__main__":
    unittest.main()
