"""Conferência textual offline de evidências, sem interpretação semântica.

Lê somente os bytes do patch já validado por parse_patch e os compara com
citações do modelo. Uma citação literal NÃO comprova que o bug existe.
"""
from __future__ import annotations

import re

_HUNK = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@.*\Z")
_MIN_QUOTE_CHARS = 12


def new_file_lines(patch: str) -> dict[int, str]:
    """Retorna linhas NEW adicionadas e de contexto, com números reais do hunk.

    O chamador deve validar contagens e coordenadas com parse_patch ANTES.
    """
    if not isinstance(patch, str):
        raise ValueError("patch inválido")
    lines: dict[int, str] = {}
    pos: int | None = None
    for value in patch.split("\n"):
        line = value.removesuffix("\r")
        hunk = _HUNK.fullmatch(line)
        if hunk is not None:
            pos = int(hunk[1])
            continue
        if pos is None or line == r"\ No newline at end of file":
            continue
        if line.startswith("-"):
            continue
        if line.startswith((" ", "+")):
            # Dados do PR podem conter texto não confiável, mas não o executamos.
            lines[pos] = line[1:]
            pos += 1
    return lines


def has_literal_code_anchor(finding: dict, code_lines: dict) -> bool:
    """Confere somente se o texto da linha indicada aparece em evidence.

    False significa 'não confirmado automaticamente', NÃO 'falso positivo'.
    Normaliza espaços para não depender de identação sem relaxar o texto.
    """
    if not isinstance(finding, dict) or not isinstance(code_lines, dict):
        return False
    path = finding.get("path")
    line_number = finding.get("line")
    quote = finding.get("evidence")
    if not isinstance(path, str) or type(line_number) is not int or not isinstance(quote, str):
        return False
    source = code_lines.get(path)
    if not isinstance(source, dict):
        return False
    line = source.get(line_number)
    if not isinstance(line, str):
        return False
    normalized_line = " ".join(line.split())
    if len(normalized_line) < _MIN_QUOTE_CHARS:
        return False
    return normalized_line in " ".join(quote.split())
