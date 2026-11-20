"""Locks reais e subprocessos independentes; somente diretórios temporários."""

from contextlib import closing
import copy
import errno
import gc
import hashlib
import json
import os
from pathlib import Path
import pickle
import queue
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import warnings

from src.core import barreira_sqlite as servico


_CLIENTE = r'''
import json,sys
from pathlib import Path
from src.core import barreira_sqlite as s
banco=Path(sys.argv[1])
concessao=None
def emitir(**dados):
    print(json.dumps(dados),flush=True)
emitir(evento='pronto')
try:
    for linha in sys.stdin:
        comando=json.loads(linha)
        acao=comando['acao']
        if acao=='sair':
            break
        if acao=='liberar':
            concessao.fechar()
            emitir(evento='liberada',estado=concessao.consultar_estado().value)
            concessao=None
            continue
        if acao=='ler_sem_barreira':
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(banco.as_uri()+'?mode=ro',uri=True)) as c:
                valor=c.execute('PRAGMA user_version').fetchone()[0]
            emitir(evento='leitura_externa',valor=valor)
            continue
        obter=s.adquirir_manutencao if comando['tipo']=='manutencao' else s.adquirir_uso_operacional
        plataforma=s._plataforma
        if comando.get('notificar_contencao'):
            real=plataforma()
            class Observador:
                anunciado=False
                def abrir(self,p):
                    return real.abrir(p)
                def tentar(self,fd,exclusiva):
                    ok=real.tentar(fd,exclusiva)
                    if not ok and not self.anunciado:
                        self.anunciado=True
                        emitir(evento='contencao_real')
                    return ok
                def liberar(self,fd):
                    return real.liberar(fd)
            observador=Observador()
            s._plataforma=lambda:observador
        try:
            if acao=='excecao':
                try:
                    with obter(backend='sqlite',banco=banco):
                        raise RuntimeError('falha sintetica')
                except RuntimeError:
                    emitir(evento='excecao_liberada')
            else:
                concessao=obter(backend='sqlite',banco=banco,prazo=comando.get('prazo',0))
                emitir(evento='adquirida',tipo=concessao.tipo.value,estado=concessao.consultar_estado().value)
        except s.ErroBarreira as erro:
            emitir(evento='erro',codigo=erro.codigo)
        finally:
            s._plataforma=plataforma
finally:
    if concessao is not None:
        concessao.fechar()
'''


class _Processo:
    def __init__(self, banco):
        self.fila = queue.Queue()
        self.abrupto = False
        self.processo = subprocess.Popen(
            [sys.executable, "-B", "-W", "error::ResourceWarning", "-u", "-c", _CLIENTE, str(banco)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"), close_fds=True)
        self.leitor = threading.Thread(target=self._ler, daemon=True)
        self.leitor.start()

    def _ler(self):
        for linha in self.processo.stdout:
            self.fila.put(linha)
        self.fila.put(None)

    def enviar(self, acao, **dados):
        self.processo.stdin.write(json.dumps(dict(acao=acao, **dados)) + "\n")
        self.processo.stdin.flush()

    def receber(self):
        try:
            linha = self.fila.get(timeout=10)
        except queue.Empty:
            raise AssertionError("Subprocesso não respondeu ao protocolo em 10s.") from None
        if linha is None:
            raise AssertionError("Subprocesso encerrou antes da resposta.")
        return json.loads(linha)

    def matar(self):
        self.abrupto = True
        self.processo.kill()
        self.processo.wait(timeout=10)

    def fechar(self):
        if self.processo.poll() is None:
            try:
                self.enviar("sair")
            except (BrokenPipeError, OSError):
                self.abrupto = True
                self.processo.kill()
            try:
                self.processo.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.abrupto = True
                self.processo.kill()
                self.processo.wait(timeout=5)
        erros = self.processo.stderr.read()
        self.leitor.join(timeout=5)
        for canal in (self.processo.stdin, self.processo.stdout, self.processo.stderr):
            canal.close()
        if self.leitor.is_alive():
            raise AssertionError("Leitor do subprocesso não encerrou.")
        if erros or (not self.abrupto and self.processo.returncode != 0):
            raise AssertionError("Subprocesso sintético falhou: " + erros)


class BarreiraSQLite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="seagbh-barreira-")
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name).resolve()
        self.banco = self.raiz / "banco sintético.db"
        # Banco sintético válido, sem DDL, journal_mode, checkpoint ou Database.
        with closing(sqlite3.connect(self.banco)) as conn:
            conn.execute("PRAGMA user_version=7")
            conn.commit()
        self.coordenacao = servico.caminho_coordenacao(backend="sqlite", banco=self.banco)

    def obter(self, tipo="operacional", **kwargs):
        funcao = servico.adquirir_manutencao if tipo == "manutencao" else servico.adquirir_uso_operacional
        return funcao(**dict(dict(backend="sqlite", banco=self.banco), **kwargs))

    def processo(self):
        cliente = _Processo(self.banco)
        self.addCleanup(cliente.fechar)
        self.assertEqual(cliente.receber(), {"evento": "pronto"})
        return cliente

    def erro(self, codigo, chamada):
        with self.assertRaises(servico.ErroBarreira) as contexto:
            chamada()
        self.assertEqual(contexto.exception.codigo, codigo)
        return contexto.exception

    def estado_arquivos(self):
        return {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                for p in self.raiz.iterdir() if p.is_file() and p != self.coordenacao}

    def test_dois_processos_compartilhados_coexistem_e_impedem_manutencao(self):
        clientes = [self.processo(), self.processo()]
        for cliente in clientes:
            cliente.enviar("adquirir", tipo="operacional")
            self.assertEqual(cliente.receber()["evento"], "adquirida")
        self.erro("ocupado", lambda: self.obter("manutencao"))
        clientes[0].enviar("liberar")
        self.assertEqual(clientes[0].receber()["estado"], "liberada")
        self.erro("ocupado", lambda: self.obter("manutencao"))
        clientes[1].enviar("liberar")
        self.assertEqual(clientes[1].receber()["estado"], "liberada")
        with self.obter("manutencao") as concessao:
            self.assertEqual(concessao.consultar_estado(), servico.EstadoConcessao.ATIVA)

    def test_concessoes_no_mesmo_processo_tem_recursos_e_liberacoes_independentes(self):
        primeira = self.obter()
        self.addCleanup(primeira.fechar)
        segunda = self.obter()
        self.addCleanup(segunda.fechar)
        self.assertNotEqual(primeira._Concessao__fd, segunda._Concessao__fd)
        self.erro("ocupado", lambda: self.obter("manutencao"))
        cliente = self.processo()
        cliente.enviar("adquirir", tipo="manutencao")
        self.assertEqual(cliente.receber(), {"evento": "erro", "codigo": "ocupado"})

        erros = []
        def liberar_primeira():
            try:
                primeira.fechar()
            except BaseException as exc:
                erros.append(exc)
        thread = threading.Thread(target=liberar_primeira, daemon=True)
        thread.start()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "Fechamento não terminou em 10s.")
        self.assertEqual(erros, [])
        primeira.fechar()  # A repetição não pode desbloquear a segunda concessão.
        self.assertEqual(primeira.consultar_estado(), servico.EstadoConcessao.LIBERADA)
        self.assertEqual(segunda.consultar_estado(), servico.EstadoConcessao.ATIVA)
        self.assertEqual(segunda.tipo, servico.TipoConcessao.OPERACIONAL)
        self.erro("ocupado", lambda: self.obter("manutencao"))
        cliente.enviar("adquirir", tipo="manutencao")
        self.assertEqual(cliente.receber(), {"evento": "erro", "codigo": "ocupado"})
        segunda.fechar()
        cliente.enviar("adquirir", tipo="manutencao")
        self.assertEqual(cliente.receber()["evento"], "adquirida")

    def test_manutencao_recusa_cliente_e_segunda_manutencao_entre_processos(self):
        dono, outro = self.processo(), self.processo()
        dono.enviar("adquirir", tipo="manutencao")
        self.assertEqual(dono.receber()["tipo"], "manutencao")
        for tipo in ("operacional", "manutencao"):
            self.erro("ocupado", lambda: self.obter(tipo))
            outro.enviar("adquirir", tipo=tipo)
            self.assertEqual(outro.receber(), {"evento": "erro", "codigo": "ocupado"})

    def test_disputa_concorrente_de_manutencao_tem_um_unico_vencedor(self):
        clientes = [self.processo(), self.processo()]
        for cliente in clientes:
            cliente.enviar("adquirir", tipo="manutencao")
        respostas = [c.receber() for c in clientes]
        self.assertEqual(sum(r["evento"] == "adquirida" for r in respostas), 1)
        self.assertEqual(sum(r.get("codigo") == "ocupado" for r in respostas), 1)
        self.erro("ocupado", self.obter)

    def test_aquisicao_aguarda_liberacao_apos_contencao_real_sem_sleep_no_teste(self):
        cliente = self.processo()
        with self.obter() as primeira:
            cliente.enviar("adquirir", tipo="manutencao", prazo=5, notificar_contencao=True)
            self.assertEqual(cliente.receber(), {"evento": "contencao_real"})
            self.assertEqual(primeira.consultar_estado(), servico.EstadoConcessao.ATIVA)
        self.assertEqual(cliente.receber()["evento"], "adquirida")
        self.erro("ocupado", self.obter)

    def test_timeout_real_enquanto_outro_processo_mantem_concessao(self):
        cliente = self.processo()
        cliente.enviar("adquirir", tipo="operacional")
        self.assertEqual(cliente.receber()["evento"], "adquirida")
        inicio = time.monotonic()
        self.erro("prazo_excedido", lambda: self.obter("manutencao", prazo=0.15))
        self.assertGreaterEqual(time.monotonic() - inicio, 0.1)
        self.erro("ocupado", lambda: self.obter("manutencao"))

    def test_crash_libera_lock_compartilhado_e_exclusivo_sem_apagar_arquivo(self):
        for tipo in ("operacional", "manutencao"):
            with self.subTest(tipo=tipo):
                cliente = self.processo()
                cliente.enviar("adquirir", tipo=tipo)
                self.assertEqual(cliente.receber()["evento"], "adquirida")
                identidade = self.coordenacao.stat().st_ino
                self.erro("ocupado", lambda: self.obter("manutencao"))
                cliente.matar()
                with self.obter("manutencao", prazo=2):
                    self.assertEqual(self.coordenacao.stat().st_ino, identidade)

    def test_excecao_no_contexto_libera_recursos(self):
        concessao = self.obter("manutencao")
        with self.assertRaisesRegex(ValueError, "falha sintética"):
            with concessao:
                raise ValueError("falha sintética")
        self.assertEqual(concessao.consultar_estado(), servico.EstadoConcessao.LIBERADA)
        with self.obter("manutencao"):
            pass

    def test_excecao_em_subprocesso_libera_lock_real(self):
        cliente = self.processo()
        cliente.enviar("excecao", tipo="manutencao")
        self.assertEqual(cliente.receber(), {"evento": "excecao_liberada"})
        with self.obter("manutencao"):
            pass

    def test_banco_ainda_ausente_nao_e_criado(self):
        banco = self.raiz / "ainda-ausente.db"
        with self.obter("manutencao", banco=banco) as concessao:
            self.assertFalse(banco.exists())
            self.assertTrue(concessao.arquivo_coordenacao.exists())
        self.assertFalse(banco.exists())

    def test_arquivo_coordenacao_estavel_sem_truncar_escrever_ou_recriar(self):
        self.coordenacao.write_bytes(b"coordenacao preexistente sintetica")
        info = self.coordenacao.stat()
        for tipo in ("operacional", "manutencao", "operacional"):
            with self.obter(tipo):
                self.assertEqual(self.coordenacao.stat().st_ino, info.st_ino)
            self.assertEqual(self.coordenacao.read_bytes(), b"coordenacao preexistente sintetica")
            self.assertEqual(self.coordenacao.stat().st_mtime_ns, info.st_mtime_ns)

    def test_banco_sidecars_e_lock_legado_intactos_sem_sqlite_ou_escritas(self):
        for sufixo in ("-wal", "-shm", "-journal", ".lock"):
            Path(str(self.banco) + sufixo).write_bytes(b"auxiliar sintetico")
        antes = self.estado_arquivos()
        with patch.object(sqlite3, "connect", side_effect=AssertionError("SQLite proibido")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("mkdir proibido")), \
                patch.object(os, "replace", side_effect=AssertionError("replace proibido")), \
                patch.object(os, "rename", side_effect=AssertionError("rename proibido")), \
                patch.object(os, "unlink", side_effect=AssertionError("unlink proibido")), \
                patch.object(shutil, "copy2", side_effect=AssertionError("cópia proibida")):
            with self.obter("manutencao"):
                pass
        self.assertEqual(self.estado_arquivos(), antes)

    def test_cliente_sqlite_nao_cooperativo_consegue_ler_durante_manutencao(self):
        cliente = self.processo()
        antes = self.estado_arquivos()
        with self.obter("manutencao"):
            cliente.enviar("ler_sem_barreira")
            self.assertEqual(cliente.receber(), {"evento": "leitura_externa", "valor": 7})
        self.assertEqual(self.estado_arquivos(), antes)

    def test_backend_recusado_antes_de_io(self):
        with patch.object(Path, "lstat") as lstat, patch.object(servico, "_plataforma") as plataforma:
            for funcao in (servico.adquirir_uso_operacional, servico.adquirir_manutencao,
                           servico.caminho_coordenacao):
                self.erro("backend_nao_suportado", lambda: funcao(backend="postgres", banco=self.banco))
        lstat.assert_not_called()
        plataforma.assert_not_called()

    def test_prazos_invalidos_recusados_antes_de_abrir(self):
        invalidos = (-1, -0.5, True, False, float("nan"), float("inf"),
                     -float("inf"), 10**400, -(10**400), "1", None, 1j)
        for funcao in (servico.adquirir_uso_operacional, servico.adquirir_manutencao):
            for prazo in invalidos:
                with self.subTest(api=funcao.__name__, prazo=prazo), \
                        patch.object(servico, "_plataforma") as plataforma, \
                        patch.object(Path, "lstat") as lstat, \
                        patch.object(os, "open") as abrir:
                    erro = self.erro("prazo_invalido", lambda: funcao(
                        backend="sqlite", banco=self.banco, prazo=prazo))
                    self.assertEqual(erro.fase, "argumentos")
                    plataforma.assert_not_called()
                    lstat.assert_not_called()
                    abrir.assert_not_called()

    def test_prazos_validos_e_zero_preservados_nas_duas_apis(self):
        for funcao in (servico.adquirir_uso_operacional, servico.adquirir_manutencao):
            for prazo in (0, 0.0, -0.0, 1, 0.25, sys.float_info.max, int(sys.float_info.max)):
                with self.subTest(api=funcao.__name__, prazo=prazo):
                    with funcao(backend="sqlite", banco=self.banco, prazo=prazo) as concessao:
                        self.assertEqual(concessao.consultar_estado(), servico.EstadoConcessao.ATIVA)
        with self.obter("manutencao"):
            for funcao in (servico.adquirir_uso_operacional, servico.adquirir_manutencao):
                with self.subTest(api=funcao.__name__):
                    self.erro("ocupado", lambda: funcao(backend="sqlite", banco=self.banco, prazo=0))

    @unittest.skipUnless(os.name == "nt", "Aliases de nomes no filesystem Windows.")
    def test_windows_alias_do_nome_reservado_recusado_sem_segunda_coordenacao(self):
        with self.obter():
            pass
        def retrato():
            return {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(),
                             p.stat().st_mtime_ns, p.stat().st_ino)
                    for p in self.raiz.iterdir()}
        antes = retrato()
        nomes = (self.coordenacao.name, self.coordenacao.name.upper(),
                 self.coordenacao.name.replace("access", "AcCeSs"))
        for funcao in (servico.caminho_coordenacao, servico.adquirir_uso_operacional,
                       servico.adquirir_manutencao):
            for nome in nomes:
                alias = self.coordenacao.with_name(nome)
                self.assertTrue(alias.samefile(self.coordenacao))
                def tentar():
                    resultado = funcao(backend="sqlite", banco=alias)
                    if isinstance(resultado, servico.Concessao):
                        resultado.fechar()
                    return resultado
                with self.subTest(api=funcao.__name__, nome=nome):
                    self.erro("banco_e_coordenacao_confundidos", tentar)
                    self.assertEqual(retrato(), antes)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Nomes distintos em filesystem Linux.")
    def test_linux_preserva_distincao_de_caixa_do_sufixo_reservado(self):
        banco = self.raiz / "banco.SEAGBH-ACCESS-V1.LOCK"
        banco.write_bytes(b"arquivo sintetico independente")
        esperado = banco.with_name(banco.name + ".seagbh-access-v1.lock")
        self.assertEqual(servico.caminho_coordenacao(backend="sqlite", banco=banco), esperado)
        for funcao in (servico.adquirir_uso_operacional, servico.adquirir_manutencao):
            with funcao(backend="sqlite", banco=banco) as concessao:
                self.assertEqual(concessao.banco, banco)
                self.assertEqual(concessao.arquivo_coordenacao, esperado)
        self.assertEqual(banco.read_bytes(), b"arquivo sintetico independente")

    def test_caminhos_relativos_traversal_unc_nul_sao_recusados(self):
        for banco in ("relativo.db", self.raiz / ".." / "banco.db", "\\\\servidor\\banco.db",
                      str(self.raiz / "nul.db") + "\x00"):
            with self.subTest(banco=str(banco)):
                self.erro("caminho_inseguro", lambda: self.obter(banco=banco))

    @unittest.skipUnless(os.name == "nt", "Regras específicas do namespace Windows.")
    def test_windows_recusa_ads_dispositivos_e_nomes_ambiguos(self):
        for nome in ("banco.db:stream", "CON", "NUL.db", "COM1", "pasta.", "banco.db "):
            with self.subTest(nome=nome):
                self.erro("caminho_inseguro", lambda: self.obter(banco=self.raiz / nome))

    @unittest.skipUnless(os.name == "nt", "Verificação de volumes Windows.")
    def test_volume_remoto_recusado_antes_de_lstat(self):
        with patch.object(servico._Windows, "validar_volume", side_effect=servico.ErroBarreira(
                "caminhos", "volume_local_nao_confirmado")), patch.object(Path, "lstat") as lstat:
            self.erro("volume_local_nao_confirmado", self.obter)
        lstat.assert_not_called()

    def test_plataforma_sem_api_nao_faz_fallback_ou_io(self):
        with patch.object(servico, "_plataforma", side_effect=servico.ErroBarreira(
                "aquisicao", "plataforma_nao_suportada")), patch.object(Path, "lstat") as lstat:
            self.erro("plataforma_nao_suportada", self.obter)
        lstat.assert_not_called()

    def test_diretorio_ausente_nao_e_criado(self):
        pai = self.raiz / "inexistente"
        self.erro("diretorio_ou_arquivo_ausente", lambda: self.obter(banco=pai / "banco.db"))
        self.assertFalse(pai.exists())
        self.erro("arquivo_nao_regular_ou_com_hardlinks", lambda: self.obter(banco=self.raiz))

    def test_banco_nao_pode_ser_o_arquivo_de_coordenacao(self):
        self.erro("banco_e_coordenacao_confundidos", lambda: self.obter(banco=self.coordenacao))

    def test_hardlinks_reais_de_banco_e_coordenacao_recusados(self):
        alias = self.raiz / "alias.db"
        os.link(self.banco, alias)
        self.erro("arquivo_nao_regular_ou_com_hardlinks", self.obter)
        alias.unlink()  # Somente a fixture altera seus arquivos.
        self.coordenacao.write_bytes(b"sintetico")
        os.link(self.coordenacao, self.raiz / "alias.lock")
        self.erro("arquivo_nao_regular_ou_com_hardlinks", self.obter)

    def test_symlinks_reais_quando_permitidos(self):
        alias = self.raiz / "alias.db"
        try:
            alias.symlink_to(self.banco)
        except OSError as exc:
            self.skipTest("symlink real indisponível: " + type(exc).__name__)
        self.erro("link_ou_reparse_point", lambda: self.obter(banco=alias))
        alias.unlink()
        diretorio = self.raiz / "link-pasta"
        diretorio.symlink_to(self.raiz, target_is_directory=True)
        self.erro("link_ou_reparse_point", lambda: self.obter(banco=diretorio / self.banco.name))
        self.coordenacao.symlink_to(self.banco)
        self.erro("link_ou_reparse_point", self.obter)

    def test_reparse_point_simulado_em_banco_e_ancestral(self):
        original = Path.lstat
        for alvo in (self.banco, self.raiz):
            def observar(caminho, *args, **kwargs):
                info = original(caminho, *args, **kwargs)
                if caminho == alvo:
                    return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
                return info
            with self.subTest(alvo=alvo.name), patch.object(Path, "lstat", new=observar):
                self.erro("link_ou_reparse_point", self.obter)

    def test_permissao_de_abertura_negada_e_sanitizada(self):
        api = servico._plataforma()
        with patch.object(servico, "_plataforma", return_value=api), \
                patch.object(api, "abrir", side_effect=PermissionError(errno.EACCES, "caminho privado")):
            erro = self.erro("acesso_negado", self.obter)
        self.assertEqual(erro.causa, "PermissionError")
        self.assertNotIn("privado", str(erro))
        self.assertFalse(self.coordenacao.exists())

    def test_permissao_real_arquivo_somente_leitura_ou_sem_acesso(self):
        self.coordenacao.write_bytes(b"sintetico")
        if os.name == "posix" and os.geteuid() == 0:
            self.skipTest("Root ignora os bits de permissão POSIX.")
        self.coordenacao.chmod(stat.S_IREAD if os.name == "nt" else 0)
        try:
            self.erro("acesso_negado", self.obter)
        finally:
            self.coordenacao.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_erro_da_api_de_lock_nao_produz_concessao_e_fecha_fd(self):
        api, descritores = servico._plataforma(), []
        abrir = api.abrir
        def registrar(caminho):
            fd = abrir(caminho)
            descritores.append(fd)
            return fd
        with patch.object(servico, "_plataforma", return_value=api), \
                patch.object(api, "abrir", side_effect=registrar), \
                patch.object(api, "tentar", side_effect=OSError(errno.EIO, "detalhe privado")):
            erro = self.erro("erro_sistema", self.obter)
        self.assertNotIn("privado", str(erro))
        self.assertEqual(len(descritores), 1)
        with self.assertRaises(OSError):
            os.fstat(descritores[0])
        with self.obter("manutencao"):
            pass

    @unittest.skipUnless(os.name == "nt", "Falhas da API nativa Windows.")
    def test_windows_false_de_lockfileex_com_erro_nao_e_sucesso(self):
        for codigo in (5, 6, 50, 997):
            api = servico._plataforma()
            def recusar(*args):
                api.ctypes.set_last_error(codigo)
                return 0
            with self.subTest(codigo=codigo), patch.object(servico, "_plataforma", return_value=api), \
                    patch.object(api.api, "LockFileEx", side_effect=recusar) as chamada:
                with self.assertRaises(servico.ErroBarreira) as contexto:
                    self.obter(prazo=1)
                self.assertIn(contexto.exception.codigo, {"acesso_negado", "erro_sistema"})
                chamada.assert_called_once()
            with self.obter("manutencao"):
                pass

    def test_falha_de_identidade_apos_lock_fecha_recurso(self):
        conferir = servico._conferir
        chamadas = []
        def alterar(*args):
            chamadas.append(True)
            if len(chamadas) == 2:
                raise servico.ErroBarreira("aquisicao", "identidade_coordenacao_nao_confirmada")
            return conferir(*args)
        with patch.object(servico, "_conferir", side_effect=alterar):
            self.erro("identidade_coordenacao_nao_confirmada", self.obter)
        self.assertEqual(len(chamadas), 2)
        with self.obter("manutencao"):
            pass

    def test_registro_transfere_propriedade_sem_reter_ou_vazar_descritores(self):
        registro, conferir = servico._temporarios, servico._conferir
        pasta_fds = Path("/proc/self/fd")
        antes = len(list(pasta_fds.iterdir())) if sys.platform.startswith("linux") else None
        def observar(fd, caminho):
            self.assertIn(fd, registro.descritores)
            self.assertTrue(registro.mutex.acquire(blocking=False), "Mutex cobre o lock nativo.")
            registro.mutex.release()
            conferir(fd, caminho)
        for _ in range(12):
            for tipo in ("operacional", "manutencao"):
                with patch.object(servico, "_conferir", side_effect=observar):
                    with self.obter(tipo) as concessao:
                        fd = concessao._Concessao__fd
                        self.assertNotIn(fd, registro.descritores)
                        os.fstat(fd)
                with self.assertRaises(OSError) as erro:
                    os.fstat(fd)
                self.assertEqual(erro.exception.errno, errno.EBADF)
        self.assertEqual(registro.descritores, set())
        if antes is not None:
            self.assertEqual(len(list(pasta_fds.iterdir())), antes)

    def test_excecoes_antes_da_emissao_fecham_fd_e_removem_registro(self):
        api = servico._plataforma()
        abrir = api.abrir
        for alvo in ("set_inheritable", "_conferir", "Concessao"):
            fds = []
            def observar(caminho):
                fd = abrir(caminho)
                fds.append(fd)
                return fd
            modulo = os if alvo == "set_inheritable" else servico
            with self.subTest(alvo=alvo), patch.object(servico, "_plataforma", return_value=api), \
                    patch.object(api, "abrir", side_effect=observar), \
                    patch.object(modulo, alvo, side_effect=RuntimeError("falha sintetica")):
                with self.assertRaisesRegex(RuntimeError, "falha sintetica"):
                    self.obter("manutencao")
            self.assertEqual(len(fds), 1)
            self.assertNotIn(fds[0], servico._temporarios.descritores)
            with self.assertRaises(OSError):
                os.fstat(fds[0])
            with self.obter("manutencao"):
                pass

    def test_falha_no_registro_fecha_abertura_ainda_protegida(self):
        registro, api = servico._DescritoresTemporarios(), servico._plataforma()
        abrir, fds = api.abrir, []
        class RegistroSemMemoria(set):
            def add(self, fd):
                self.add_protegido = registro.mutex.locked()
                raise MemoryError("falha sintetica")
        registro.descritores = RegistroSemMemoria()
        def observar(caminho):
            fd = abrir(caminho)
            fds.append(fd)
            return fd
        with patch.object(api, "abrir", side_effect=observar):
            with self.assertRaises(MemoryError):
                registro.abrir(api, self.coordenacao)
        self.assertTrue(registro.descritores.add_protegido)
        self.assertEqual(registro.descritores, set())
        with self.assertRaises(OSError):
            os.fstat(fds[0])
        registro.verificar()

    def test_close_temporario_incerto_recusa_novo_io_sem_repetir_fd_reutilizado(self):
        registro, fechar = servico._DescritoresTemporarios(), os.close
        api = servico._plataforma()
        fd = registro.abrir(api, self.coordenacao)
        def falhar(descritor):
            fechar(descritor)
            raise OSError(errno.EIO, "falha apos close sintetico")
        with patch.object(os, "close", side_effect=falhar) as close:
            with self.assertRaises(OSError):
                registro.fechar(fd)
            registro.fechar(fd)
            close.assert_called_once_with(fd)
        self.assertEqual(registro.descritores, set())
        # Reutilizar de fato o mesmo número; o registro antigo não pode fechá-lo.
        novo = os.open(self.banco, os.O_RDONLY)
        try:
            if novo != fd:
                os.dup2(novo, fd)
            try:
                registro.fechar(fd)
                os.fstat(fd)
                with patch.object(servico, "_temporarios", registro), \
                        patch.object(servico, "_plataforma") as plataforma, \
                        patch.object(Path, "lstat") as lstat:
                    self.erro("fechamento_temporario_nao_confirmado", self.obter)
                plataforma.assert_not_called()
                lstat.assert_not_called()
            finally:
                if novo != fd:
                    os.close(fd)
        finally:
            os.close(novo)

    def test_callback_filho_fecha_so_temporarios_recria_mutex_e_recusa_close_incerto(self):
        # Exercita o callback isoladamente também no Windows; o fork real é testado no Linux.
        registro, api = servico._DescritoresTemporarios(), servico._plataforma()
        fds = [registro.abrir(api, self.coordenacao) for _ in range(2)]
        self.addCleanup(lambda: [os.close(fd) for fd in tuple(registro.descritores)])
        self.assertTrue(api.tentar(fds[0], True))
        mutex, fechar = registro.mutex, os.close
        registro.antes_do_fork()
        falhou = []
        def observar(fd):
            fechar(fd)
            if not falhou:
                falhou.append(fd)
                raise OSError(errno.EIO, "falha apos close sintetico")
        with patch.object(os, "close", side_effect=observar) as close, \
                patch.object(api, "liberar") as unlock:
            registro.depois_no_filho()
        self.assertEqual({c.args[0] for c in close.call_args_list}, set(fds))
        unlock.assert_not_called()
        self.assertIsNot(registro.mutex, mutex)
        self.assertFalse(registro.mutex.locked())
        self.assertEqual(registro.descritores, set())
        self.erro("fechamento_temporario_nao_confirmado", registro.verificar)
        for fd in fds:
            with self.assertRaises(OSError):
                os.fstat(fd)
        with self.obter("manutencao"):
            pass

    def test_unlock_falha_fecha_handle_e_informa_estado_de_erro(self):
        concessao = self.obter("manutencao")
        api = concessao._Concessao__api
        with patch.object(api, "liberar", side_effect=OSError(errno.EIO, "detalhe privado")):
            self.erro("liberacao_falhou", concessao.fechar)
        self.assertEqual(concessao.consultar_estado(), servico.EstadoConcessao.ERRO_LIBERACAO)
        self.erro("liberacao_nao_confirmada", concessao.fechar)
        with self.obter("manutencao", prazo=2):
            pass

    def test_close_com_erro_nao_e_repetido_em_fd_potencialmente_reutilizado(self):
        for herdada in (False, True):
            with self.subTest(herdada=herdada):
                concessao = self.obter()
                fd, fechar = concessao._Concessao__fd, os.close
                # Injeção só do ramo de PID; o fork real tem teste Linux separado.
                pid = os.getpid() + 1 if herdada else os.getpid()
                def falhar(descritor):
                    fechar(descritor)
                    raise OSError(errno.EIO, "erro após close sintético")
                with patch.object(os, "getpid", return_value=pid), \
                        patch.object(os, "close", side_effect=falhar) as close:
                    codigo = "fechamento_falhou" if herdada else "liberacao_falhou"
                    self.erro(codigo, concessao.fechar)
                    self.erro("liberacao_nao_confirmada", concessao.fechar)
                    self.assertEqual(concessao.consultar_estado(), servico.EstadoConcessao.ERRO_LIBERACAO)
                    close.assert_called_once_with(fd)
                self.assertEqual(concessao.consultar_estado(), servico.EstadoConcessao.ERRO_LIBERACAO)
                with self.assertRaises(OSError):
                    os.fstat(fd)

    def test_concessao_artificial_nao_retorna_estado_ativo(self):
        self.erro("concessao_nao_emitida", servico.Concessao)
        artificial = object.__new__(servico.Concessao)
        self.erro("concessao_nao_emitida", artificial.consultar_estado)
        self.erro("concessao_nao_emitida", artificial.fechar)

    def test_concessao_nao_copiavel_ou_serializavel(self):
        with self.obter() as concessao:
            for operacao in (copy.copy, copy.deepcopy, pickle.dumps):
                with self.subTest(operacao=operacao.__name__), self.assertRaises(TypeError):
                    operacao(concessao)
            with self.assertRaises(AttributeError):
                concessao.tipo = servico.TipoConcessao.MANUTENCAO

    def test_liberacao_idempotente_reentrada_recusada_e_fd_nao_herdavel(self):
        concessao = self.obter()
        self.assertFalse(os.get_inheritable(concessao._Concessao__fd))
        concessao.fechar()
        concessao.fechar()
        self.erro("concessao_nao_ativa", concessao.__enter__)

    def test_consulta_local_nao_sonda_outros_processos_ou_filesystem(self):
        with self.obter() as concessao:
            with patch.object(Path, "lstat", side_effect=AssertionError("lstat proibido")), \
                    patch.object(os, "fstat", side_effect=AssertionError("fstat proibido")):
                self.assertEqual(concessao.consultar_estado(), servico.EstadoConcessao.ATIVA)

    def test_gc_fecha_recurso_abandonado_e_emite_resourcewarning(self):
        with warnings.catch_warnings(record=True) as avisos:
            warnings.simplefilter("always", ResourceWarning)
            concessao = self.obter("manutencao")
            del concessao
            gc.collect()
        self.assertTrue(any(issubclass(a.category, ResourceWarning) for a in avisos))
        with self.obter("manutencao", prazo=2):
            pass

    @unittest.skipUnless(sys.platform.startswith("linux"), "Semântica de fork/flock Linux.")
    def test_fork_durante_aquisicao_nao_prolonga_lock_apos_crash_do_pai(self):
        for depois_do_lock in (False, True):
            with self.subTest(depois_do_lock=depois_do_lock):
                self._verificar_fork_durante_aquisicao(depois_do_lock)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Semântica de fork/flock Linux.")
    def test_fork_aguarda_abertura_e_registro_atomicos_antes_de_copiar_fds(self):
        self._verificar_fork_durante_aquisicao(False, durante_abertura=True)

    def _verificar_fork_durante_aquisicao(self, depois_do_lock, *, durante_abertura=False):
        import select
        import signal

        descritores = set()
        def tubo():
            par = os.pipe()
            descritores.update(par)
            return par
        def fechar(fd):
            os.close(fd)
            descritores.discard(fd)
        def receber(fd):
            fim, dados = time.monotonic() + 10, bytearray()
            while True:
                restante = fim - time.monotonic()
                if restante <= 0 or not select.select([fd], [], [], restante)[0]:
                    raise AssertionError("Processo fork não respondeu em 10s.")
                byte = os.read(fd, 1)
                if not byte:
                    raise AssertionError("Processo fork encerrou antes da resposta.")
                if byte == b"\n":
                    return json.loads(dados)
                dados.extend(byte)

        pai, filho_encerrado = None, False
        try:
            comando_r, comando_w = tubo()
            resposta_r, resposta_w = tubo()
            filho_r, filho_w = tubo()
            pai = os.fork()
            if pai == 0:
                try:
                    os.setpgid(0, 0)  # Grupo próprio permite limpar até um callback filho travado.
                    for fd in (comando_w, resposta_r, filho_w):
                        fechar(fd)
                    def emitir(**dados):
                        os.write(resposta_w, (json.dumps(dados) + "\n").encode("utf-8"))
                    aberto, continuar, manter = (threading.Event() for _ in range(3))
                    contexto = {"conferencias": 0}
                    if durante_abertura:
                        devolver_abertura = threading.Event()
                        principal = threading.get_ident()
                        mutex = servico._temporarios.mutex
                        class PortaoObservado:
                            def acquire(self, *args, **kwargs):
                                # before precisa aguardar a publicação no registro, sem sleeps.
                                if threading.get_ident() == principal:
                                    devolver_abertura.set()
                                return mutex.acquire(*args, **kwargs)
                            def release(self):
                                mutex.release()
                            def __enter__(self):
                                self.acquire()
                                return self
                            def __exit__(self, *args):
                                self.release()
                        servico._temporarios.mutex = PortaoObservado()
                        api = servico._plataforma()
                        abrir = api.abrir
                        def pausar_abertura(caminho):
                            fd = abrir(caminho)
                            contexto["fd"] = fd
                            info = os.fstat(fd)
                            contexto["identidade"] = (info.st_dev, info.st_ino)
                            aberto.set()
                            if not devolver_abertura.wait(timeout=10):
                                os.close(fd)
                                raise AssertionError("before não coordenou abertura em andamento.")
                            return fd
                        api.abrir = pausar_abertura
                        servico._plataforma = lambda: api
                    conferir = servico._conferir
                    def pausar(fd, caminho):
                        contexto["conferencias"] += 1
                        if contexto["conferencias"] == (2 if depois_do_lock else 1):
                            contexto["fd"] = fd
                            info = os.fstat(fd)
                            contexto["identidade"] = (info.st_dev, info.st_ino)
                            aberto.set()
                            if not continuar.wait(timeout=10):
                                raise AssertionError("Aquisição não recebeu continuação.")
                        conferir(fd, caminho)
                    servico._conferir = pausar
                    def adquirir():
                        try:
                            with servico.adquirir_manutencao(backend="sqlite", banco=self.banco):
                                emitir(evento="pai_adquiriu")
                                if not manter.wait(timeout=10):
                                    raise AssertionError("Pai não recebeu encerramento.")
                        except BaseException as exc:
                            emitir(evento="erro_worker", tipo=type(exc).__name__)
                    worker = threading.Thread(target=adquirir, daemon=True)
                    worker.start()
                    if not aberto.wait(timeout=10):
                        raise AssertionError("Descritor temporário não foi aberto.")
                    emitir(evento="aberto", herdavel_exec=os.get_inheritable(contexto["fd"]))
                    # Python 3.12 avisa sobre fork com threads; esta é a intercalação investigada.
                    with warnings.catch_warnings(record=True):
                        warnings.simplefilter("always", DeprecationWarning)
                        filho = os.fork()
                    if filho == 0:
                        try:
                            fechar(comando_r)
                            temporario = contexto["fd"]
                            try:
                                info = os.fstat(temporario)
                            except OSError as exc:
                                if exc.errno != errno.EBADF:
                                    raise
                                herdado = False
                            else:
                                herdado = (info.st_dev, info.st_ino) == contexto["identidade"]
                            emitir(evento="filho_pronto", herdado=herdado)
                            while True:
                                comando = receber(filho_r)
                                if comando == "liberar":
                                    if herdado:
                                        os.close(temporario)  # Nunca LOCK_UN na descrição do pai.
                                        herdado = False
                                    emitir(evento="filho_liberou")
                                elif comando == "sair":
                                    emitir(evento="filho_encerrando")
                                    break
                        finally:
                            os._exit(0)
                    emitir(evento="pai_pronto")
                    if receber(comando_r) != "adquirir":
                        raise AssertionError("Comando de aquisição inesperado.")
                    continuar.set()
                    if receber(comando_r) != "sair":
                        raise AssertionError("Comando de encerramento inesperado.")
                    manter.set()
                    worker.join(timeout=10)
                    if worker.is_alive():
                        raise AssertionError("Worker não encerrou.")
                except BaseException as exc:
                    emitir(evento="erro_pai", tipo=type(exc).__name__)
                finally:
                    os._exit(0)

            for fd in (comando_r, resposta_w, filho_r):
                fechar(fd)
            mensagens = [receber(resposta_r) for _ in range(3)]
            self.assertEqual({m["evento"] for m in mensagens}, {"aberto", "filho_pronto", "pai_pronto"})
            self.assertFalse(next(m for m in mensagens if m["evento"] == "aberto")["herdavel_exec"])
            herdado = next(m for m in mensagens if m["evento"] == "filho_pronto")["herdado"]
            os.write(comando_w, b'"adquirir"\n')
            self.assertEqual(receber(resposta_r), {"evento": "pai_adquiriu"})
            self.erro("ocupado", lambda: self.obter("manutencao"))
            os.kill(pai, signal.SIGKILL)
            # Manter o PID reservado até o cleanup evita sinalizar um grupo reutilizado.
            os.waitid(os.P_PID, pai, os.WEXITED | os.WNOWAIT)
            try:
                with self.obter("manutencao"):
                    apos_crash = "adquirida"
            except servico.ErroBarreira as exc:
                apos_crash = exc.codigo
            os.write(filho_w, b'"liberar"\n')
            self.assertEqual(receber(resposta_r), {"evento": "filho_liberou"})
            with self.obter("manutencao", prazo=1):
                pass
            os.write(filho_w, b'"sair"\n')
            self.assertEqual(receber(resposta_r), {"evento": "filho_encerrando"})
            self.assertTrue(select.select([resposta_r], [], [], 10)[0], "Filho não encerrou.")
            self.assertEqual(os.read(resposta_r, 1), b"", "Pipe do filho permaneceu aberto.")
            filho_encerrado = True
            self.assertFalse(herdado,
                             f"fd_temporario_herdado={herdado}; apos_SIGKILL={apos_crash}; "
                             "apos_fecho_filho=adquirida")
            self.assertEqual(apos_crash, "adquirida", "Filho prolongou lock após SIGKILL do pai.")
        finally:
            if pai not in (None, 0):
                if not filho_encerrado:
                    try:
                        os.killpg(pai, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                try:
                    os.kill(pai, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.waitpid(pai, 0)
            for fd in tuple(descritores):
                fechar(fd)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Semântica de fork/flock Linux.")
    def test_fork_filho_nao_desbloqueia_concessao_do_pai(self):
        import select
        concessao = self.obter("manutencao")
        self.addCleanup(concessao.fechar)
        leitura, escrita = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(leitura)
                herdada = concessao.consultar_estado() == servico.EstadoConcessao.HERDADA
                concessao.fechar()
                liberada = concessao.consultar_estado() == servico.EstadoConcessao.LIBERADA
                os.write(escrita, b"ok" if herdada and liberada else b"erro")
            finally:
                os._exit(0)
        os.close(escrita)
        pronto = []
        try:
            pronto, _, _ = select.select([leitura], [], [], 10)
            self.assertTrue(pronto, "Filho não respondeu")
            self.assertEqual(os.read(leitura, 10), b"ok")
        finally:
            os.close(leitura)
            if not pronto:
                import signal
                os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        self.erro("ocupado", self.obter)
        concessao.fechar()
        with self.obter("manutencao"):
            pass

    @unittest.skipUnless(os.name == "nt", "Compartilhamento sem DELETE do Windows.")
    def test_windows_impede_remover_ou_renomear_coordenacao_ativa(self):
        with self.obter():
            with self.assertRaises(PermissionError):
                self.coordenacao.unlink()
            with self.assertRaises(PermissionError):
                self.coordenacao.rename(self.raiz / "outro.lock")
        self.assertTrue(self.coordenacao.exists())

    def test_importacao_isolada_sem_sqlite_database_qt_ou_rede(self):
        programa = r'''
import builtins,sys
original=builtins.__import__
proibidos=('sqlite3','PyQt6','src.core.database','core.database','src.core.licenca','dotenv','socket','requests')
def importar(nome,*args,**kwargs):
    if any(nome==p or nome.startswith(p+'.') for p in proibidos):
        raise AssertionError('importacao operacional proibida')
    return original(nome,*args,**kwargs)
builtins.__import__=importar
from src.core import barreira_sqlite
assert not any(p in sys.modules for p in proibidos)
'''
        resultado = subprocess.run([sys.executable, "-B", "-W", "error::ResourceWarning", "-c", programa],
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)


if __name__ == "__main__":
    unittest.main()
