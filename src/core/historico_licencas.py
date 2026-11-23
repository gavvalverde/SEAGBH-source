"""Persistência SQLite privada do Gerenciamento de Clientes."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from ._pasta_historico import PastaHistorico, caminho_privado


_BANCO = "historico.sqlite3"
_COPIA = "originais.sqlite3"
_APP_HISTORICO = 0x53454148
_APP_COPIA = 0x53454142
_VERSAO = 1
_CAMPOS = ("id", "cliente", "plano", "emissao", "expira", "chave")
_DDL_HISTORICO = (
    "CREATE TABLE registros (ordem INTEGER PRIMARY KEY AUTOINCREMENT, "
    "id TEXT NOT NULL UNIQUE, excluido INTEGER NOT NULL CHECK(excluido IN (0, 1)), "
    "registro TEXT NOT NULL)",
    "CREATE TABLE importacao (unica INTEGER PRIMARY KEY CHECK(unica = 1), "
    "ativos TEXT NOT NULL, excluidos TEXT NOT NULL)",
)
_DDL_COPIA = ("CREATE TABLE originais (unica INTEGER PRIMARY KEY CHECK(unica = 1), "
              "ativos BLOB NOT NULL, excluidos BLOB NOT NULL)",)


class ErroHistorico(RuntimeError):
    """Somente códigos internos. Mensagens não carregam dados do histórico."""

    def __init__(self, codigo: str):
        super().__init__(codigo)
        self.codigo = codigo


@dataclass(frozen=True)
class ResultadoImportacao:
    ativos: int
    excluidos: int
    ja_importado: bool


def diretorio_historico_privado() -> Path:
    """Resolve o local previsto sem abrir ou criar arquivos."""
    try:
        if os.name == "nt":
            base = Path(os.environ["LOCALAPPDATA"])
        else:
            base = Path.home() / ".local" / "share"
        return caminho_privado(base / "SEAGBH" / "GerenciamentoClientes")
    except (KeyError, OSError, RuntimeError, ValueError):
        raise ErroHistorico("caminho_privado_indisponivel") from None


def _objeto(pares):
    resultado = dict(pares)
    if len(resultado) != len(pares):
        raise ValueError
    return resultado


def _decimal_exato(texto):
    valor = float(texto)
    if not math.isfinite(valor) or Decimal(str(valor)) != Decimal(texto):
        raise ValueError
    return valor


def _constante_invalida(_texto):
    raise ValueError


def _json(texto):
    return json.loads(texto, object_pairs_hook=_objeto, parse_float=_decimal_exato,
                      parse_constant=_constante_invalida)


def _serializar(registro: dict) -> str:
    if (not isinstance(registro, dict)
            or any(not isinstance(registro.get(campo), str) for campo in _CAMPOS)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", registro["id"])):
        raise ErroHistorico("registro_invalido")
    if registro.get("status", "ativa") not in ("ativa", "revogada"):
        raise ErroHistorico("estado_contraditorio")
    try:
        for campo in ("emissao", "expira"):
            if date.fromisoformat(registro[campo]).isoformat() != registro[campo]:
                raise ValueError
        texto = json.dumps(registro, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        texto.encode("utf-8")
        if _json(texto) != registro:
            raise ValueError
        return texto
    except (TypeError, ValueError, RecursionError, ArithmeticError):
        raise ErroHistorico("registro_invalido") from None


def _validar_origens(ativos: bytes, excluidos: bytes):
    grupos = []
    ids = set()
    for origem in (ativos, excluidos):
        if not isinstance(origem, bytes):
            raise ErroHistorico("origem_invalida")
        try:
            registros = _json(origem.decode("utf-8-sig"))
        except (ValueError, RecursionError, ArithmeticError):
            raise ErroHistorico("origem_invalida") from None
        if not isinstance(registros, list):
            raise ErroHistorico("origem_invalida")
        for registro in registros:
            _serializar(registro)
            lid = registro["id"]
            if lid in ids:
                raise ErroHistorico("id_duplicado")
            ids.add(lid)
        grupos.append(registros)
    return grupos


def _iniciar_esquema(conexao, caminho, aplicacao, ddl, *, criar):
    identificador = conexao.execute("PRAGMA application_id").fetchone()[0]
    versao = conexao.execute("PRAGMA user_version").fetchone()[0]
    if (identificador, versao) == (aplicacao, _VERSAO):
        return
    if (identificador or versao or not criar or caminho.stat().st_size
            or conexao.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone()):
        raise ErroHistorico("esquema_incompativel")
    # Os valores de PRAGMA e comandos DDL vêm apenas das constantes do módulo.
    for comando in ddl:
        conexao.execute(comando)
    conexao.execute(f"PRAGMA application_id={aplicacao}")
    conexao.execute(f"PRAGMA user_version={_VERSAO}")


def _integridade(conexao):
    resultado = conexao.execute("PRAGMA integrity_check").fetchall()
    if resultado != [("ok",)]:
        raise ErroHistorico("armazenamento_corrompido")


def _ler(conexao, excluido):
    registros = []
    for lid, texto in conexao.execute(
        "SELECT id, registro FROM registros WHERE excluido = ? ORDER BY ordem", (excluido,)
    ):
        try:
            registro = _json(texto)
            _serializar(registro)
            if registro["id"] != lid:
                raise ValueError
        except (ErroHistorico, ValueError, TypeError, RecursionError, ArithmeticError):
            raise ErroHistorico("armazenamento_corrompido") from None
        registros.append(registro)
    return registros


class HistoricoLicencas:
    """Cada operação abre sua conexão. Só a importação explícita cria o banco."""

    def __init__(self, pasta: Path):
        try:
            self._pasta = caminho_privado(pasta)
        except (TypeError, ValueError):
            raise ErroHistorico("caminho_inseguro") from None

    @contextmanager
    def _transacao(self, nome, aplicacao, ddl, *, gravar, criar=False):
        try:
            pasta = PastaHistorico(self._pasta, criar=criar)
            for arquivo in (_BANCO, _COPIA):
                pasta.arquivo(arquivo)
                pasta.arquivo(arquivo + "-journal")  # O SQLite pode recuperar um journal quente.
                for sufixo in ("-wal", "-shm"):
                    if pasta.arquivo(arquivo + sufixo) is not None:
                        raise ErroHistorico("modo_sqlite_incompativel")
            caminho = self._pasta / nome
            if pasta.arquivo(nome) is None and criar:
                if nome == _BANCO and pasta.arquivo(_COPIA) is not None:
                    # A cópia só é criada depois do banco. Sua presença isolada
                    # pode indicar perda de registros posteriores à importação.
                    if pasta.arquivo(_BANCO) is None:
                        raise ErroHistorico("principal_ausente_com_copia")
                else:
                    try:
                        fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    except FileExistsError:
                        pass  # Outro inicializador será serializado por BEGIN IMMEDIATE.
                    else:
                        os.close(fd)
            if pasta.arquivo(nome) is None:
                raise ErroHistorico("armazenamento_ausente")
            uri = caminho.as_uri() + "?mode=rw"
            tempo_espera = 5 if gravar else 0
            with closing(sqlite3.connect(uri, uri=True, isolation_level=None, timeout=tempo_espera)) as conexao, conexao:
                if conexao.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                    raise ErroHistorico("modo_sqlite_incompativel")
                conexao.execute("PRAGMA synchronous=EXTRA")
                conexao.execute("PRAGMA temp_store=MEMORY")
                conexao.execute("PRAGMA trusted_schema=OFF")
                conexao.execute("BEGIN IMMEDIATE" if gravar else "BEGIN")
                _iniciar_esquema(conexao, caminho, aplicacao, ddl, criar=criar)
                yield conexao
        except FileNotFoundError:
            raise ErroHistorico("armazenamento_ausente") from None
        except PermissionError:
            raise ErroHistorico("permissoes_inseguras") from None
        except sqlite3.Error as erro:
            codigo_sqlite = getattr(erro, "sqlite_errorcode", 0) & 0xff
            if codigo_sqlite in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                codigo = "armazenamento_ocupado"
            elif codigo_sqlite in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                codigo = "armazenamento_corrompido"
            else:
                codigo = "falha_armazenamento"
            raise ErroHistorico(codigo) from None
        except OSError:
            raise ErroHistorico("falha_armazenamento") from None

    @contextmanager
    def _historico(self, *, gravar=False):
        with self._transacao(_BANCO, _APP_HISTORICO, _DDL_HISTORICO, gravar=gravar) as conexao:
            if not conexao.execute("SELECT 1 FROM importacao WHERE unica = 1").fetchone():
                raise ErroHistorico("migracao_pendente")
            yield conexao

    def ler_ativos(self) -> list[dict]:
        with self._historico() as conexao:
            return _ler(conexao, False)

    def ler_excluidos(self) -> list[dict]:
        with self._historico() as conexao:
            return _ler(conexao, True)

    def adicionar(self, registro: dict) -> None:
        texto = _serializar(registro)
        with self._historico(gravar=True) as conexao:
            if conexao.execute(
                "INSERT INTO registros (id, excluido, registro) VALUES (?, 0, ?) "
                "ON CONFLICT(id) DO NOTHING", (registro["id"], texto),
            ).rowcount != 1:
                raise ErroHistorico("id_duplicado")

    def atualizar(self, anterior: dict, **mudancas) -> None:
        """Falha diante de alteração concorrente do registro exibido."""
        novo = dict(anterior, **mudancas)
        texto = _serializar(novo)
        with self._historico(gravar=True) as conexao:
            if novo["id"] != anterior["id"] or conexao.execute(
                "UPDATE registros SET registro = ? WHERE id = ? AND excluido = 0 AND registro = ?",
                (texto, anterior["id"], _serializar(anterior)),
            ).rowcount != 1:
                raise ErroHistorico("registro_alterado")

    def mover(self, registro: dict, *, para_excluidos: bool) -> None:
        """Mantém o registro e o coloca no fim da lista de destino."""
        texto = _serializar(registro)
        with self._historico(gravar=True) as conexao:
            if conexao.execute(
                "DELETE FROM registros WHERE id = ? AND excluido = ? AND registro = ?",
                (registro["id"], not para_excluidos, texto),
            ).rowcount != 1:
                raise ErroHistorico("registro_alterado")
            conexao.execute("INSERT INTO registros (id, excluido, registro) VALUES (?, ?, ?)",
                            (registro["id"], para_excluidos, texto))

    def migrar_fixtures(self, *, ativos: bytes, excluidos: bytes) -> ResultadoImportacao:
        """Homologa a migração com bytes sintéticos. A rotina não resolve origens."""
        registros = _validar_origens(ativos, excluidos)
        originais = (ativos, excluidos)
        hashes = tuple(hashlib.sha256(origem).hexdigest() for origem in originais)
        with self._transacao(_BANCO, _APP_HISTORICO, _DDL_HISTORICO, gravar=True, criar=True) as conexao:
            anterior = conexao.execute("SELECT ativos, excluidos FROM importacao WHERE unica = 1").fetchone()
            if anterior is not None and anterior != hashes:
                raise ErroHistorico("destino_em_uso")
            if anterior is None and conexao.execute("SELECT 1 FROM registros LIMIT 1").fetchone():
                raise ErroHistorico("destino_em_uso")
            self._guardar_originais(originais)
            if anterior is None:
                for grupo, itens in enumerate(registros):
                    conexao.executemany(
                        "INSERT INTO registros (id, excluido, registro) VALUES (?, ?, ?)",
                        ((registro["id"], grupo, _serializar(registro)) for registro in itens),
                    )
                if [_ler(conexao, grupo) for grupo in (0, 1)] != registros:
                    raise ErroHistorico("importacao_divergente")
                conexao.execute("INSERT INTO importacao VALUES (1, ?, ?)", hashes)
            _integridade(conexao)
        return ResultadoImportacao(len(registros[0]), len(registros[1]), anterior is not None)

    def retomar_fixtures_da_copia(self) -> ResultadoImportacao:
        """Retoma uma importação sintética interrompida a partir da cópia privada."""
        with self._transacao(_COPIA, _APP_COPIA, _DDL_COPIA, gravar=False) as copia:
            _integridade(copia)
            originais = copia.execute("SELECT ativos, excluidos FROM originais WHERE unica = 1").fetchone()
            if originais is None:
                raise ErroHistorico("copia_original_ausente")
        return self.migrar_fixtures(ativos=originais[0], excluidos=originais[1])

    def _guardar_originais(self, originais):
        # Os escritores adquirem primeiro o banco principal. A cópia é confirmada
        # antes dele; uma interrupção pode deixar só a cópia, segura para repetir.
        with self._transacao(_COPIA, _APP_COPIA, _DDL_COPIA, gravar=True, criar=True) as copia:
            anterior = copia.execute("SELECT ativos, excluidos FROM originais WHERE unica = 1").fetchone()
            if anterior is None:
                copia.execute("INSERT INTO originais VALUES (1, ?, ?)", originais)
                anterior = copia.execute("SELECT ativos, excluidos FROM originais WHERE unica = 1").fetchone()
            if anterior != originais:
                raise ErroHistorico("copia_original_divergente")
            _integridade(copia)
