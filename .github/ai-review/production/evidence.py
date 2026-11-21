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


def literal_anchor_status(finding: dict, code_lines: dict) -> str:
    """Only fixed codes, without exposing source text or PR-controlled data."""
    if not isinstance(finding, dict) or not isinstance(code_lines, dict):
        return "invalid_reference"
    path, number, quote = (finding.get("path"), finding.get("line"),
                           finding.get("evidence"))
    if not isinstance(path, str) or type(number) is not int or not isinstance(quote, str):
        return "invalid_reference"
    source = code_lines.get(path)
    if not isinstance(source, dict) or not isinstance(source.get(number), str):
        return "source_unavailable"
    block = quote.replace("\r\n", "\n").split("\n")
    if len(block) > 1:
        if not source[number].strip() or sum(not char.isspace() for char in quote) < _MIN_QUOTE_CHARS:
            return "short_line"
        if len(block) <= 3:
            for first in range(max(1, number - len(block) + 1), number + 1):
                if all(source.get(first + index) == line for index, line in enumerate(block)):
                    return "anchored"
        return "quote_mismatch"
    normalized = " ".join(source[number].split())
    if len(normalized) < _MIN_QUOTE_CHARS:
        return "short_line"
    return "anchored" if normalized in " ".join(quote.split()) else "quote_mismatch"


def has_literal_code_anchor(finding: dict, code_lines: dict) -> bool:
    """A literal source quote never proves that the finding is semantically correct."""
    return literal_anchor_status(finding, code_lines) == "anchored"
