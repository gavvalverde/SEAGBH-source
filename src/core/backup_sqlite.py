"""Backups SQLite locais, sem UI, configuração implícita ou acesso remoto.

Contrato: caminhos absolutos locais, sem links/junctions; a pasta de backups
deve existir e ser controlada pelo chamador, sem alterações externas hostis.
O namespace ``seagbh-sqlite-v1-*`` é reservado a este serviço. Arquivos legados,
de resgate e staging não pertencem ao catálogo. Não há restauração neste módulo.

Publicação: hard link do staging finalizado (mesmo filesystem), sem substituir
nomes existentes. rename sobrescreve no POSIX e difere no Windows; replace
sobrescreve em ambos; criar o destino com O_EXCL e depois copiar exporia parcial.
Sem suporte a hard links, a operação falha, sem fallback para cópia/replace.
Um lock exclusivo serializa criação/retenção nesta pasta; após crash, um lock
remanescente exige investigação manual, nunca remoção automática.

O prazo/cancelamento é cooperativo: verificado entre passos SQLite, consultas e
blocos de hash. Não interrompe uma syscall de disco, fsync ou callback bloqueado.
mode=ro não cria a origem nem muda seu journal_mode; WAL exige suporte/permissões
SQLite para seus sidecars e pode falhar em volumes restritos. Não usamos immutable
na origem viva. Não há garantia de durabilidade contra falhas de hardware/energia
ou proteção contra outro processo do mesmo usuário adulterando a pasta.
"""

from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time
from typing import Callable, Iterator
from urllib.parse import quote
from uuid import uuid4


__all__ = [
    "ErroBackup", "ValidacaoBackup", "ResultadoBackup", "ResultadoRetencao",
    "criar_backup", "validar_backup", "aplicar_retencao",
]

_NOME = re.compile(r"seagbh-sqlite-v1-(\d{8}T\d{12}Z)-([0-9a-f]{32})\.db\Z")
_LOCK = ".seagbh-backup.lock"
# Assinatura mínima das tabelas reais; colunas opcionais/novas são permitidas.
_ESQUEMA = {
    "equipamentos": {"id", "codigo_barras", "nome", "data_cadastro"},
    "eventos": {"id", "nome_evento", "responsavel", "data_inicio", "data_fim", "status"},
    "movimentacoes": {"id", "evento_id", "equipamento_id", "data_movimentacao", "tipo"},
    "manutencao": {"id", "equipamento_id", "data_saida", "local_manutencao", "status"},
    "auditoria": {"id", "tabela", "item_id", "acao", "data"},
}


class ErroBackup(Exception):
    """Diagnóstico sem SQL, conteúdo do banco, caminhos ou texto bruto de erros."""

    def __init__(self, fase: str, codigo: str, causa: BaseException | None = None):
        self.fase = fase
        self.codigo = codigo
        self.causa = type(causa).__name__ if causa else None
        self.erro_sistema = getattr(causa, "errno", None)
        self.erro_sqlite = getattr(causa, "sqlite_errorcode", None)
        self.avisos: tuple[str, ...] = ()
        super().__init__(f"Backup não concluído: fase={fase}; código={codigo}.")


@dataclass(frozen=True)
class ValidacaoBackup:
    tamanho: int
    sha256: str
    integridade_fisica: bool = True
    integridade_referencial: bool = True
    esquema_reconhecido: bool = True
    independente: bool = True


@dataclass(frozen=True)
class ResultadoRetencao:
    removidos: tuple[Path, ...]
    validos_preservados: int
    ignorados: int
    avisos: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResultadoBackup:
    caminho: Path
    identificador: str
    criado_em: datetime
    tamanho: int
    sha256: str
    validacao: ValidacaoBackup
    retencao: ResultadoRetencao | None
    avisos: tuple[str, ...] = ()


@dataclass
class _Controle:
    fim: float
    cancelar: Callable[[], bool] | None

    def verificar(self, fase: str) -> None:
        if self.cancelar is not None:
            try:
                cancelado = self.cancelar()
            except Exception as exc:
                raise ErroBackup(fase, "callback_cancelamento_falhou", exc) from None
            if cancelado:
                raise ErroBackup(fase, "cancelado")
        if time.monotonic() >= self.fim:
            raise ErroBackup(fase, "prazo_excedido")


def _controle(backend: str, prazo: float, cancelar: Callable[[], bool] | None) -> _Controle:
    if backend != "sqlite":
        raise ErroBackup("argumentos", "backend_nao_suportado")
    if (isinstance(prazo, bool) or not isinstance(prazo, (int, float))
            or not math.isfinite(prazo) or prazo <= 0
            or (cancelar is not None and not callable(cancelar))):
        raise ErroBackup("argumentos", "controle_invalido")
    return _Controle(time.monotonic() + prazo, cancelar)


def _caminho(valor: str | Path, *, diretorio: bool = False) -> Path:
    try:
        caminho = Path(valor)
        if (not caminho.is_absolute() or ".." in caminho.parts
                or str(caminho).startswith(("\\\\", "//"))):
            raise ErroBackup("argumentos", "caminho_inseguro")
        for parte in (*reversed(caminho.parents), caminho):
            info = parte.lstat()
            if (stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 0x400):
                raise ErroBackup("argumentos", "link_ou_reparse_point")
            if os.name == "nt" and parte != Path(parte.anchor) and ":" in parte.name:
                raise ErroBackup("argumentos", "caminho_inseguro")
        info = caminho.stat()
        if diretorio:
            if not stat.S_ISDIR(info.st_mode):
                raise ErroBackup("argumentos", "diretorio_invalido")
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ErroBackup("argumentos", "arquivo_invalido")
        return caminho.resolve(strict=True)
    except (OSError, ValueError, TypeError) as exc:
        raise ErroBackup("argumentos", "caminho_invalido", exc) from None


def _assinatura(caminho: Path) -> tuple[int, ...]:
    info = caminho.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _abrir_leitura(caminho: Path) -> sqlite3.Connection:
    # Path.as_uri no Python 3.14 importa urllib.request e a pilha de rede.
    uri = "file://" + ("/" if os.name == "nt" else "") + quote(caminho.as_posix(), safe="/:")
    return sqlite3.connect(uri + "?mode=ro", uri=True,
                           timeout=0.1, isolation_level=None)


def _validar(caminho: Path, controle: _Controle) -> ValidacaoBackup:
    controle.verificar("validacao")
    antes = _assinatura(caminho)
    # DELETE journal no cabeçalho e ausência de sidecars: arquivo autossuficiente.
    if any(Path(str(caminho) + sufixo).exists() for sufixo in ("-wal", "-shm", "-journal")):
        raise ErroBackup("validacao", "sidecar_presente")
    with caminho.open("rb") as arquivo:
        cabecalho = arquivo.read(20)
    if (len(cabecalho) != 20 or cabecalho[:16] != b"SQLite format 3\x00"
            or cabecalho[18:20] != b"\x01\x01"):
        raise ErroBackup("validacao", "arquivo_nao_independente")
    interrompido: list[ErroBackup] = []

    def verificar_consulta() -> int:
        try:
            controle.verificar("validacao")
        except ErroBackup as exc:
            interrompido.append(exc)
            return 1
        return 0

    try:
        with closing(_abrir_leitura(caminho)) as conn:
            conn.set_progress_handler(verificar_consulta, 1000)
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA trusted_schema=OFF")
            with closing(conn.execute("PRAGMA integrity_check")) as cursor:
                if cursor.fetchall() != [("ok",)]:
                    raise ErroBackup("validacao", "integridade_fisica")
            with closing(conn.execute("PRAGMA foreign_key_check")) as cursor:
                if cursor.fetchone() is not None:
                    raise ErroBackup("validacao", "integridade_referencial")
            tabelas = {linha[0] for linha in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'")}
            for tabela, colunas in _ESQUEMA.items():
                if tabela not in tabelas:
                    raise ErroBackup("validacao", "esquema_nao_reconhecido")
                # Identificadores vêm apenas da constante acima.
                campos = list(conn.execute(f'PRAGMA table_info("{tabela}")'))
                if (not colunas <= {linha[1] for linha in campos}
                        or not any(linha[1] == "id" and linha[5] == 1 for linha in campos)):
                    raise ErroBackup("validacao", "esquema_nao_reconhecido")
    except sqlite3.Error as exc:
        if interrompido:
            raise interrompido[0] from None
        raise ErroBackup("validacao", "sqlite_invalido", exc) from None
    hash_arquivo = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        while bloco := arquivo.read(1024 * 1024):
            controle.verificar("validacao")
            hash_arquivo.update(bloco)
    if (antes != _assinatura(caminho) or any(
            Path(str(caminho) + sufixo).exists() for sufixo in ("-wal", "-shm", "-journal"))):
        raise ErroBackup("validacao", "arquivo_alterado")
    controle.verificar("validacao")
    return ValidacaoBackup(tamanho=antes[2], sha256=hash_arquivo.hexdigest())


def validar_backup(*, backend: str, caminho: str | Path, prazo: float = 30,
                   cancelar: Callable[[], bool] | None = None) -> ValidacaoBackup:
    """Valida artefato fechado/estável. Não valida banco operacional em uso.

    A assinatura mínima reconhece tabelas/colunas/chaves primárias, sem afirmar
    compatibilidade de todas as regras de negócio ou realizar migração.
    """
    controle = _controle(backend, prazo, cancelar)
    try:
        return _validar(_caminho(caminho), controle)
    except ErroBackup:
        raise
    except Exception as exc:
        raise ErroBackup("validacao", "operacao_falhou", exc) from None


@contextmanager
def _exclusao(pasta: Path, avisos: list[str]) -> Iterator[None]:
    lock = pasta / _LOCK
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ErroBackup("exclusao", "pasta_ocupada") from None
    try:
        info = os.fstat(fd)
    finally:
        os.close(fd)
    try:
        yield
    finally:
        try:
            atual = lock.lstat()
            if (atual.st_dev, atual.st_ino) != (info.st_dev, info.st_ino):
                avisos.append("lock_alterado_nao_removido")
            else:
                lock.unlink()
        except OSError:
            avisos.append("limpeza_lock_falhou")


def _limpar_staging(pasta: Path | None, avisos: list[str]) -> None:
    if pasta is None:
        return
    for nome in ("snapshot.partial", "snapshot.partial-wal", "snapshot.partial-shm",
                 "snapshot.partial-journal"):
        try:
            (pasta / nome).unlink(missing_ok=True)
        except OSError:
            avisos.append("limpeza_staging_falhou")
    try:
        pasta.rmdir()
    except OSError:
        avisos.append("limpeza_staging_falhou")


def _sincronizar_pasta(pasta: Path) -> None:
    # Windows não expõe fsync de diretório portável pela stdlib.
    if os.name == "posix":
        fd = os.open(pasta, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _retencao(pasta: Path, manter: int, protegidos: set[Path],
              controle: _Controle) -> ResultadoRetencao:
    validos: list[tuple[Path, ValidacaoBackup]] = []
    ignorados = 0
    removidos: list[Path] = []
    avisos: list[str] = []
    try:
        for caminho in sorted(pasta.iterdir()):
            nome = _NOME.fullmatch(caminho.name)
            if nome is None:
                ignorados += 1
                continue
            try:
                datetime.strptime(nome[1], "%Y%m%dT%H%M%S%fZ")
                candidato = _caminho(caminho)
                validos.append((candidato, _validar(candidato, controle)))
            except (ErroBackup, ValueError) as exc:
                if isinstance(exc, ErroBackup) and exc.codigo in {"cancelado", "prazo_excedido"}:
                    raise
                ignorados += 1
                avisos.append("candidato_nao_validado_preservado")
        excedentes = max(0, len(validos) - manter)
        for caminho, validacao in validos:
            if len(removidos) >= excedentes:
                break
            if caminho in protegidos:
                continue
            controle.verificar("retencao")
            # Reconfere antes de apagar; pasta não pode ser adulterada externamente.
            atual = _validar(_caminho(caminho), controle)
            if atual.sha256 != validacao.sha256:
                raise ErroBackup("retencao", "candidato_alterado")
            caminho.unlink()
            removidos.append(caminho)
    except (OSError, ErroBackup) as exc:
        codigo = exc.codigo if isinstance(exc, ErroBackup) else "erro_io"
        avisos.append("retencao_incompleta:" + codigo)
    return ResultadoRetencao(tuple(removidos), len(validos) - len(removidos),
                             ignorados, tuple(avisos))


def _validar_retencao(manter: int | None) -> None:
    if manter is not None and (type(manter) is not int or manter < 1):
        raise ErroBackup("argumentos", "retencao_invalida")


def aplicar_retencao(*, backend: str, pasta: str | Path, manter: int = 10,
                     prazo: float = 30) -> ResultadoRetencao:
    """Remove somente excedentes válidos do namespace reservado, sob lock.

    Arquivos inválidos/desconhecidos são preservados. Falhas de limpeza retornam
    avisos, nunca uma afirmação de limpeza completa. manter deve ser >= 1.
    """
    controle = _controle(backend, prazo, None)
    _validar_retencao(manter)
    if manter is None:
        raise ErroBackup("argumentos", "retencao_invalida")
    avisos: list[str] = []
    try:
        with _exclusao(_caminho(pasta, diretorio=True), avisos):
            resultado = _retencao(Path(pasta), manter, set(), controle)
        return replace(resultado, avisos=resultado.avisos + tuple(avisos))
    except OSError as exc:
        raise ErroBackup("retencao", "erro_io", exc) from None


def criar_backup(*, backend: str, origem: str | Path, pasta: str | Path,
                 manter: int | None = 10, prazo: float = 30, paginas: int = 128,
                 cancelar: Callable[[], bool] | None = None,
                 progresso: Callable[[int, int], None] | None = None) -> ResultadoBackup:
    """Publica snapshot validado; manter=None desabilita retenção.

    Não altera dados/journal_mode da origem. Recusa origem em qualquer nível da
    árvore de backups. Cancelamento anterior à publicação não publica nem roda retenção;
    após o hard link, a criação está concluída e falhas posteriores viram avisos.
    O chamador deve apresentar esses avisos (inclusive limpeza/retencão).
    """
    controle = _controle(backend, prazo, cancelar)
    _validar_retencao(manter)
    if type(paginas) is not int or paginas < 1 or (progresso is not None and not callable(progresso)):
        raise ErroBackup("argumentos", "progresso_invalido")
    origem = _caminho(origem)
    pasta = _caminho(pasta, diretorio=True)
    if origem.is_relative_to(pasta):
        raise ErroBackup("argumentos", "origem_na_pasta_de_backups")
    controle.verificar("preparacao")
    avisos: list[str] = []
    staging = None
    erro = None
    resultado = None
    fase = "exclusao"
    try:
        with _exclusao(pasta, avisos):
            try:
                fase = "snapshot"
                staging = Path(tempfile.mkdtemp(prefix=".seagbh-staging-", dir=pasta))
                temporario = staging / "snapshot.partial"
                fd = os.open(temporario, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)

                def avancar(status: int, restantes: int, total: int) -> None:
                    controle.verificar("snapshot")
                    if progresso is not None:
                        progresso(restantes, total)
                    controle.verificar("snapshot")

                with closing(_abrir_leitura(origem)) as fonte:
                    with closing(sqlite3.connect(temporario, timeout=0.1,
                                                isolation_level=None)) as destino:
                        fonte.backup(destino, pages=paginas, progress=avancar, sleep=0.05)
                        if destino.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                            raise ErroBackup("snapshot", "destino_nao_independente")
                fase = "validacao"
                validacao = _validar(temporario, controle)
                fase = "publicacao"
                # Windows _commit/fsync exige handle gravável, mesmo após validação.
                with temporario.open("r+b") as arquivo:
                    os.fsync(arquivo.fileno())
                criado_em = datetime.now(timezone.utc)
                identificador = uuid4().hex
                nome = f"seagbh-sqlite-v1-{criado_em:%Y%m%dT%H%M%S%fZ}-{identificador}.db"
                final = pasta / nome
                controle.verificar("publicacao")
                os.link(temporario, final)  # operação exclusiva; EEXIST nunca sobrescreve
                resultado = ResultadoBackup(final, identificador, criado_em, validacao.tamanho,
                                             validacao.sha256, validacao, None)
            finally:
                _limpar_staging(staging, avisos)
            # Não reportar uma criação já publicada como se nunca tivesse ocorrido.
            try:
                _sincronizar_pasta(pasta)
            except OSError:
                avisos.append("sincronizacao_diretorio_nao_confirmada")
            if manter is not None and not avisos:
                retencao = _retencao(pasta, manter, {resultado.caminho}, controle)
                resultado = replace(resultado, retencao=retencao)
            elif manter is not None:
                avisos.append("retencao_nao_executada")
    except ErroBackup as exc:
        if resultado is None:
            erro = exc
        else:
            avisos.append("pos_publicacao_incompleta:" + exc.codigo)
    except Exception as exc:
        # Fronteira pública também sanitiza exceções de callbacks, sem ocultá-las.
        if resultado is None:
            erro = ErroBackup(fase, "operacao_falhou", exc)
        else:
            avisos.append("pos_publicacao_incompleta:" + type(exc).__name__)
    if erro is not None:
        erro.avisos = tuple(avisos)
        raise erro from None
    return replace(resultado, avisos=tuple(avisos))
