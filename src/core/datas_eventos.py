"""Interpretação compartilhada dos períodos de evento, sem acesso a persistência."""
from dataclasses import dataclass
from datetime import datetime, time
import re


_FORMATO = re.compile(
    r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})"
    r"(?:[ \t]+([0-9]{1,2}):([0-9]{1,2})(?::([0-9]{1,2}))?)?"
)
ORIENTACAO_DATA_EVENTO = "DD/MM/AAAA, com horário opcional HH:MM ou HH:MM:SS"


class ErroPeriodoEvento(ValueError):
    """Data impossível, formato não suportado ou período invertido."""


@dataclass(frozen=True)
class PeriodoEvento:
    inicio: datetime
    fim: datetime
    fim_com_hora: bool

    def vencido_em(self, agora: datetime) -> bool:
        if self.fim_com_hora:
            return agora > self.fim
        return agora.date() > self.fim.date()


def _interpretar(texto: str, campo: str, *, fim: bool) -> tuple[datetime, bool]:
    partes = _FORMATO.fullmatch(texto.strip()) if isinstance(texto, str) else None
    if partes is not None:
        dia, mes, ano, hora, minuto, segundo = partes.groups()
        try:
            data = datetime(int(ano), int(mes), int(dia))
            if hora is not None:
                return data.replace(hour=int(hora), minute=int(minuto), second=int(segundo or 0)), True
            return (datetime.combine(data.date(), time.max) if fim else data), False
        except ValueError:
            pass
    raise ErroPeriodoEvento(f"Data de {campo} inválida. Use {ORIENTACAO_DATA_EVENTO}.")


def validar_periodo_evento(inicio: str, fim: str) -> PeriodoEvento:
    inicio_dt, _ = _interpretar(inicio, "início", fim=False)
    fim_dt, fim_com_hora = _interpretar(fim, "término", fim=True)
    if inicio_dt > fim_dt:
        raise ErroPeriodoEvento("A data de início não pode ser posterior à data de término.")
    return PeriodoEvento(inicio_dt, fim_dt, fim_com_hora)


def problema_periodo_evento(inicio: str, fim: str) -> str | None:
    """Diagnóstico somente de leitura para períodos legados."""
    try:
        validar_periodo_evento(inicio, fim)
    except ErroPeriodoEvento as erro:
        return str(erro)
    return None
