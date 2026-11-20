"""Pré-validação offline: stdlib, bancos sintéticos e nenhuma restauração."""

from contextlib import closing, ExitStack
from dataclasses import FrozenInstanceError
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.core import backup_sqlite as backup
from src.core import restauracao_sqlite as servico
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


if __name__ == "__main__":
    unittest.main()
