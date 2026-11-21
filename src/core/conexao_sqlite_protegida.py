"""Sessão SQLite cooperativa isolada, com operações síncronas na thread dona."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import threading

from .barreira_sqlite import Concessao, adquirir_uso_operacional

__all__ = ["ConexaoSQLiteProtegida", "abrir_conexao_operacional"]

_EMISSAO = object()
_REGISTRO_MUTEX = threading.Lock()
_SESSOES_ABERTAS: set[ConexaoSQLiteProtegida] = set()


def _registrar(sessao: ConexaoSQLiteProtegida) -> None:
    with _REGISTRO_MUTEX:
        _SESSOES_ABERTAS.add(sessao)


def _autorizar(acao, _arg1, _arg2, _banco, _origem):
    # ATTACH e VACUUM podem abrir outro banco fora do namespace da concessão.
    return sqlite3.SQLITE_DENY if acao == sqlite3.SQLITE_ATTACH else sqlite3.SQLITE_OK


@dataclass(frozen=True, slots=True)
class ResultadoSQLite:
    linhas: list[tuple]
    lastrowid: int | None


class ConexaoSQLiteProtegida:
    """Proprietária da concessão, conexão e cursor até completar o fechamento.

    O contexto entrega a sessão. executar() devolve apenas linhas materializadas.
    Commit e rollback são explícitos. O escopo aceita SQL de aplicação confiável.
    """

    def __init__(self, *, _emissao=None):
        if _emissao is not _EMISSAO:
            raise RuntimeError("conexao_protegida_nao_emitida")
        self.__pid = os.getpid()
        self.__thread = threading.current_thread()
        self.__concessao: Concessao | None = None
        self.__conexao: sqlite3.Connection | None = None
        self.__cursor: sqlite3.Cursor | None = None
        self.__ocupada = False
        self.__encerrando = False
        self.__em_contexto = False

    def _abrir(self, banco: str | Path, prazo: float) -> None:
        self.__verificar_dono()
        if self.__encerrando or self.__concessao is not None:
            raise RuntimeError("conexao_protegida_ja_iniciada")
        self.__concessao = adquirir_uso_operacional(backend="sqlite", banco=banco, prazo=prazo)
        self.__conexao = sqlite3.connect(
            self.__concessao.banco, timeout=30.0, cached_statements=0,
            autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL,
        )
        self.__conexao.set_authorizer(_autorizar)

    def __verificar_dono(self) -> None:
        if os.getpid() != self.__pid:
            raise RuntimeError("conexao_protegida_processo_incorreto")
        if threading.current_thread() is not self.__thread:
            raise RuntimeError("conexao_protegida_thread_incorreta")

    def verificar_disponivel(self) -> None:
        """Valida o dono e o estado, inclusive para consumidores de resultados."""
        self.__verificar_dono()
        if self.__ocupada:
            raise RuntimeError("conexao_protegida_operacao_ativa")
        if self.__encerrando or self.__conexao is None:
            raise RuntimeError("conexao_protegida_encerrada")

    @contextmanager
    def __operacao(self):
        self.verificar_disponivel()
        self.__ocupada = True
        try:
            yield self.__conexao
        finally:
            self.__ocupada = False

    def __fechar_cursor(self) -> None:
        if self.__cursor is not None:
            try:
                self.__cursor.close()
            except BaseException:
                self.__encerrando = True
                raise
            self.__cursor = None

    @contextmanager
    def __cursor_controlado(self, conexao):
        self.__cursor = conexao.cursor()
        try:
            yield self.__cursor
        finally:
            self.__fechar_cursor()

    def executar(self, sql: str, parametros=()) -> list[tuple]:
        """Executa uma instrução e encerra o cursor antes de devolver as linhas."""
        return self.executar_resultado(sql, parametros).linhas

    def executar_resultado(self, sql: str, parametros=()) -> ResultadoSQLite:
        """Captura o lastrowid da instrução junto às linhas materializadas."""
        with self.__operacao() as conexao, self.__cursor_controlado(conexao) as cursor:
            cursor.execute(sql, parametros)
            return ResultadoSQLite(cursor.fetchall(), cursor.lastrowid)

    def executar_script_inicializacao(self, script: str) -> None:
        """Executa DDL confiável com o parser nativo, fora de transação ativa."""
        with self.__operacao() as conexao:
            if conexao.in_transaction:
                raise RuntimeError("sqlite_script_com_transacao_ativa")
            with self.__cursor_controlado(conexao) as cursor:
                cursor.executescript(script)

    def commit(self) -> None:
        with self.__operacao() as conexao:
            conexao.commit()

    def rollback(self) -> None:
        with self.__operacao() as conexao:
            conexao.rollback()

    def fechar(self) -> None:
        """Conserva a concessão enquanto algum fechamento SQLite estiver incerto."""
        self.__verificar_dono()
        if self.__ocupada:
            raise RuntimeError("conexao_protegida_operacao_ativa")
        self.__encerrando = True
        self.__ocupada = True
        try:
            self.__fechar_cursor()
            if self.__conexao is not None:
                # A API não entrega recursos brutos. O cursor já foi fechado e
                # o driver não tem cache de statements nem BLOBs/backups ativos.
                self.__conexao.close()
                self.__conexao = None
            if self.__concessao is not None:
                self.__concessao.fechar()
                self.__concessao = None
            with _REGISTRO_MUTEX:
                _SESSOES_ABERTAS.discard(self)
        finally:
            self.__ocupada = False

    def __enter__(self) -> ConexaoSQLiteProtegida:
        self.verificar_disponivel()
        if self.__em_contexto:
            raise RuntimeError("conexao_protegida_contexto_ativo")
        self.__em_contexto = True
        return self

    def __exit__(self, _tipo, _erro, _traceback) -> bool:
        self.fechar()
        return False

    def __reduce_ex__(self, protocolo):
        raise TypeError("conexao_protegida_nao_copiavel")


def abrir_conexao_operacional(*, banco: str | Path,
                             prazo: float = 0) -> ConexaoSQLiteProtegida:
    """Registra o dono antes de adquirir a concessão e abrir o banco."""
    sessao = ConexaoSQLiteProtegida(_emissao=_EMISSAO)
    try:
        _registrar(sessao)
        sessao._abrir(banco, prazo)
        return sessao
    except BaseException as erro_abertura:
        # Uma abertura incompleta segue a mesma ordem de fechamento. Se o
        # cleanup falhar, a referência no registro mantém a proteção e o erro
        # de abertura fica encadeado à falha de fechamento.
        try:
            sessao.fechar()
        except BaseException as erro_fechamento:
            raise erro_fechamento from erro_abertura
        raise
