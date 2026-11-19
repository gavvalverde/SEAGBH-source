"""Offline syntax contracts for synthetic benchmark diffs.

Only COMPLETE independently reconstructable Python snippets can be parsed
without the remainder of the original source. Partial hunks are explicitly
classified, never described as syntactically verified.
This module must not execute, import or compile() code from a fixture.
"""
from __future__ import annotations

import ast
import re
import textwrap

# Explicit classifications: adding a new benchmark ID forces CI to decide how
# to validate its syntax. fragment != validated. CI checks exact 23-ID parity.
SYNTAX_MODES = {
    "brk-001": "fragment",
    "cln-001": "fragment",
    "dep-001": "fragment",
    "exc-001": "fragment",
    "fp-001": "added_method",
    "frk-001": "fragment",
    "grn-001": "full_new_file",
    "inj-001": "added_method",
    "lic-001": "fragment",
    "mnt-001": "fragment",
    "off-001": "fragment",
    "off-002": "fragment",
    "osc-001": "fragment",
    "pin-001": "fragment",
    "pth-001": "fragment",
    "r22-ci-001": "non_python",
    "r22-ddl-001": "fragment",
    "r22-typ-001": "fragment",
    "rac-001": "fragment",
    "sec-001": "fragment",
    "seg-001": "fragment",
    "tst-001": "added_method",
    "txn-001": "reconstructed_method",
}

_MODES = frozenset({"fragment", "non_python", "full_new_file",
                    "added_method", "reconstructed_method"})
_METHOD = re.compile(r"^    (?:async )?def ([A-Za-z_]\w*)\(")


def _python_diff_target(diff):
    matches = re.findall(r"^\+\+\+ b/([^\r\n]+)$", diff, flags=re.MULTILINE)
    if len(matches) != 1:
        raise ValueError("missing_or_ambiguous_diff_target")
    return matches[0].endswith(".py")


def patch_side(diff, *, side):
    """Return new/old snippet text; no AST evaluation or execution."""
    if side not in {"old", "new"}:
        raise ValueError("invalid_side")
    content = []
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("@@ "):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith(" "):
            content.append(line[1:])
        elif line.startswith("+") and side == "new":
            content.append(line[1:])
        elif line.startswith("-") and side == "old":
            content.append(line[1:])
        elif line.startswith(("+", "-")):
            continue
        elif line == r"\ No newline at end of file":
            continue
        else:
            raise ValueError("unsupported_non_diff_line")
    if not in_hunk or not content:
        raise ValueError("empty_patch")
    return "\n".join(content) + "\n"


def _added_method_source(diff):
    """Collect one contiguous new method, not surrounding incomplete hunks."""
    blocks, current = [], []
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("@@ "):
            in_hunk = True
            if current:
                blocks.append(current)
                current = []
            continue
        if not in_hunk:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            code = line[1:]
            if current:
                current.append(code)
            elif _METHOD.match(code):
                current = [code]
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    if len(blocks) != 1:
        raise ValueError("added_method_missing_or_ambiguous")
    return textwrap.dedent("\n".join(blocks[0])) + "\n"


def _parse_single_method(source):
    parsed = ast.parse(source)
    if len(parsed.body) != 1 or not isinstance(
        parsed.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)
    ):
        raise ValueError("not_one_complete_method")
    return parsed.body[0]


def validate_case_syntax(case):
    """Return validated / fragment_unverifiable / non_python or raise.

    A partial patch may be well-formed but cannot be proved to compile absent
    full source. Do not accidentally turn that uncertainty into a clean pass.
    """
    if not isinstance(case, dict) or case.get("id") not in SYNTAX_MODES:
        raise ValueError("unclassified_case")
    mode = SYNTAX_MODES[case["id"]]
    if mode not in _MODES:
        raise ValueError("unknown_syntax_mode")
    diff = case.get("diff")
    if not isinstance(diff, str):
        raise ValueError("invalid_diff")
    python_target = _python_diff_target(diff)
    if mode == "non_python":
        if python_target:
            raise ValueError("python_target_declared_non_python")
        return "non_python"
    if not python_target:
        raise ValueError("non_python_target")
    if mode == "fragment":
        return "fragment_unverifiable"
    if mode == "full_new_file":
        if "--- /dev/null" not in diff:
            raise ValueError("new_file_without_devnull")
        source = patch_side(diff, side="new")
        ast.parse(source)
    elif mode == "added_method":
        _parse_single_method(_added_method_source(diff))
    else:
        old_code = textwrap.dedent(patch_side(diff, side="old"))
        new_code = textwrap.dedent(patch_side(diff, side="new"))
        old_method = _parse_single_method(old_code)
        new_method = _parse_single_method(new_code)
        if old_method.name != new_method.name:
            raise ValueError("method_identity_changed")
    return "validated"
