"""Contratos e decisões puras para o futuro licenciamento multiativação.

Este módulo não acessa rede, não persiste ativações e não concede acesso.
A reserva real de vagas deverá ocorrer em uma transação no backend confiável.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable
from uuid import UUID


class SituacaoLicenca(str, Enum):
    ATIVA = "ativa"
    SUSPENSA = "suspensa"
    ENCERRADA = "encerrada"


class SituacaoAtivacao(str, Enum):
    ATIVA = "ativa"
    REVOGADA = "revogada"


class DecisaoAtivacao(str, Enum):
    PERMITIDA = "permitida"
    JA_ATIVADO = "ja_ativado"
    LIMITE_ATINGIDO = "limite_atingido"
    LICENCA_INATIVA = "licenca_inativa"


def _validar_uuid(valor: UUID, nome: str) -> None:
    if not isinstance(valor, UUID) or valor.int == 0:
        raise ValueError(f"{nome} deve ser um UUID não nulo")


@dataclass(frozen=True, slots=True)
class Empresa:
    id: UUID
    nome: str

    def __post_init__(self) -> None:
        _validar_uuid(self.id, "empresa.id")
        if not isinstance(self.nome, str) or not self.nome.strip():
            raise ValueError("empresa.nome deve ser preenchido")


@dataclass(frozen=True, slots=True)
class Licenca:
    id: UUID
    empresa_id: UUID
    limite_dispositivos: int
    situacao: SituacaoLicenca

    def __post_init__(self) -> None:
        _validar_uuid(self.id, "licenca.id")
        _validar_uuid(self.empresa_id, "licenca.empresa_id")
        if type(self.limite_dispositivos) is not int or self.limite_dispositivos < 1:
            raise ValueError("limite_dispositivos deve ser um inteiro positivo")
        if not isinstance(self.situacao, SituacaoLicenca):
            raise ValueError("situacao da licença inválida")


@dataclass(frozen=True, slots=True)
class Ativacao:
    id: UUID
    empresa_id: UUID
    licenca_id: UUID
    dispositivo_id: UUID
    situacao: SituacaoAtivacao

    def __post_init__(self) -> None:
        for nome in ("id", "empresa_id", "licenca_id", "dispositivo_id"):
            _validar_uuid(getattr(self, nome), f"ativacao.{nome}")
        if not isinstance(self.situacao, SituacaoAtivacao):
            raise ValueError("situacao da ativação inválida")


@dataclass(frozen=True, slots=True)
class ResultadoAtivacao:
    decisao: DecisaoAtivacao
    vagas_utilizadas: int
    vagas_totais: int

    @property
    def vagas_livres(self) -> int:
        return max(0, self.vagas_totais - self.vagas_utilizadas)


def avaliar_ativacao(
    licenca: Licenca,
    dispositivo_id: UUID,
    ativacoes: Iterable[Ativacao],
) -> ResultadoAtivacao:
    """Avalia a ocupação de uma licença sem escrever nem autenticar.

    A lista deve conter apenas as ativações da licença solicitada, lidas de
    uma visão consistente pelo backend. Vínculos misturados ou registros
    ativos duplicados são rejeitados para evitar decisões permissivas.

    IMPORTANTE: o resultado PERMITIDA não reserva uma vaga. Uma chamada
    concorrente pode observar a mesma capacidade. O backend deve validar
    identidade, licença, limite e inserção em uma única transação.
    """
    if not isinstance(licenca, Licenca):
        raise TypeError("licenca deve ser Licenca")
    _validar_uuid(dispositivo_id, "dispositivo_id")

    ativos: set[UUID] = set()
    ids_ativacao: set[UUID] = set()

    for ativacao in ativacoes:
        if not isinstance(ativacao, Ativacao):
            raise TypeError("ativacoes deve conter apenas Ativacao")
        if (ativacao.empresa_id != licenca.empresa_id
                or ativacao.licenca_id != licenca.id):
            raise ValueError("ativacao não pertence à empresa e licença informadas")
        if ativacao.id in ids_ativacao:
            raise ValueError("ativacao duplicada")
        ids_ativacao.add(ativacao.id)
        if ativacao.situacao == SituacaoAtivacao.ATIVA:
            if ativacao.dispositivo_id in ativos:
                raise ValueError("dispositivo com mais de uma ativacao ativa")
            ativos.add(ativacao.dispositivo_id)

    usadas = len(ativos)
    vagas = licenca.limite_dispositivos

    if licenca.situacao != SituacaoLicenca.ATIVA:
        decisao = DecisaoAtivacao.LICENCA_INATIVA
    elif dispositivo_id in ativos:
        decisao = DecisaoAtivacao.JA_ATIVADO
    elif usadas >= vagas:
        decisao = DecisaoAtivacao.LIMITE_ATINGIDO
    else:
        decisao = DecisaoAtivacao.PERMITIDA

    return ResultadoAtivacao(decisao, usadas, vagas)
