"""Restauração de um arquivo SQLite sintético em um ambiente temporário dedicado."""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from uuid import uuid4

from .backup_sqlite import _abrir_leitura, _sincronizar_pasta
from .barreira_sqlite import ErroBarreira, adquirir_manutencao, caminho_coordenacao
from .restauracao_sqlite import (
    Compatibilidade,
    ErroRestauracao,
    _COLUNAS_APLICACAO,
    _Prazo,
    _arquivo,
    _caminho,
    _hash,
    _info,
    _sem_auxiliares,
    inspecionar_destino_restauracao,
    validar_candidato_restauracao,
)


# A aplicação não grava esta marca. A preparação dos bancos sintéticos é do teste.
APPLICATION_ID_TESTE = 0x53475435
_PREFIXO = "seagbh-restauracao-teste-"
_MARCA = ".ambiente-restauracao-teste"
# sqlite_schema do DDL SQLite atual, incluindo autoíndices e sqlite_sequence.
_ESQUEMA = "d624b60b9861393c6d1710e2ea258252dc50f5fd9250c1779ab4d7652a4980e7"
_DATAS = {"data_cadastro", "data_inicio", "data_fim", "data_movimentacao", "data_saida", "data"}
_FORMATOS_DATA = ("%d/%m/%Y", "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S")
_ESTADOS = {
    ("eventos", "status"): {"Agendado", "Concluído"},
    ("movimentacoes", "tipo"): {"saida", "retorno"},
    ("manutencao", "status"): {"Em manutenção", "Concluído"},
}


class EstadoRestauracao(str, Enum):
    SUCESSO = "sucesso"
    RECUSADA = "recusada"
    FALHA_RECUPERAVEL = "falha_recuperavel"
    INTERVENCAO_MANUAL = "intervencao_manual"


@dataclass(frozen=True, repr=False)
class AmbienteRestauracaoTeste:
    raiz: Path
    identificador: str


@dataclass(frozen=True)
class ResultadoRestauracao:
    estado: EstadoRestauracao
    fase: str
    codigo: str
    resgate_validado: bool = False


@dataclass(frozen=True)
class _Arquivos:
    destino: Path
    resgate: Path
    substituto: Path
    diario: Path
    diario_preparo: Path


def criar_ambiente_restauracao_teste() -> AmbienteRestauracaoTeste:
    """Cria um diretório novo. O chamador decide quando descartar as evidências."""
    pasta = Path(tempfile.gettempdir()).resolve(strict=True)
    raiz = Path(tempfile.mkdtemp(prefix=_PREFIXO, dir=pasta))
    identificador = uuid4().hex
    with (raiz / _MARCA).open("xb") as marca:
        marca.write(identificador.encode("ascii"))
        marca.flush()
        os.fsync(marca.fileno())
    return AmbienteRestauracaoTeste(raiz, identificador)


def _autorizar(ambiente: AmbienteRestauracaoTeste) -> Path:
    if (type(ambiente) is not AmbienteRestauracaoTeste
            or not isinstance(ambiente.raiz, Path)
            or not isinstance(ambiente.identificador, str)
            or re.fullmatch(r"[0-9a-f]{32}", ambiente.identificador) is None):
        raise ErroRestauracao("ambiente", "ambiente_de_teste_invalido")
    raiz = ambiente.raiz
    if (raiz.parent != Path(tempfile.gettempdir()).resolve(strict=True)
            or not raiz.name.startswith(_PREFIXO)):
        raise ErroRestauracao("ambiente", "ambiente_de_teste_invalido")
    marca = _caminho(raiz / _MARCA)
    _arquivo(marca)
    with marca.open("rb") as entrada:
        if entrada.read(33) != ambiente.identificador.encode("ascii"):
            raise ErroRestauracao("ambiente", "ambiente_de_teste_invalido")
    return raiz


def _banco_teste(raiz: Path, valor: str | Path) -> Path:
    caminho = Path(valor)
    if (not caminho.is_relative_to(raiz) or ".." in caminho.parts
            or caminho.suffix.lower() != ".db"):
        raise ErroRestauracao("caminhos", "banco_fora_do_ambiente_de_teste")
    # Reutiliza a normalização da barreira, incluindo volumes e nomes no Windows.
    caminho_coordenacao(backend="sqlite", banco=caminho)
    caminho = _caminho(caminho.resolve(strict=False))
    if not caminho.is_relative_to(raiz) or caminho.parent.stat().st_dev != raiz.stat().st_dev:
        raise ErroRestauracao("caminhos", "banco_fora_do_ambiente_de_teste")
    return caminho


def _arquivos(raiz: Path, destino: str | Path, resgate: str | Path) -> _Arquivos:
    alvo, copia = (_banco_teste(raiz, p) for p in (destino, resgate))
    if alvo.is_relative_to(copia.parent):
        raise ErroRestauracao("caminhos", "resgate_sobreposto_ao_destino")
    diario = alvo.with_name(alvo.name + ".seagbh-restauracao-teste.json")
    return _Arquivos(alvo, copia, alvo.with_name(alvo.name + ".seagbh-substituto-teste"),
                     diario, diario.with_suffix(".preparo"))


def _data_valida(valor: str) -> bool:
    for formato in _FORMATOS_DATA:
        try:
            datetime.strptime(valor, formato)
            return True
        except ValueError:
            continue
    return False


def _conferir_dados(conn: sqlite3.Connection, controle: _Prazo) -> None:
    for tabela in _COLUNAS_APLICACAO:
        # Os nomes e tipos do sqlite_schema já foram comparados integralmente.
        campos = conn.execute(f'PRAGMA table_info("{tabela}")').fetchall()
        with closing(conn.execute(f'SELECT * FROM "{tabela}"')) as linhas:
            for linha in linhas:
                controle.restante()
                for valor, (_, nome, tipo, obrigatorio, _, pk) in zip(linha, campos):
                    valores = _ESTADOS.get((tabela, nome))
                    if valor is None and not (obrigatorio or pk or valores):
                        continue
                    esperado = int if tipo == "INTEGER" else str
                    if (type(valor) is not esperado
                            or (valores is not None and valor not in valores)
                            or (nome in _DATAS and not _data_valida(valor))):
                        raise ErroRestauracao("compatibilidade", "dados_incompativeis")


def _validar(caminho: Path, controle: _Prazo) -> str:
    candidato = validar_candidato_restauracao(
        backend="sqlite", backup=caminho, prazo=controle.restante()
    )
    if candidato.compatibilidade is Compatibilidade.INCOMPATIVEL:
        raise ErroRestauracao("compatibilidade", "colunas_da_aplicacao_ausentes")
    with closing(_abrir_leitura(caminho)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
        if conn.execute("PRAGMA application_id").fetchone() != (APPLICATION_ID_TESTE,):
            raise ErroRestauracao("ambiente", "banco_sem_marca_sintetica")
        esquema = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
        ).fetchall()
        serializado = json.dumps(esquema, ensure_ascii=True, separators=(",", ":")).encode()
        if (hashlib.sha256(serializado).hexdigest() != _ESQUEMA
                or conn.execute("PRAGMA user_version").fetchone() != (0,)):
            raise ErroRestauracao("compatibilidade", "esquema_fora_do_contrato_teste")
        _conferir_dados(conn, controle)
    if (_arquivo(caminho) != candidato.assinatura
            or not _igual(caminho, candidato.validacao.sha256, controle)):
        raise ErroRestauracao("validacao", "banco_alterado")
    return candidato.validacao.sha256


def _igual(caminho: Path, esperado: str, controle: _Prazo) -> bool:
    antes = _arquivo(caminho)
    _sem_auxiliares(caminho)
    digest = _hash(caminho, antes, controle)
    _sem_auxiliares(caminho)
    return digest == esperado and _arquivo(caminho) == antes


def _copiar(origem: Path, novo: Path, esperado: str, controle: _Prazo) -> None:
    antes = _arquivo(origem)
    _sem_auxiliares(origem)
    _sem_auxiliares(novo)
    with origem.open("rb") as entrada:
        aberta = os.fstat(entrada.fileno())
        if (aberta.st_dev, aberta.st_ino, aberta.st_nlink) != (antes.dispositivo, antes.inode, 1):
            raise ErroRestauracao("copia", "origem_alterada")
        with novo.open("xb") as saida:
            while bloco := entrada.read(1024 * 1024):
                controle.restante()
                saida.write(bloco)
            saida.flush()
            os.fsync(saida.fileno())
    if _validar(novo, controle) != esperado:
        raise ErroRestauracao("copia", "copia_diverge_do_original")
    _sincronizar_pasta(novo.parent)


def _publicar(origem: Path, destino: Path) -> None:
    if os.name != "nt":
        os.replace(origem, destino)
        return
    import ctypes
    from ctypes import wintypes

    mover = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    mover.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
    mover.restype = wintypes.BOOL
    # REPLACE_EXISTING | WRITE_THROUGH, sem COPY_ALLOWED.
    if not mover(str(origem), str(destino), 0x1 | 0x8):
        raise ctypes.WinError(ctypes.get_last_error())


def _gravar_diario(arqs: _Arquivos, raiz: Path, antigo: str, novo: str) -> None:
    with arqs.diario_preparo.open("xb") as arquivo:
        registro = {"v": 1, "antigo": antigo, "novo": novo,
                    "resgate": arqs.resgate.relative_to(raiz).as_posix()}
        arquivo.write(json.dumps(registro).encode())
        arquivo.flush()
        os.fsync(arquivo.fileno())
    _publicar(arqs.diario_preparo, arqs.diario)
    _sincronizar_pasta(arqs.diario.parent)


def _ler_diario(arqs: _Arquivos, raiz: Path) -> tuple[str, str]:
    _arquivo(arqs.diario)
    with arqs.diario.open("rb") as arquivo:
        dados = arquivo.read(4097)
    if len(dados) > 4096:
        raise ErroRestauracao("recuperacao", "diario_invalido")
    registro = json.loads(dados)
    if (not isinstance(registro, dict) or set(registro) != {"v", "resgate", "antigo", "novo"}
            or type(registro["v"]) is not int or registro["v"] != 1
            or registro["resgate"] != arqs.resgate.relative_to(raiz).as_posix()
            or not all(isinstance(registro[k], str) and re.fullmatch(r"[0-9a-f]{64}", registro[k])
                       for k in ("antigo", "novo"))):
        raise ErroRestauracao("recuperacao", "diario_invalido")
    return registro["antigo"], registro["novo"]


def _finalizar(arqs: _Arquivos) -> None:
    # Persiste o estado do destino antes de liberar o diário.
    _sincronizar_pasta(arqs.destino.parent)
    arqs.diario.unlink()
    _sincronizar_pasta(arqs.destino.parent)


_FALHAS = (ErroRestauracao, OSError, sqlite3.Error, ValueError, TypeError, OverflowError,
           RecursionError)


def _codigo(erro):
    return erro.codigo if isinstance(erro, (ErroRestauracao, ErroBarreira)) else type(erro).__name__


def _executar(arqs: _Arquivos, raiz: Path, origem: Path, controle: _Prazo) -> ResultadoRestauracao:
    fase = "validacao"
    anterior = None
    resgate_ok = False
    preparo_iniciado = False
    try:
        if any(_info(p) is not None for p in (arqs.diario, arqs.diario_preparo, arqs.substituto)):
            return ResultadoRestauracao(EstadoRestauracao.INTERVENCAO_MANUAL, fase,
                                         "operacao_anterior_pendente")
        inspecao = inspecionar_destino_restauracao(backend="sqlite", backup=origem,
            destino=arqs.destino, pasta_backups=origem.parent, pasta_resgate=arqs.resgate.parent)
        if inspecao.impedimentos:
            return ResultadoRestauracao(EstadoRestauracao.RECUSADA, fase, inspecao.impedimentos[0])
        if _info(arqs.resgate) is not None:
            return ResultadoRestauracao(EstadoRestauracao.RECUSADA, fase, "resgate_ja_existe")

        novo = _validar(origem, controle)
        anterior = _validar(arqs.destino, controle)
        fase = "resgate"
        _copiar(arqs.destino, arqs.resgate, anterior, controle)
        resgate_ok = True
        fase = "preparacao"
        preparo_iniciado = True
        _copiar(origem, arqs.substituto, novo, controle)
        fase = "intencao"
        _gravar_diario(arqs, raiz, anterior, novo)
        if not all(_igual(p, digest, controle) for p, digest in (
            (origem, novo), (arqs.substituto, novo), (arqs.resgate, anterior), (arqs.destino, anterior)
        )):
            raise ErroRestauracao("publicacao", "arquivo_alterado_antes_da_publicacao")
        controle.restante()
        fase = "publicacao"
        _publicar(arqs.substituto, arqs.destino)
        fase = "confirmacao"
        if not _igual(arqs.destino, novo, controle):
            raise ErroRestauracao(fase, "destino_nao_corresponde_ao_substituto")
        _finalizar(arqs)
        return ResultadoRestauracao(EstadoRestauracao.SUCESSO, fase, "substituto_publicado", True)
    except _FALHAS as erro:
        estado = EstadoRestauracao.RECUSADA
        if anterior is not None:
            estado = EstadoRestauracao.INTERVENCAO_MANUAL
            try:
                if fase != "confirmacao" and _igual(arqs.destino, anterior, controle):
                    # Sem intenção publicada, somente estes artefatos foram criados por nós.
                    if preparo_iniciado and _info(arqs.diario) is None:
                        arqs.substituto.unlink(missing_ok=True)
                        arqs.diario_preparo.unlink(missing_ok=True)
                    estado = EstadoRestauracao.FALHA_RECUPERAVEL
            except _FALHAS:
                return ResultadoRestauracao(estado, fase, "preservacao_nao_confirmada", resgate_ok)
        return ResultadoRestauracao(estado, fase, _codigo(erro), resgate_ok)


def restaurar_sqlite_teste(*, ambiente: AmbienteRestauracaoTeste, origem: str | Path,
                           destino: str | Path, resgate: str | Path,
                           prazo: float = 30) -> ResultadoRestauracao:
    """Substitui somente bancos marcados, fechados e sob concessão exclusiva nova."""
    resgate_ok = False
    try:
        controle = _Prazo(prazo)
        raiz = _autorizar(ambiente)
        arqs = _arquivos(raiz, destino, resgate)
        candidato = _banco_teste(raiz, origem)
        with adquirir_manutencao(backend="sqlite", banco=arqs.destino):
            resultado = _executar(arqs, raiz, candidato, controle)
            resgate_ok = resultado.resgate_validado
        return resultado
    except ErroBarreira as erro:
        estado = (EstadoRestauracao.INTERVENCAO_MANUAL if erro.fase == "liberacao"
                  else EstadoRestauracao.RECUSADA)
        return ResultadoRestauracao(estado, "barreira_" + erro.fase, erro.codigo, resgate_ok)
    except _FALHAS as erro:
        return ResultadoRestauracao(EstadoRestauracao.RECUSADA, "argumentos", _codigo(erro))


def recuperar_restauracao_teste(*, ambiente: AmbienteRestauracaoTeste, destino: str | Path,
                                resgate: str | Path, prazo: float = 30) -> ResultadoRestauracao:
    """Reconcilia os hashes sob nova concessão. Nunca repõe bytes no destino."""
    fase = "argumentos"
    resgate_ok = False
    try:
        controle = _Prazo(prazo)
        raiz = _autorizar(ambiente)
        arqs = _arquivos(raiz, destino, resgate)
        with adquirir_manutencao(backend="sqlite", banco=arqs.destino):
            fase = "recuperacao"
            if _info(arqs.diario) is None:
                if any(_info(p) is not None for p in (arqs.diario_preparo, arqs.substituto)):
                    raise ErroRestauracao(fase, "preparo_sem_diario")
                return ResultadoRestauracao(EstadoRestauracao.RECUSADA, fase, "sem_diario")
            if _info(arqs.diario_preparo) is not None:
                raise ErroRestauracao(fase, "diario_conflitante")
            antigo, novo = _ler_diario(arqs, raiz)
            if _validar(arqs.resgate, controle) != antigo:
                raise ErroRestauracao(fase, "resgate_diverge_do_diario")
            resgate_ok = True
            atual = _validar(arqs.destino, controle)
            if atual not in (antigo, novo):
                raise ErroRestauracao(fase, "destino_diverge_do_diario")
            if _info(arqs.substituto) is not None:
                if _validar(arqs.substituto, controle) != novo:
                    raise ErroRestauracao(fase, "substituto_diverge_do_diario")
                arqs.substituto.unlink()
            _finalizar(arqs)
            if atual == novo:
                return ResultadoRestauracao(EstadoRestauracao.SUCESSO, fase, "publicacao_confirmada", True)
            return ResultadoRestauracao(EstadoRestauracao.FALHA_RECUPERAVEL, fase, "original_preservado", True)
    except ErroBarreira as erro:
        estado = (EstadoRestauracao.INTERVENCAO_MANUAL if erro.fase == "liberacao"
                  else EstadoRestauracao.RECUSADA)
        return ResultadoRestauracao(estado, "barreira_" + erro.fase, erro.codigo, resgate_ok)
    except _FALHAS as erro:
        estado = (EstadoRestauracao.INTERVENCAO_MANUAL if fase == "recuperacao"
                  else EstadoRestauracao.RECUSADA)
        return ResultadoRestauracao(estado, fase, _codigo(erro), resgate_ok)
