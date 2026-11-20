"""Regressão offline: bancos sintéticos, sem Database, GUI ou serviços externos."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import UUID

from src.core import backup_sqlite as servico


DDL = """
CREATE TABLE equipamentos (
 id INTEGER PRIMARY KEY, codigo_barras TEXT UNIQUE NOT NULL, nome TEXT NOT NULL,
 descricao TEXT, localizacao TEXT, data_cadastro TEXT NOT NULL);
CREATE TABLE eventos (
 id INTEGER PRIMARY KEY, nome_evento TEXT NOT NULL, local_evento TEXT,
 responsavel TEXT NOT NULL, data_inicio TEXT NOT NULL, data_fim TEXT NOT NULL,
 status TEXT NOT NULL, notas TEXT);
CREATE TABLE movimentacoes (
 id INTEGER PRIMARY KEY, evento_id INTEGER NOT NULL REFERENCES eventos(id),
 equipamento_id INTEGER NOT NULL REFERENCES equipamentos(id),
 data_movimentacao TEXT NOT NULL, tipo TEXT);
CREATE TABLE manutencao (
 id INTEGER PRIMARY KEY, equipamento_id INTEGER NOT NULL REFERENCES equipamentos(id),
 data_saida TEXT NOT NULL, local_manutencao TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE auditoria (
 id INTEGER PRIMARY KEY, tabela TEXT NOT NULL, item_id INTEGER NOT NULL,
 acao TEXT NOT NULL, detalhes TEXT, data TEXT NOT NULL, usuario TEXT);
CREATE TABLE futuro (id INTEGER PRIMARY KEY REFERENCES equipamentos(id), payload BLOB);
CREATE INDEX idx_futuro ON futuro(payload);
CREATE VIEW equipamentos_nomes AS SELECT id, nome FROM equipamentos;
CREATE TRIGGER auditar_futuro AFTER INSERT ON futuro BEGIN
 INSERT INTO auditoria(tabela,item_id,acao,data) VALUES('futuro',NEW.id,'inserir','2026-01-01');
END;
INSERT INTO equipamentos VALUES(1,'COD-1','Sintetico',NULL,NULL,'2026-01-01');
INSERT INTO eventos VALUES(1,'Evento','Sala','Teste','2026-01-01','2026-01-02','Agendado','');
INSERT INTO movimentacoes VALUES(1,1,1,'2026-01-01','saida');
INSERT INTO manutencao VALUES(1,1,'2026-01-01','Oficina','Em manutenção');
INSERT INTO futuro VALUES(1,X'010203');
"""


def sha(caminho):
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


class BackupSQLite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="seagbh-backup-test-")
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name).resolve()
        self.origem = self.raiz / "origem # sintética.db"
        self.pasta = self.raiz / "backups"
        self.pasta.mkdir()
        self.conn = sqlite3.connect(self.origem, timeout=0.01)
        self.addCleanup(self.conn.close)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(DDL)
        self.conn.commit()

    def criar(self, **kwargs):
        return servico.criar_backup(backend="sqlite", origem=self.origem,
                                   pasta=self.pasta, **kwargs)

    def arquivos(self):
        return sorted(p for p in self.pasta.iterdir() if p.suffix == ".db")

    def assert_sem_staging(self):
        self.assertFalse(any(p.name.startswith(".seagbh") for p in self.pasta.iterdir()))

    def assert_erro(self, codigo, chamada):
        with self.assertRaises(servico.ErroBackup) as contexto:
            chamada()
        self.assertEqual(contexto.exception.codigo, codigo)
        return contexto.exception

    def test_backup_preserva_conteudo_tabelas_indices_views_triggers(self):
        antes = sha(self.origem)
        resultado = self.criar()
        with closing(sqlite3.connect(resultado.caminho)) as copia:
            self.assertEqual(list(self.conn.iterdump()), list(copia.iterdump()))
            self.assertEqual(copia.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(copia.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(sha(self.origem), antes)
        self.assertEqual(resultado.sha256, sha(resultado.caminho))
        self.assertEqual(resultado.tamanho, resultado.caminho.stat().st_size)
        self.assertTrue(resultado.validacao.independente)
        self.assertTrue(resultado.validacao.esquema_reconhecido)
        self.assertTrue(resultado.validacao.integridade_fisica)
        self.assertTrue(resultado.validacao.integridade_referencial)
        self.assertEqual(resultado.criado_em.tzinfo, timezone.utc)
        self.assertEqual(resultado.retencao.avisos, ())
        self.assertEqual(resultado.avisos, ())
        self.assert_sem_staging()

    def test_validacao_publica_de_artefato_fechado(self):
        resultado = self.criar(manter=None)
        validacao = servico.validar_backup(backend="sqlite", caminho=resultado.caminho)
        self.assertEqual(validacao, resultado.validacao)
        self.assertIsNone(resultado.retencao)

    def test_wal_com_leitor_antigo_checkpoint_ocupado_e_commits(self):
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA wal_autocheckpoint=0")
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        with closing(sqlite3.connect(self.origem)) as leitor:
            leitor.execute("BEGIN")
            leitor.execute("SELECT COUNT(*) FROM equipamentos").fetchone()
            self.conn.execute("INSERT INTO equipamentos VALUES(2,'COD-2','Segundo',NULL,NULL,'2026-01-01')")
            self.conn.commit()
            self.conn.execute("PRAGMA busy_timeout=1")
            checkpoint = self.conn.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
            self.assertEqual(checkpoint[0], 1)
            self.assertGreater(checkpoint[1], checkpoint[2])
            resultado = self.criar()
            self.assertEqual(self.conn.execute("PRAGMA journal_mode").fetchone(), ("wal",))
            self.assertTrue(Path(str(self.origem) + "-wal").exists())
            with closing(sqlite3.connect(resultado.caminho)) as copia:
                self.assertEqual(copia.execute("SELECT COUNT(*) FROM equipamentos").fetchone(), (2,))
            leitor.rollback()
        self.assertEqual(resultado.caminho.read_bytes()[18:20], b"\x01\x01")
        for sufixo in ("-wal", "-shm", "-journal"):
            self.assertFalse(Path(str(resultado.caminho) + sufixo).exists())

    def test_escrita_concorrente_controlada_durante_snapshot(self):
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("CREATE TABLE volume (id INTEGER PRIMARY KEY, bloco BLOB)")
        self.conn.executemany("INSERT INTO volume(bloco) VALUES(?)", [(b"x" * 2048,)] * 100)
        self.conn.commit()
        iniciar = threading.Event()
        concluido = threading.Event()
        falhas = []

        def escrever():
            try:
                if not iniciar.wait(3):
                    raise TimeoutError("barreira não liberada")
                with closing(sqlite3.connect(self.origem, timeout=1)) as conn:
                    conn.execute("PRAGMA foreign_keys=ON")
                    with conn:
                        conn.execute("INSERT INTO equipamentos VALUES(2,'COD-2','Segundo',NULL,NULL,'2026-01-01')")
                        conn.execute("INSERT INTO futuro VALUES(2,X'04')")
            except Exception as exc:
                falhas.append(exc)
            finally:
                concluido.set()

        worker = threading.Thread(target=escrever, daemon=True)
        worker.start()

        def progresso(restantes, total):
            if restantes > 0 and not iniciar.is_set():
                iniciar.set()
                if not concluido.wait(3):
                    raise TimeoutError("escritor bloqueado")

        try:
            resultado = self.criar(paginas=1, progresso=progresso, prazo=5)
        finally:
            iniciar.set()
            worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(falhas, [])
        self.assertTrue(concluido.is_set())
        with closing(sqlite3.connect(resultado.caminho)) as copia:
            self.assertEqual(copia.execute("SELECT COUNT(*) FROM equipamentos").fetchone(), (2,))
            self.assertEqual(copia.execute("SELECT COUNT(*) FROM futuro").fetchone(), (2,))
            self.assertEqual(copia.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_orfaos_reprovam_sem_publicar_mesmo_com_integridade_fisica(self):
        self.conn.execute("PRAGMA foreign_keys=OFF")
        self.conn.execute("INSERT INTO manutencao VALUES(2,999,'2026-01-01','Teste','Aberto')")
        self.conn.commit()
        self.assertEqual(self.conn.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
        self.assert_erro("integridade_referencial", self.criar)
        self.assertEqual(list(self.pasta.iterdir()), [])

    def test_origem_inexistente_nao_cria_banco(self):
        caminho = self.raiz / "ausente.db"
        self.assert_erro("caminho_invalido", lambda: servico.criar_backup(
            backend="sqlite", origem=caminho, pasta=self.pasta))
        self.assertFalse(caminho.exists())

    def test_origem_corrompida_nao_publica(self):
        invalido = self.raiz / "invalido.db"
        invalido.write_bytes(b"nao e sqlite")
        with self.assertRaises(servico.ErroBackup):
            servico.criar_backup(backend="sqlite", origem=invalido, pasta=self.pasta)
        self.assertEqual(list(self.pasta.iterdir()), [])

    def test_banco_vazio_nao_e_esquema_reconhecido(self):
        vazio = self.raiz / "vazio.db"
        with closing(sqlite3.connect(vazio)) as conn:
            conn.execute("CREATE TABLE outra (id INTEGER PRIMARY KEY)")
        self.assert_erro("esquema_nao_reconhecido", lambda: servico.criar_backup(
            backend="sqlite", origem=vazio, pasta=self.pasta))
        self.assertEqual(list(self.pasta.iterdir()), [])

    def test_coluna_obrigatoria_ausente_reprova(self):
        self.conn.execute("ALTER TABLE auditoria RENAME COLUMN acao TO outra")
        self.conn.commit()
        self.assert_erro("esquema_nao_reconhecido", self.criar)

    def test_id_sem_chave_primaria_reprova(self):
        self.conn.execute("DROP TABLE manutencao")
        self.conn.execute("CREATE TABLE manutencao(id, equipamento_id, data_saida, local_manutencao, status)")
        self.conn.commit()
        self.assert_erro("esquema_nao_reconhecido", self.criar)

    def test_validacao_rejeita_cabecalho_corrompido(self):
        resultado = self.criar()
        with resultado.caminho.open("r+b") as arquivo:
            arquivo.write(b"invalido")
        self.assert_erro("arquivo_nao_independente", lambda: servico.validar_backup(
            backend="sqlite", caminho=resultado.caminho))

    def test_integrity_check_detecta_pagina_corrompida(self):
        resultado = self.criar()
        with resultado.caminho.open("r+b") as arquivo:
            arquivo.seek(100)
            arquivo.write(b"\xff" * 100)
        with self.assertRaises(servico.ErroBackup) as erro:
            servico.validar_backup(backend="sqlite", caminho=resultado.caminho)
        self.assertIn(erro.exception.codigo, {"sqlite_invalido", "integridade_fisica"})

    def test_validacao_rejeita_sidecar_sem_apagar(self):
        resultado = self.criar()
        sidecar = Path(str(resultado.caminho) + "-wal")
        sidecar.write_bytes(b"incompleto")
        self.assert_erro("sidecar_presente", lambda: servico.validar_backup(
            backend="sqlite", caminho=resultado.caminho))
        self.assertEqual(sidecar.read_bytes(), b"incompleto")

    def test_destino_inexistente_ou_arquivo_reprovado(self):
        for pasta in (self.raiz / "ausente", self.origem):
            with self.subTest(pasta=pasta.name), self.assertRaises(servico.ErroBackup):
                servico.criar_backup(backend="sqlite", origem=self.origem, pasta=pasta)

    def test_caminhos_relativos_e_parent_traversal_reprovados(self):
        for caminho in (Path("relativo.db"), self.raiz / ".." / self.raiz.name / self.origem.name):
            with self.subTest(caminho=str(caminho)):
                self.assert_erro("caminho_inseguro", lambda: servico.criar_backup(
                    backend="sqlite", origem=caminho, pasta=self.pasta))

    def test_origem_na_pasta_de_backups_recusada(self):
        self.assert_erro("origem_na_pasta_de_backups", lambda: servico.criar_backup(
            backend="sqlite", origem=self.origem, pasta=self.raiz))

    def test_origem_em_subdiretorios_de_backups_recusada_sem_alteracoes(self):
        self.criar(manter=None)
        for subpasta in ("subpasta", "subpasta/nivel2/nivel3"):
            with self.subTest(subpasta=subpasta):
                origem = self.pasta / subpasta / "origem.db"
                origem.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(origem)) as conn:
                    conn.executescript(DDL)
                    conn.commit()
                servico.validar_backup(backend="sqlite", caminho=origem)
                hash_origem = sha(origem)
                antes = {p.relative_to(self.pasta): sha(p) if p.is_file() else None
                         for p in self.pasta.rglob("*")}

                self.assert_erro("origem_na_pasta_de_backups", lambda: servico.criar_backup(
                    backend="sqlite", origem=origem, pasta=self.pasta, manter=1))

                depois = {p.relative_to(self.pasta): sha(p) if p.is_file() else None
                          for p in self.pasta.rglob("*")}
                self.assertEqual(depois, antes)
                self.assertEqual(sha(origem), hash_origem)
                self.assert_sem_staging()

    def test_hardlink_de_origem_recusado(self):
        alias = self.raiz / "alias.db"
        os.link(self.origem, alias)
        self.assert_erro("arquivo_invalido", self.criar)

    def test_links_simbolicos_recusados_quando_disponiveis(self):
        alias = self.raiz / "link"
        try:
            alias.symlink_to(self.pasta, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlink indisponível: {type(exc).__name__}")
        self.assert_erro("link_ou_reparse_point", lambda: servico.criar_backup(
            backend="sqlite", origem=self.origem, pasta=alias))

    def test_duas_criacoes_mesmo_instante_nao_colidem(self):
        instante = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch.object(servico, "datetime", wraps=datetime) as relogio:
            relogio.now.return_value = instante
            a = self.criar(manter=None)
            b = self.criar(manter=None)
        self.assertNotEqual(a.caminho, b.caminho)
        self.assertNotEqual(a.identificador, b.identificador)
        self.assertEqual(len(self.arquivos()), 2)

    def test_colisao_real_na_publicacao_nao_sobrescreve(self):
        link_real = os.link
        colisao = []

        def publicar(origem, destino):
            # Concorrente cria o nome final no instante da publicação.
            destino.write_bytes(b"arquivo preexistente")
            colisao.append(destino)
            return link_real(origem, destino)

        with patch.object(servico.os, "link", side_effect=publicar):
            erro = self.assert_erro("operacao_falhou", self.criar)
        self.assertEqual(erro.fase, "publicacao")
        self.assertEqual(colisao[0].read_bytes(), b"arquivo preexistente")
        self.assert_sem_staging()

    def test_falhas_snapshot_validacao_publicacao_preservam_anteriores(self):
        anterior = self.criar(manter=None)
        hash_anterior = sha(anterior.caminho)
        falhas = [
            ("_abrir_leitura", sqlite3.OperationalError("falha snapshot")),
            ("_validar", servico.ErroBackup("validacao", "falha_injetada")),
            ("os.link", OSError("filesystem sem hardlink")),
        ]
        for alvo, erro in falhas:
            with self.subTest(fase=alvo):
                with patch("src.core.backup_sqlite." + alvo, side_effect=erro):
                    with self.assertRaises(servico.ErroBackup):
                        self.criar(manter=1)
                self.assertEqual(sha(anterior.caminho), hash_anterior)
                self.assertEqual(self.arquivos(), [anterior.caminho])
                self.assert_sem_staging()

    def test_falha_apos_algumas_paginas_limpa_snapshot_parcial(self):
        self.conn.execute("CREATE TABLE volume(bloco)")
        self.conn.executemany("INSERT INTO volume VALUES(?)", [(b"x" * 4096,)] * 20)
        self.conn.commit()
        chamadas = []

        def falhar(restantes, total):
            chamadas.append((restantes, total))
            raise OSError("falha controlada após passo de snapshot")

        self.assert_erro("operacao_falhou", lambda: self.criar(paginas=1, progresso=falhar))
        self.assertGreater(chamadas[0][0], 0)
        self.assertEqual(list(self.pasta.iterdir()), [])

    def test_cancelamento_antes_e_durante_snapshot(self):
        self.assert_erro("cancelado", lambda: self.criar(cancelar=lambda: True))
        cancelado = threading.Event()
        self.assert_erro("cancelado", lambda: self.criar(
            paginas=1, cancelar=cancelado.is_set,
            progresso=lambda restantes, total: cancelado.set()))
        self.assertEqual(list(self.pasta.iterdir()), [])

    def test_banco_bloqueado_respeita_prazo_cooperativo(self):
        self.conn.execute("BEGIN EXCLUSIVE")
        inicio = time.monotonic()
        try:
            self.assert_erro("prazo_excedido", lambda: self.criar(prazo=0.2))
        finally:
            self.conn.rollback()
        self.assertLess(time.monotonic() - inicio, 3)
        self.assertEqual(list(self.pasta.iterdir()), [])

    def test_falha_fsync_antes_publicacao_nao_publica(self):
        with patch.object(servico.os, "fsync", side_effect=OSError("fsync falhou")):
            self.assert_erro("operacao_falhou", self.criar)
        self.assertEqual(list(self.pasta.iterdir()), [])

    def test_sem_espaco_ao_criar_staging_preserva_anterior(self):
        anterior = self.criar(manter=None)
        hash_anterior = sha(anterior.caminho)
        abrir_real = os.open

        def falhar(caminho, *args, **kwargs):
            if Path(caminho).name == "snapshot.partial":
                raise OSError(errno.ENOSPC, "sem espaco sintetico")
            return abrir_real(caminho, *args, **kwargs)

        with patch.object(servico.os, "open", side_effect=falhar):
            erro = self.assert_erro("operacao_falhou", self.criar)
        self.assertEqual(erro.erro_sistema, errno.ENOSPC)
        self.assertEqual(sha(anterior.caminho), hash_anterior)
        self.assertEqual(self.arquivos(), [anterior.caminho])
        self.assert_sem_staging()

    def test_lock_existente_recusa_sem_remover_lock_alheio(self):
        lock = self.pasta / ".seagbh-backup.lock"
        lock.write_bytes(b"existente")
        self.assert_erro("pasta_ocupada", self.criar)
        self.assertEqual(lock.read_bytes(), b"existente")

    def test_lock_reentrante_impede_retencao_durante_criacao(self):
        tentativas = []

        def progresso(restantes, total):
            if not tentativas:
                erro = self.assert_erro("pasta_ocupada", lambda: servico.aplicar_retencao(
                    backend="sqlite", pasta=self.pasta))
                tentativas.append(erro.codigo)

        resultado = self.criar(progresso=progresso)
        self.assertEqual(tentativas, ["pasta_ocupada"])
        self.assertTrue(resultado.caminho.exists())

    def test_reparse_point_recusado_mesmo_sem_privilegio_para_criar_link(self):
        lstat_real = Path.lstat

        class Reparse:
            st_mode = stat.S_IFDIR
            st_file_attributes = 0x400

        def simular(caminho, *args, **kwargs):
            return Reparse() if caminho == self.pasta else lstat_real(caminho, *args, **kwargs)

        with patch.object(Path, "lstat", simular):
            self.assert_erro("link_ou_reparse_point", self.criar)

    def test_argumentos_invalidos_recusados_sem_criar_arquivos(self):
        for kwargs in ({"paginas": 0}, {"manter": False}, {"prazo": float("inf")},
                       {"prazo": 0}, {"cancelar": 1}, {"progresso": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(servico.ErroBackup):
                self.criar(**kwargs)
        self.assertEqual(list(self.pasta.iterdir()), [])

    def test_retencao_padrao_dez_ordena_por_criacao_nao_mtime(self):
        inicio = datetime(2026, 1, 1, tzinfo=timezone.utc)
        instantes = [inicio + timedelta(seconds=i) for i in range(12)]
        # wraps preserva strptime, usado para validar nomes durante a retenção.
        with patch.object(servico, "datetime", wraps=datetime) as relogio:
            relogio.now.side_effect = instantes
            criados = [self.criar(manter=None) for _ in range(11)]
            for indice, resultado in enumerate(criados):
                os.utime(resultado.caminho, (1000 - indice, 1000 - indice))
            novo = self.criar()
        self.assertEqual([r.criado_em for r in [*criados, novo]], instantes)
        self.assertEqual(len(self.arquivos()), 10)
        self.assertFalse(criados[0].caminho.exists())
        self.assertFalse(criados[1].caminho.exists())
        self.assertTrue(novo.caminho.exists())
        self.assertEqual(len(novo.retencao.removidos), 2)
        self.assertEqual(novo.retencao.validos_preservados, 10)

    def test_retencao_com_timestamps_iguais_desempata_por_nome_e_protege_novo(self):
        instante = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # Ordem de UUID inversa à criação; o novo seria o primeiro por nome.
        with patch.object(servico, "datetime", wraps=datetime) as relogio, \
                patch.object(servico, "uuid4", side_effect=[UUID(int=i) for i in range(11, -1, -1)]):
            relogio.now.return_value = instante
            criados = [self.criar(manter=None) for _ in range(11)]
            novo = self.criar()
        self.assertEqual({r.criado_em for r in [*criados, novo]}, {instante})
        self.assertLess(novo.caminho.name, min(r.caminho.name for r in criados))
        esperados = tuple(sorted((r.caminho for r in criados), key=lambda p: p.name)[:2])
        self.assertEqual(novo.retencao.removidos, esperados)
        self.assertEqual(len(novo.retencao.removidos), 2)
        self.assertTrue(novo.caminho.exists())
        self.assertNotIn(novo.caminho, novo.retencao.removidos)
        self.assertEqual(len(self.arquivos()), 10)
        self.assertEqual(novo.retencao.validos_preservados, 10)
        self.assertEqual(novo.retencao.avisos, ())
        for caminho in self.arquivos():
            self.assertTrue(servico.validar_backup(backend="sqlite", caminho=caminho).integridade_fisica)

    def test_retencao_preserva_legado_parcial_resgate_e_gerenciado_invalido(self):
        for nome in ("seaghb_backup_2026-01-01.db", "resgate.db", "snapshot.partial",
                     "seagbh-sqlite-v1-20260101T000000000000Z-" + "0" * 32 + ".db"):
            (self.pasta / nome).write_bytes(b"nao gerenciado ou invalido")
        anteriores = {p: p.read_bytes() for p in self.pasta.iterdir()}
        a = self.criar(manter=None)
        b = self.criar(manter=1)
        self.assertFalse(a.caminho.exists())
        self.assertTrue(b.caminho.exists())
        for caminho, conteudo in anteriores.items():
            self.assertEqual(caminho.read_bytes(), conteudo)
        self.assertIn("candidato_nao_validado_preservado", b.retencao.avisos)

    def test_retencao_nunca_remove_ultima_valida(self):
        unico = self.criar()
        resultado = servico.aplicar_retencao(backend="sqlite", pasta=self.pasta, manter=1)
        self.assertEqual(resultado.removidos, ())
        self.assertTrue(unico.caminho.exists())
        self.assert_erro("retencao_invalida", lambda: self.criar(manter=0))

    def test_falha_exclusao_retorna_backup_criado_com_aviso(self):
        anterior = self.criar(manter=None)
        hash_anterior = sha(anterior.caminho)
        unlink_real = Path.unlink

        def falhar(caminho, *args, **kwargs):
            if caminho == anterior.caminho:
                raise PermissionError("exclusão negada")
            return unlink_real(caminho, *args, **kwargs)

        with patch.object(Path, "unlink", falhar):
            novo = self.criar(manter=1)
        self.assertTrue(novo.caminho.exists())
        self.assertEqual(sha(anterior.caminho), hash_anterior)
        self.assertIn("retencao_incompleta:erro_io", novo.retencao.avisos)
        self.assertEqual(len(self.arquivos()), 2)

    def test_retencao_publica_remove_somente_excedentes_validos(self):
        criados = [self.criar(manter=None) for _ in range(3)]
        resultado = servico.aplicar_retencao(backend="sqlite", pasta=self.pasta, manter=1)
        self.assertEqual(set(resultado.removidos), {r.caminho for r in criados[:2]})
        self.assertEqual(self.arquivos(), [criados[-1].caminho])
        self.assertEqual(resultado.validos_preservados, 1)
        self.assertEqual(resultado.avisos, ())

    def test_retencao_preserva_arquivo_com_outro_hardlink(self):
        anterior = self.criar(manter=None)
        alias = self.raiz / "preservado.db"
        os.link(anterior.caminho, alias)
        novo = self.criar(manter=1)
        self.assertTrue(anterior.caminho.exists())
        self.assertEqual(sha(alias), anterior.sha256)
        self.assertTrue(novo.caminho.exists())
        self.assertIn("candidato_nao_validado_preservado", novo.retencao.avisos)

    def test_erro_inesperado_de_retencao_nao_oculta_publicacao(self):
        anterior = self.criar(manter=None)
        with patch.object(servico, "_retencao", side_effect=RuntimeError("falha sintetica")):
            novo = self.criar()
        self.assertTrue(novo.caminho.exists())
        self.assertEqual(sha(anterior.caminho), anterior.sha256)
        self.assertIn("pos_publicacao_incompleta:RuntimeError", novo.avisos)

    def test_cancelamento_apos_publicacao_vira_aviso_de_retencao(self):
        publicado = threading.Event()
        sincronizar = servico._sincronizar_pasta

        def concluir(pasta):
            publicado.set()
            sincronizar(pasta)

        with patch.object(servico, "_sincronizar_pasta", side_effect=concluir):
            resultado = self.criar(cancelar=publicado.is_set)
        self.assertTrue(resultado.caminho.exists())
        self.assertIn("retencao_incompleta:cancelado", resultado.retencao.avisos)

    def test_falha_limpeza_staging_apos_publicacao_e_visivel(self):
        unlink_real = Path.unlink

        def falhar(caminho, *args, **kwargs):
            if caminho.name == "snapshot.partial":
                raise PermissionError("limpeza negada")
            return unlink_real(caminho, *args, **kwargs)

        with patch.object(Path, "unlink", falhar):
            resultado = self.criar()
        self.assertTrue(resultado.caminho.exists())
        self.assertIn("limpeza_staging_falhou", resultado.avisos)
        self.assertIn("retencao_nao_executada", resultado.avisos)
        self.assertIsNone(resultado.retencao)

    def test_falha_limpeza_antes_publicacao_preserva_erro_e_avisa(self):
        unlink_real = Path.unlink

        def falhar(caminho, *args, **kwargs):
            if caminho.name == "snapshot.partial":
                raise PermissionError("limpeza negada")
            return unlink_real(caminho, *args, **kwargs)

        with patch.object(Path, "unlink", falhar), patch.object(
                servico, "_validar", side_effect=servico.ErroBackup("validacao", "falha_injetada")):
            erro = self.assert_erro("falha_injetada", self.criar)
        self.assertIn("limpeza_staging_falhou", erro.avisos)
        self.assertEqual(self.arquivos(), [])

    def test_falha_sincronizacao_apos_publicacao_nao_finge_criacao_falha(self):
        with patch.object(servico, "_sincronizar_pasta", side_effect=OSError("io")):
            resultado = self.criar()
        self.assertEqual(resultado.sha256, sha(resultado.caminho))
        self.assertIn("sincronizacao_diretorio_nao_confirmada", resultado.avisos)
        self.assertIn("retencao_nao_executada", resultado.avisos)

    def test_diagnostico_nao_expoe_texto_bruto_de_excecao(self):
        with patch.object(servico, "_abrir_leitura", side_effect=OSError("conteudo privado sintetico")):
            erro = self.assert_erro("operacao_falhou", self.criar)
        self.assertNotIn("conteudo privado", str(erro))
        self.assertEqual(erro.causa, "OSError")
        self.assertEqual(erro.fase, "snapshot")

    def test_backend_postgres_recusado_antes_de_sql_ou_io(self):
        with patch.object(servico, "_caminho") as caminho, patch.object(sqlite3, "connect") as conectar:
            chamadas = (
                lambda: servico.criar_backup(backend="postgres", origem=self.origem, pasta=self.pasta),
                lambda: servico.validar_backup(backend="postgres", caminho=self.origem),
                lambda: servico.aplicar_retencao(backend="postgres", pasta=self.pasta),
            )
            for chamada in chamadas:
                self.assert_erro("backend_nao_suportado", chamada)
            caminho.assert_not_called()
            conectar.assert_not_called()

    def test_origem_aberta_exclusivamente_mode_ro_e_conexoes_fechadas(self):
        conectar_real = sqlite3.connect
        conexoes = []
        aberturas = []

        def conectar(caminho, *args, **kwargs):
            conn = conectar_real(caminho, *args, **kwargs)
            conexoes.append(conn)
            aberturas.append((str(caminho), kwargs))
            return conn

        with patch.object(servico.sqlite3, "connect", side_effect=conectar):
            resultado = self.criar()
        fontes = [(uri, args) for uri, args in aberturas if uri.startswith(self.origem.as_uri())]
        self.assertTrue(fontes)
        self.assertTrue(all(uri.endswith("?mode=ro") and args["uri"] for uri, args in fontes))
        for conn in conexoes:
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")
        self.assertTrue(resultado.caminho.exists())

    def test_conexoes_fechadas_apos_falha_no_meio_do_snapshot(self):
        conectar_real = sqlite3.connect
        conexoes = []

        def conectar(*args, **kwargs):
            conn = conectar_real(*args, **kwargs)
            conexoes.append(conn)
            return conn

        def falhar(restantes, total):
            raise RuntimeError("falha sintetica")

        with patch.object(servico.sqlite3, "connect", side_effect=conectar):
            self.assert_erro("operacao_falhou", lambda: self.criar(progresso=falhar))
        self.assertEqual(len(conexoes), 2)
        for conn in conexoes:
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def test_modulo_isolado_sem_importar_ui_database_ou_rede(self):
        programa = """
import importlib.abc, sys
class Bloquear(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'PyQt6','psycopg','firebase_admin','requests','socket'} or fullname in {'src.core.database','core.database'}:
            raise RuntimeError('import proibido: ' + fullname)
sys.meta_path.insert(0, Bloquear())
import src.core.backup_sqlite
resultado = src.core.backup_sqlite.criar_backup(backend='sqlite', origem=sys.argv[1], pasta=sys.argv[2])
src.core.backup_sqlite.validar_backup(backend='sqlite', caminho=resultado.caminho)
src.core.backup_sqlite.aplicar_retencao(backend='sqlite', pasta=sys.argv[2])
"""
        resultado = subprocess.run([sys.executable, "-B", "-c", programa,
                                    str(self.origem), str(self.pasta)],
                                   capture_output=True, text=True, timeout=5)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)


if __name__ == "__main__":
    unittest.main()
