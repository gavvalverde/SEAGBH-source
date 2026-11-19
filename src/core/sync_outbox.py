"""Outbox SQLite para a futura sincronização offline-first do SEAGBH.

Não acessa Supabase ou Firebase, não concede autorização e não altera as
tabelas operacionais existentes. Todas as alterações e seus eventos devem
ser gravados na MESMA transação SQLite pelo código chamador.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import sqlite3
from uuid import UUID, uuid4


_OPERACOES = frozenset({"upsert", "delete"})
_TIPO_REGISTRO = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
_MAX_PAYLOAD_BYTES = 65536


@dataclass(frozen=True, slots=True)
class EventoOutbox:
    sequencia: int
    evento_id: UUID
    empresa_id: UUID
    dispositivo_id: UUID
    tipo_registro: str
    registro_id: UUID
    operacao: str
    dados: dict
    criado_em: str


def _uuid(valor: UUID, campo: str) -> str:
    if not isinstance(valor, UUID) or valor.int == 0:
        raise ValueError(f"{campo} deve ser UUID não nulo")
    return str(valor)


def _tipo(valor: str) -> str:
    if not isinstance(valor, str) or _TIPO_REGISTRO.fullmatch(valor) is None:
        raise ValueError("tipo_registro inválido")
    return valor


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def criar_estrutura_outbox(conexao: sqlite3.Connection) -> None:
    """Instala somente as tabelas de outbox; chamado antes das escritas.

    Não executa commit. Um esquema preexistente incompatível será detectado
    nas operações posteriores; migrações destrutivas não são feitas aqui.
    """
    if not isinstance(conexao, sqlite3.Connection):
        raise TypeError("conexao deve ser sqlite3.Connection")
    if conexao.in_transaction:
        raise RuntimeError("crie a estrutura antes da transação de negócio")
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS seagbh_sync_outbox (
            sequencia INTEGER PRIMARY KEY AUTOINCREMENT,
            evento_id TEXT NOT NULL UNIQUE,
            empresa_id TEXT NOT NULL,
            dispositivo_id TEXT NOT NULL,
            tipo_registro TEXT NOT NULL,
            registro_id TEXT NOT NULL,
            operacao TEXT NOT NULL CHECK(operacao IN ('upsert', 'delete')),
            dados_json TEXT NOT NULL,
            criado_em TEXT NOT NULL,
            confirmado_em TEXT
        )
    """)
    conexao.execute("""
        CREATE INDEX IF NOT EXISTS idx_seagbh_sync_outbox_pendente
        ON seagbh_sync_outbox (empresa_id, confirmado_em, sequencia)
    """)


def registrar_alteracao(
    conexao: sqlite3.Connection,
    *,
    empresa_id: UUID,
    dispositivo_id: UUID,
    tipo_registro: str,
    registro_id: UUID,
    operacao: str,
    dados: dict,
    evento_id: UUID | None = None,
) -> UUID:
    """Registra um evento idempotente na transação de negócio já aberta.

    Não faz commit: se a gravação de negócio falhar, o chamador deve fazer
    rollback da alteração operacional e do evento. Identidades UUID são
    requisitos estruturais, NÃO prova de autenticação do dispositivo/empresa.
    """
    if not isinstance(conexao, sqlite3.Connection):
        raise TypeError("conexao deve ser sqlite3.Connection")
    if not conexao.in_transaction:
        raise RuntimeError("inicie uma transação junto à alteração operacional")
    empresa = _uuid(empresa_id, "empresa_id")
    dispositivo = _uuid(dispositivo_id, "dispositivo_id")
    registro = _uuid(registro_id, "registro_id")
    eid = uuid4() if evento_id is None else evento_id
    eid_texto = _uuid(eid, "evento_id")
    tipo = _tipo(tipo_registro)
    if operacao not in _OPERACOES:
        raise ValueError("operacao deve ser upsert ou delete")
    if not isinstance(dados, dict):
        raise ValueError("dados deve ser um objeto JSON")
    try:
        conteudo = json.dumps(dados, ensure_ascii=False, allow_nan=False,
                              sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError("dados não são serializáveis como JSON") from error
    if len(conteudo.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("dados excedem o limite de 64 KiB")
    conexao.execute("""
        INSERT INTO seagbh_sync_outbox (
            evento_id, empresa_id, dispositivo_id, tipo_registro,
            registro_id, operacao, dados_json, criado_em
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (eid_texto, empresa, dispositivo, tipo, registro,
          operacao, conteudo, _agora()))
    return eid


def listar_pendentes(
    conexao: sqlite3.Connection, *, empresa_id: UUID, limite: int = 100
) -> list[EventoOutbox]:
    """Retorna eventos em ordem local, filtrados pela empresa solicitada.

    NÃO prova vínculo do chamador com a empresa. A autorização será aplicada
    pelo futuro backend de sincronização. Nunca ordene por horário local.
    """
    if not isinstance(conexao, sqlite3.Connection):
        raise TypeError("conexao deve ser sqlite3.Connection")
    empresa = _uuid(empresa_id, "empresa_id")
    if type(limite) is not int or not 1 <= limite <= 1000:
        raise ValueError("limite deve ser inteiro entre 1 e 1000")
    rows = conexao.execute("""
        SELECT sequencia, evento_id, empresa_id, dispositivo_id,
               tipo_registro, registro_id, operacao, dados_json, criado_em
        FROM seagbh_sync_outbox
        WHERE empresa_id = ? AND confirmado_em IS NULL
        ORDER BY sequencia ASC LIMIT ?
    """, (empresa, limite)).fetchall()
    return [
        EventoOutbox(
            sequencia=row[0], evento_id=UUID(row[1]),
            empresa_id=UUID(row[2]), dispositivo_id=UUID(row[3]),
            tipo_registro=row[4], registro_id=UUID(row[5]),
            operacao=row[6], dados=json.loads(row[7]), criado_em=row[8],
        )
        for row in rows
    ]


def confirmar_entrega(
    conexao: sqlite3.Connection, *, empresa_id: UUID, evento_id: UUID
) -> bool:
    """Confirma a entrega apenas APÓS o backend reconhecer o mesmo evento_id.

    O chamador controla o commit. Se a confirmação local falhar, reenviar
    o mesmo evento_id é seguro somente quando o backend for idempotente.
    """
    if not isinstance(conexao, sqlite3.Connection):
        raise TypeError("conexao deve ser sqlite3.Connection")
    if not conexao.in_transaction:
        raise RuntimeError("a confirmação deve ser transacional")
    empresa = _uuid(empresa_id, "empresa_id")
    evento = _uuid(evento_id, "evento_id")
    result = conexao.execute("""
        UPDATE seagbh_sync_outbox SET confirmado_em = ?
        WHERE empresa_id = ? AND evento_id = ? AND confirmado_em IS NULL
    """, (_agora(), empresa, evento))
    return result.rowcount == 1
