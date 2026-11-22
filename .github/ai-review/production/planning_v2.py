"""Planejamento offline do AI Reviewer v2: cobertura integral, âncoras e limites.

Não consulta redes, não executa código de PRs e não omite hunks silenciosamente.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType

import core
import evidence
import runner
from transport import Problem

_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*")
MAX_FRAGMENT_BYTES = 9000
MAX_CHUNK_BYTES = 42000
MAX_CHUNKS = 8
MAX_ANCHORS = 6000


@dataclass(frozen=True)
class Part:
    path: str
    patch: str
    additions: int
    deletions: int
    status: str


@dataclass(frozen=True)
class Chunk:
    index: int
    parts: tuple[Part, ...]
    anchors: tuple[tuple[str, str, int | None, str], ...]


@dataclass(frozen=True)
class Plan:
    base_sha: str
    head_sha: str
    files: tuple[str, ...]
    chunks: tuple[Chunk, ...]
    anchors: object
    digest: str
    added_total: int

    @property
    def coverage(self):
        return {
            "files_supplied": len(self.files), "files_omitted": 0,
            "test_files_omitted": 0, "chunks_planned": len(self.chunks),
            "chunks_completed": 0, "hunks_supplied": sum(len(c.parts) for c in self.chunks),
            "added_lines_supplied": self.added_total,
        }


def split_patch(path, patch, status):
    """Fragmenta hunks enormes mantendo coordenadas OLD/NEW absolutas."""
    rows = patch.split("\n")
    if rows[-1] == "":
        rows.pop()
    hunks = []
    for row in rows:
        m = _HUNK.fullmatch(row.removesuffix("\r"))
        if m:
            hunks.append([int(m[1]), int(m[3]), []])
        elif hunks:
            hunks[-1][2].append(row)
        else:
            raise Problem("v2_patch_invalid")

    result = []
    for first_old, first_new, body in hunks:
        units = []
        for row in body:
            if row == r"\ No newline at end of file" and units:
                units[-1].append(row)
            elif row.startswith((" ", "+", "-")):
                units.append([row])
            else:
                raise Problem("v2_patch_invalid")
        old = first_old
        new = first_new
        group = []
        weight = 0
        old_begin = old
        new_begin = new

        def finish():
            nonlocal group, weight, old_begin, new_begin
            if not group:
                return
            old_count = sum(x.startswith((" ", "-")) for x in group)
            new_count = sum(x.startswith((" ", "+")) for x in group)
            added = sum(x.startswith("+") for x in group)
            deleted = sum(x.startswith("-") for x in group)
            header = f"@@ -{old_begin},{old_count} +{new_begin},{new_count} @@"
            fragment = header + "\n" + "\n".join(group)
            runner.parse_patch(fragment, added, deleted)
            result.append(Part(path, fragment, added, deleted, status))
            group = []
            weight = 0
            old_begin = old
            new_begin = new

        for unit in units:
            cost = len(("\n".join(unit) + "\n").encode("utf-8"))
            if cost + 120 > MAX_FRAGMENT_BYTES:
                raise Problem("v2_fragment_oversize")
            if group and weight + cost + 120 > MAX_FRAGMENT_BYTES:
                finish()
            group.extend(unit)
            weight += cost
            for row in unit:
                if row.startswith((" ", "-")):
                    old += 1
                if row.startswith((" ", "+")):
                    new += 1
        finish()
    if not result:
        raise Problem("v2_patch_empty")
    return result


def _added_new_lines(patch):
    """Return changed (+) lines with absolute NEW coordinates.

    Keep context in the diff for reasoning; only changed lines are citeable.
    A deletion-only fragment can use an explicitly file-level anchor.
    """
    rows = {}
    pos = None
    for line in patch.split("\n"):
        line = line.removesuffix("\r")
        header = _HUNK.fullmatch(line)
        if header:
            pos = int(header[3])
            continue
        if pos is None or line == r"\ No newline at end of file":
            continue
        if line.startswith("+"):
            rows[pos] = line[1:]
            pos += 1
        elif line.startswith(" "):
            pos += 1
        elif line.startswith("-"):
            continue
        else:
            raise Problem("v2_patch_invalid")
    return rows


def _anchors_for(part, chunk_id=0):
    lines = _added_new_lines(part.patch)
    if not lines:
        # Only deletions: location is file-level, never an invented NEW line.
        lines = {None: ""}
    result = []
    for line, source in sorted(lines.items(), key=lambda pair: -1 if pair[0] is None else pair[0]):
        digest = hashlib.sha256(json.dumps(
            [part.path, line, part.patch if line is None else source, chunk_id], ensure_ascii=True
        ).encode("utf-8")).hexdigest()
        result.append(("a" + digest[:16], part.path, line, digest, chunk_id))
    return result

def build_plan(github, pinned):
    if pinned.changed_files > runner.MAX_FILES:
        raise Problem("v2_file_limit")
    comparison = github.compare(pinned)
    if not isinstance(comparison, dict) or not isinstance(comparison.get("base_commit"), dict):
        raise Problem("v2_comparison_invalid")
    if comparison["base_commit"].get("sha") != pinned.base_sha:
        raise Problem("v2_comparison_mismatch")
    files = comparison.get("files")
    if not isinstance(files, list) or len(files) != pinned.changed_files:
        raise Problem("v2_files_incomplete")
    paths = []
    parts = []
    added_total = 0
    for item in files:
        if not isinstance(item, dict):
            raise Problem("v2_file_invalid")
        path = item.get("filename")
        status = item.get("status")
        if (not core._safe_path(path) or path in paths or
                len(path.encode("utf-8")) > 1024 or
                status not in {"added", "modified", "removed", "renamed", "copied", "changed"}):
            raise Problem("v2_path_invalid")
        if (item.get("previous_filename") is not None and
                not core._safe_path(item["previous_filename"])):
            raise Problem("v2_path_invalid")
        patch = item.get("patch")
        runner.parse_patch(patch, item.get("additions"), item.get("deletions"))
        pieces = split_patch(path, patch, status)
        if (sum(p.additions for p in pieces) != item["additions"] or
                sum(p.deletions for p in pieces) != item["deletions"]):
            raise Problem("v2_hunk_incomplete")
        paths.append(path)
        parts.extend(pieces)
        added_total += item["additions"]

    chunks = []
    registry = {}
    pending = []
    size = 0

    def flush():
        nonlocal pending, size
        if not pending:
            return
        if len(chunks) == MAX_CHUNKS:
            raise Problem("v2_chunk_limit")
        values = []
        for p in pending:
            for anchor in _anchors_for(p, len(chunks) + 1):
                if anchor[0] in registry:
                    raise Problem("v2_anchor_collision")
                registry[anchor[0]] = anchor
                values.append(anchor)
        chunks.append(Chunk(len(chunks) + 1, tuple(pending), tuple(values)))
        pending = []
        size = 0

    for part in parts:
        source_bytes = len(("# File " + json.dumps(part.path) + "\n" + part.patch).encode("utf-8"))
        anchors = _anchors_for(part)
        # Reserve bytes for the model-facing anchor registry and JSON envelope.
        # Registry is grouped by path: each compact [anchor_id, line] pair is
        # transmitted once; original hashes remain in the trusted manifest.
        estimate = source_bytes + 100 + len(part.path.encode("utf-8")) + sum(
            35 + len(str(a[2])) for a in anchors
        )
        if estimate > MAX_CHUNK_BYTES:
            raise Problem("v2_fragment_budget")
        if pending and size + estimate > MAX_CHUNK_BYTES:
            flush()
        pending.append(part)
        size += estimate
    flush()
    if not chunks or len(registry) > MAX_ANCHORS:
        raise Problem("v2_plan_limit")
    canonical = json.dumps({
        "base": pinned.base_sha, "head": pinned.head_sha,
        "paths": paths,
        "anchors": sorted(registry.values()),
        # Bind ALL diff text, including deletion/context rows without anchors.
        "fragment_sha256": [
            hashlib.sha256((part.path + "\0" + part.patch).encode("utf-8")).hexdigest()
            for part in parts
        ],
    }, ensure_ascii=True, sort_keys=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    runner.ensure_current(github, pinned)
    return Plan(pinned.base_sha, pinned.head_sha, tuple(paths), tuple(chunks),
                MappingProxyType(registry), digest, added_total)
