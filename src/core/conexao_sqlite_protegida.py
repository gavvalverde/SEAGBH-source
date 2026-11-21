"""Piloto isolado: ciclo de vida de uma conexão SQLite com barreira cooperativa.

NÃO altera Database, workers, backup, UI ou política de restauração. Uma
concessão de uso operacional é adquirida antes de sqlite3.connect e mantida
até a confirmação do fechamento da conexão. Não autentica nem impede clientes
SQLite externos que não participam do protocolo.

ATENÇÃO: sqlite3.Connection.close() confirma o fechamento lógico do objeto
Python, não prova quiescência nativa de cursores/BLOBs/backups pendentes
(sqlite3_close_v2 pode adiar a desalocação). Antes de integrar em Database,
é obrigatório fechar esses recursos e testar o ciclo em processos reais.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .barreira_sqlite import Concessao, adquirir_uso_operacional

__all__ = ["ConexaoSQLiteProtegida", "abrir_conexao_operacional"]

_EMISSAO = object()
_REGISTRO_MUTEX = threading.Lock()
_FALHAS_FECHAMENTO: set[ConexaoSQLiteProtegida] = set()
_SESSOES_ABERTAS: set[ConexaoSQLiteProtegida] = set()


class ConexaoSQLiteProtegida:
    """Posse inseparável de conexão e concessão; criada somente pela fábrica.

    A transação pertence ao chamador. O contexto não executa commit implícito:
    fechar uma transação pendente deixa o sqlite3 aplicar rollback. Em caso de
    A sessão fica referenciada até close confirmado, inclusive quando o
    chamador abandona o objeto ou close falha. Isso evita liberar a concessão
    por GC enquanto uma conexão ainda puder existir. A recuperação exige
    fechar explicitamente ou encerrar o processo; não há auto-reparo.
    """

    def __init__(self, conexao: sqlite3.Connection, concessao: Concessao,
                 *, _emissao: object = None):
        if _emissao is not _EMISSAO:
            raise RuntimeError("conexao_protegida_nao_emitida")
        self._conexao: sqlite3.Connection | None = conexao
        self._concessao: Concessao | None = concessao
        self._mutex = threading.RLock()

    @property
    def conexao(self) -> sqlite3.Connection:
        with self._mutex:
            if self._conexao is None:
                raise RuntimeError("conexao_protegida_encerrada")
            return self._conexao

    def fechar(self) -> None:
        """Fecha SQLite primeiro; nunca confirma liberação após close falho."""
        with self._mutex:
            if self._conexao is not None:
                try:
                    self._conexao.close()
                except BaseException:
                    # Se o objeto sumir após uma falha, __del__ da concessão
                    # poderia liberar o lock sem confirmação do close SQLite.
                    with _REGISTRO_MUTEX:
                        _FALHAS_FECHAMENTO.add(self)
                    raise
                self._conexao = None

            if self._concessao is not None:
                # Um erro ao liberar não pode ser reportado como sucesso.
                self._concessao.fechar()
                self._concessao = None

            with _REGISTRO_MUTEX:
                _FALHAS_FECHAMENTO.discard(self)
                _SESSOES_ABERTAS.discard(self)

    def __enter__(self) -> sqlite3.Connection:
        return self.conexao

    def __exit__(self, _tipo, _erro, _traceback) -> bool:
        self.fechar()
        return False


def abrir_conexao_operacional(*, banco: str | Path,
                             prazo: float = 0) -> ConexaoSQLiteProtegida:
    """Adquire lock compartilhado real ANTES de abrir SQLite.

    Usa caminho canônico verificado pela barreira e timeout SQLite de 30 s.
    Somente arquivos locais suportados; jamais usa :memory:, URI arbitrária,
    fallback sem lock ou conexão do Database. Não executa PRAGMA nem DDL.
    """
    concessao = adquirir_uso_operacional(backend="sqlite", banco=banco, prazo=prazo)
    try:
        conexao = sqlite3.connect(concessao.banco, timeout=30.0)
    except BaseException:
        concessao.fechar()
        raise
    sessao = ConexaoSQLiteProtegida(conexao, concessao, _emissao=_EMISSAO)
    # Uma referência forte evita que GC libere a concessão caso o chamador
    # descarte a sessão sem fechar o SQLite que continua em uso.
    with _REGISTRO_MUTEX:
        _SESSOES_ABERTAS.add(sessao)
    return sessao
