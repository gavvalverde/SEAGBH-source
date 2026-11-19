"""Trusted Actions entrypoint. All PR input is data; no PR code is fetched/executed."""
from __future__ import annotations
from dataclasses import dataclass, field
import base64
import binascii
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from urllib.parse import quote

import core
import evidence
from transport import HTTPS, Problem, decode

REPOSITORY = "gavvalverde/SEAGBH-source"
BOT_LOGIN = "github-actions[bot]"
MAX_FILES = 50
MAX_DIFF = 81920
MAX_CONTEXT = 16384
MAX_PATCH = 32768
MAX_REVIEWS = 1000
MAX_SOURCE_FILES = 3
MAX_SOURCE_FILE_BYTES = 32768
MAX_SOURCE_SNIPPET_BYTES = 4096
MAX_SOURCE_TOTAL_BYTES = 8192
SOURCE_RADIUS = 12
_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}
_LOCK_GUARD = threading.Lock()
_LOCKS = {}


@dataclass(frozen=True)
class Snapshot:
    number: int
    base_sha: str
    head_sha: str
    title: str = field(repr=False)
    body: str = field(repr=False)
    changed_files: int


@dataclass(frozen=True)
class Outcome:
    code: str
    exit_code: int = 0
    published: bool = False
    stage: str = ""
    usage: dict | None = None
    validation_reason: str | None = None


def snapshot(pr, number):
    try:
        if type(number) is not int or number < 1 or type(pr["number"]) is not int or pr["number"] != number:
            raise Problem("invalid_pr_identity")
        if pr["base"]["repo"]["full_name"] != REPOSITORY:
            raise Problem("invalid_pr_identity")
        if pr["head"]["repo"]["full_name"] != REPOSITORY:
            raise Problem("skip_fork")
        if pr["base"]["ref"] != "main":
            raise Problem("skip_base")
        if pr.get("draft") is not False:
            raise Problem("skip_draft")
        if pr.get("state") != "open":
            raise Problem("skip_closed")
        base, head = pr["base"]["sha"], pr["head"]["sha"]
        if not core._full_sha(base) or not core._full_sha(head):
            raise Problem("invalid_pr_identity")
        title, count = pr["title"], pr["changed_files"]
        body = "" if pr.get("body") is None else pr["body"]
        if not isinstance(title, str) or not isinstance(body, str) or type(count) is not int or count < 1:
            raise Problem("invalid_pr_metadata")
        return Snapshot(number, base, head, title, body, count)
    except (KeyError, TypeError):
        raise Problem("invalid_pr_metadata") from None


def event_snapshot(event, enabled):
    if enabled != "true":
        raise Problem("skip_disabled")
    try:
        if event["repository"]["full_name"] != REPOSITORY or event["repository"]["default_branch"] != "main":
            raise Problem("invalid_repository")
        if event.get("action") not in _ACTIONS:
            raise Problem("skip_event")
        return snapshot(event["pull_request"], event["number"])
    except (KeyError, TypeError):
        raise Problem("invalid_event") from None


def ensure_current(github, pinned):
    current = snapshot(github.pr(pinned.number), pinned.number)
    if (current.base_sha, current.head_sha) != (pinned.base_sha, pinned.head_sha):
        raise Problem("pr_updated")
    return current


def _ranges(numbers):
    result = []
    for number in sorted(set(numbers)):
        if result and number == result[-1][1] + 1:
            result[-1][1] = number
        else:
            result.append([number, number])
    return result


def parse_patch(patch, additions, deletions):
    """Check every hunk counter. Return explicit added/removed/context coordinates."""
    if not isinstance(patch, str) or not patch:
        raise Problem("diff_missing")
    if any(type(v) is not int or v < 0 for v in (additions, deletions)):
        raise Problem("diff_invalid")
    added, removed, context_new, context_old = [], [], [], []
    old_left = new_left = 0
    old_pos = new_pos = 0
    previous_old = previous_new = 0
    seen = False
    # Git patch delimiters are LF/CRLF, not Unicode content such as U+2028.
    lines = patch.split("\n")
    if lines[-1] == "":
        lines.pop()
    for line in lines:
        line = line.removesuffix("\r")
        header = re.fullmatch(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)", line)
        if header:
            if old_left or new_left:
                raise Problem("diff_incomplete")
            old_pos, new_pos = int(header[1]), int(header[3])
            old_left = int(header[2]) if header[2] is not None else 1
            new_left = int(header[4]) if header[4] is not None else 1
            if (old_left and old_pos < 1) or (new_left and new_pos < 1):
                raise Problem("diff_invalid")
            if seen and (old_pos < previous_old or new_pos < previous_new):
                raise Problem("diff_invalid")
            previous_old, previous_new = old_pos + old_left, new_pos + new_left
            seen = True
        elif line == r"\ No newline at end of file":
            if not seen:
                raise Problem("diff_invalid")
        elif seen and line.startswith("+"):
            added.append(new_pos)
            new_pos += 1
            new_left -= 1
        elif seen and line.startswith("-"):
            removed.append(old_pos)
            old_pos += 1
            old_left -= 1
        elif seen and line.startswith(" "):
            context_new.append(new_pos)
            context_old.append(old_pos)
            old_pos += 1
            new_pos += 1
            old_left -= 1
            new_left -= 1
        else:
            raise Problem("diff_invalid")
        if old_left < 0 or new_left < 0:
            raise Problem("diff_invalid")
    if not seen or old_left or new_left or len(added) != additions or len(removed) != deletions:
        raise Problem("diff_incomplete")
    return {"added_new": _ranges(added), "removed_old": _ranges(removed),
            "context_new": _ranges(context_new), "context_old": _ranges(context_old)}, _ranges(added + context_new)


@dataclass(frozen=True)
class Collected:
    diff: str = field(repr=False)
    scope: core.ReviewScope = field(repr=False)
    manifest: dict = field(repr=False)
    code_lines: dict = field(default_factory=dict, repr=False)


def collect(github, pinned):
    if pinned.changed_files > MAX_FILES:
        raise Problem("diff_file_limit")
    comparison = github.compare(pinned)
    try:
        if comparison["base_commit"]["sha"] != pinned.base_sha:
            raise Problem("diff_identity_mismatch")
        files = comparison["files"]
        if not isinstance(files, list) or len(files) != pinned.changed_files:
            raise Problem("diff_incomplete")
    except (KeyError, TypeError):
        raise Problem("diff_incomplete") from None
    chunks, supplied, entries, omitted, seen = [], {}, [], [], set()
    code_lines = {}
    for item in files:
        if not isinstance(item, dict):
            raise Problem("diff_invalid")
        path = item.get("filename")
        if not core._safe_path(path) or len(path.encode("utf-8")) > 1024 or path in seen:
            raise Problem("diff_invalid_path")
        seen.add(path)
        coordinates, ranges = parse_patch(item.get("patch"), item.get("additions"), item.get("deletions"))
        entry = {"path": path, "status": item.get("status"), **coordinates}
        if entry["status"] not in {"added", "modified", "removed", "renamed", "copied", "changed"}:
            raise Problem("diff_invalid")
        if item.get("previous_filename") is not None:
            if not core._safe_path(item["previous_filename"]):
                raise Problem("diff_invalid_path")
            entry["previous_path"] = item["previous_filename"]
        # JSON quoting makes unusual path names unambiguous without altering the patch.
        chunk = "# File " + json.dumps(path, ensure_ascii=True) + "\n" + item["patch"] + "\n"
        size = len(chunk.encode("utf-8"))
        if size > MAX_PATCH or len("".join(chunks).encode("utf-8")) + size > MAX_DIFF:
            omitted.append({"path": path, "reason": "diff_budget"})
            continue
        if len(json.dumps(entries + [entry], ensure_ascii=True).encode("utf-8")) > MAX_CONTEXT // 2:
            omitted.append({"path": path, "reason": "context_budget"})
            continue
        chunks.append(chunk)
        entries.append(entry)
        code_lines[path] = evidence.new_file_lines(item["patch"])
        supplied[path] = core.FileScope(diff_ranges=tuple(map(tuple, ranges)), allow_file_level=entry["status"] == "removed")
    if not chunks:
        raise Problem("diff_no_reviewable_content")
    manifest = {"base_sha": pinned.base_sha, "head_sha": pinned.head_sha,
                "partial": bool(omitted), "files": entries, "omitted": omitted}
    if len(json.dumps(manifest, ensure_ascii=True).encode("utf-8")) > MAX_CONTEXT:
        raise Problem("context_limit")
    # The API is queried again to detect a changing PR; patches themselves came from fixed SHAs.
    ensure_current(github, pinned)
    return Collected("".join(chunks), core.ReviewScope(pinned.head_sha, supplied), manifest, code_lines)


def source_context(github, pinned, collected):
    """Read only bounded same-file Python context from the immutable head SHA.

    The snippets are untrusted PR data and never expand ReviewScope.
    """
    excerpts = []
    attempts = 0
    total_bytes = 0
    for entry in collected.manifest["files"]:
        if attempts >= MAX_SOURCE_FILES or total_bytes >= MAX_SOURCE_TOTAL_BYTES:
            break
        path = entry["path"]
        if (not path.startswith("src/") or not path.endswith(".py")
                or entry["status"] == "removed"):
            continue
        ranges = entry["added_new"] or entry["context_new"]
        if not ranges:
            continue
        attempts += 1
        body = github.source_at(pinned.head_sha, path)
        if body is None:
            continue
        lines = body.splitlines()
        selected = set()
        for lo, hi in ranges:
            if type(lo) is not int or type(hi) is not int or lo < 1 or hi < lo or hi > len(lines):
                raise Problem("source_mismatch")
            selected.update(range(max(1, lo - SOURCE_RADIUS),
                                  min(len(lines), hi + SOURCE_RADIUS) + 1))
        snippet, size = [], 0
        for number in sorted(selected):
            row = f"L{number}: {lines[number-1]}\n"
            length = len(row.encode("utf-8"))
            if size + length > MAX_SOURCE_SNIPPET_BYTES:
                break
            snippet.append(row)
            size += length
        if snippet:
            text = "".join(snippet)
            if total_bytes + size > MAX_SOURCE_TOTAL_BYTES:
                break
            excerpts.append({"path": path, "source_lines": text})
            total_bytes += size
    return excerpts


class GitHub:
    def __init__(self, token, *, http=None):
        if not isinstance(token, str) or not token or "\n" in token or "\r" in token:
            raise Problem("authentication_error")
        self._token = token
        self.http = http or HTTPS()

    def api(self, method, path, value=None):
        reply = self.http.send("api.github.com", method, path,
            {"Authorization": "Bearer " + self._token, "Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "SEAGBH-AI-Reviewer",
             "Content-Type": "application/json"},
            json.dumps(value, ensure_ascii=True).encode() if value is not None else None)
        if reply.status in (401, 403):
            raise Problem("authentication_error", reply.status)
        if not 200 <= reply.status <= 299:
            raise Problem("github_http_error", reply.status)
        return decode(reply)

    def pr(self, number):
        return self.api("GET", f"/repos/{REPOSITORY}/pulls/{number}")

    def compare(self, pinned):
        return self.api("GET", f"/repos/{REPOSITORY}/compare/{pinned.base_sha}...{pinned.head_sha}?per_page=100&page=1")

    def source_at(self, sha, path):
        """Request small plain-text files, never untrusted executable code."""
        if (not core._full_sha(sha) or not core._safe_path(path)
                or not path.startswith("src/") or not path.endswith(".py")):
            raise Problem("source_invalid")
        data = self.api("GET",
            f"/repos/{REPOSITORY}/contents/{quote(path, safe='/')}?ref={sha}")
        if not isinstance(data, dict) or data.get("path") != path:
            raise Problem("source_mismatch")
        if data.get("type") != "file":
            return None
        size = data.get("size")
        if type(size) is not int or size < 0:
            raise Problem("source_invalid")
        if size > MAX_SOURCE_FILE_BYTES:
            return None
        if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
            raise Problem("source_invalid")
        try:
            raw = base64.b64decode(
                data["content"].replace("\n", "").replace("\r", ""),
                validate=True)
            if len(raw) != size:
                raise Problem("source_mismatch")
            return raw.decode("utf-8")
        except (UnicodeError, ValueError, binascii.Error):
            raise Problem("source_invalid") from None

    def bot(self):
        user = self.api("GET", "/users/" + quote(BOT_LOGIN, safe=""))
        if not isinstance(user, dict) or user.get("login") != BOT_LOGIN or user.get("type") != "Bot" or type(user.get("id")) is not int:
            raise Problem("authentication_error")
        return user["id"]

    def reviews(self, number):
        result = []
        for page in range(1, MAX_REVIEWS // 100 + 1):
            rows = self.api("GET", f"/repos/{REPOSITORY}/pulls/{number}/reviews?per_page=100&page={page}")
            if not isinstance(rows, list):
                raise Problem("deduplication_error")
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise Problem("deduplication_limit")

    def post(self, pinned, body):
        return self.api("POST", f"/repos/{REPOSITORY}/pulls/{pinned.number}/reviews",
                        {"body": body, "commit_id": pinned.head_sha, "event": "COMMENT"})


def marker(pinned, bot_id, version):
    data = {"repo": REPOSITORY, "pr": pinned.number, "head": pinned.head_sha,
            "version": version, "bot_id": bot_id, "status": "success"}
    return "<!-- seagbh-ai-review " + json.dumps(data, sort_keys=True, separators=(",", ":")) + " -->"


def already_reviewed(rows, pinned, bot_id, version):
    suffix = "\n" + marker(pinned, bot_id, version)
    for row in rows:
        if not isinstance(row, dict):
            raise Problem("deduplication_error")
        user = row.get("user") or {}
        if not isinstance(user, dict):
            raise Problem("deduplication_error")
        if (user.get("id") == bot_id and user.get("login") == BOT_LOGIN and user.get("type") == "Bot"
                and row.get("state") == "COMMENTED" and row.get("commit_id") == pinned.head_sha
                and isinstance(row.get("body"), str) and row["body"].endswith(suffix)):
            return True
    return False


_MODEL_LABELS = {
    "deepseek/deepseek-v4.1-flash-1017": "DeepSeek V4.1 Flash 1017",
    "minimax/minimax-m2.7": "MiniMax M2.7",
}
_SEVERITY_LABELS = {
    "critical": "Crítico",
    "important": "Importante",
    "minor": "Menor",
    "informational": "Informativo",
}
_CATEGORY_LABELS = {
    "regression": "Regressão",
    "edge-case": "Caso-limite",
    "unhandled-state": "Estado não tratado",
    "exception-handling": "Tratamento de exceções",
    "race": "Concorrência",
    "security": "Segurança",
    "data-integrity": "Integridade de dados",
    "broken-api": "API incompatível",
    "insufficient-test": "Teste insuficiente",
    "out-of-scope": "Fora do escopo",
    "other": "Outro",
}
_CONFIDENCE_LABELS = {"high": "Alta", "medium": "Média", "low": "Baixa"}


def _markdown_text(value):
    """Escape untrusted model strings, including Markdown and HTML injection."""
    safe = html.escape(str(value).replace("\r\n", "\n").replace("\r", "\n"))
    safe = re.sub(r"([\\\x60*_{}\[\]()#+.!|>~-])", r"\\\1", safe)
    return safe.replace("@", "&#64;")


def _html_code(value, *, block=False):
    """Escaped code evidence: arbitrary backticks cannot close a code fence."""
    safe = html.escape(str(value)).replace("@", "&#64;")
    return "<pre><code>" + safe + "</code></pre>" if block else "<code>" + safe + "</code>"


def _trusted_model_label(model_id):
    # The ID comes from the trusted request payload, never from PR/model text.
    if not isinstance(model_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", model_id):
        raise Problem("invalid_model_identifier")
    return _MODEL_LABELS.get(model_id, model_id)


def render(result, collected, pinned, bot_id, version, *, model_id):
    if not result.publishable or result.sha != pinned.head_sha:
        raise Problem("publication_not_validated")
    review = result.review  # exclusively the accepted object, never the API envelope
    partial = collected.manifest["partial"]
    parts = [
        "## Revisão de código por IA | " + _trusted_model_label(model_id),
        "",
        "**Escopo: REVISÃO PARCIAL.** Somente o diff fornecido foi analisado." if partial else
        "**Escopo:** alterações fornecidas no diff do Pull Request.",
        "",
        "### Resumo",
        _markdown_text(review["summary"] if not partial and not result.rejected else
                       "Somente os achados validados no escopo analisado estão listados abaixo."),
        "",
        f"### Achados ({len(review['findings'])})",
    ]
    for index, item in enumerate(review["findings"], 1):
        severity = _SEVERITY_LABELS[item["severity"]]
        category = _CATEGORY_LABELS[item["category"]]
        confidence = _CONFIDENCE_LABELS[item["confidence"]]
        location = _html_code(item["path"])
        if item["line"] is not None:
            location += f", linha {item['line']}"
        else:
            location += " (arquivo inteiro)"
        parts.extend([
            "",
            f"#### {index}. {severity}: {_markdown_text(item['title'])}",
            f"**Local:** {location}",
            f"**Categoria:** {category} | **Confiança:** {confidence}",
            "",
            "**Problema, impacto e recomendação**",
            _markdown_text(item["body"]),
            "",
            "**Evidência no código**",
            _html_code(item["evidence"], block=True),
            "",
            ("**Conferência textual:** citação da linha localizada no diff; "
             "isso não comprova a existência do defeito."
             if evidence.has_literal_code_anchor(item, collected.code_lines) else
             "**Conferência textual:** citação literal da linha não confirmada "
             "automaticamente; avaliação humana necessária."),
        ])
    if not review["findings"]:
        parts.extend(["", "Nenhum achado validado no escopo efetivamente analisado."])
    if result.rejected:
        parts.extend(["", f"**Achados descartados por estarem fora do escopo:** {len(result.rejected)}."])
    parts.extend(["", marker(pinned, bot_id, version)])
    body = "\n".join(parts)
    if len(body.encode("utf-8")) > 60000:
        raise Problem("publication_size_limit")
    return body


def run_review(event, enabled, github, model, *, version):
    """Mocks may implement the small GitHub and model interfaces. No hidden retry of POST."""
    try:
        pinned = event_snapshot(event, enabled)
    except Problem as error:
        return Outcome(error.code, 0 if error.code.startswith("skip_") else 1)
    key = (REPOSITORY, pinned.number)
    with _LOCK_GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        return Outcome("skip_busy")
    stage = "identity"
    try:
        ensure_current(github, pinned)
        bot_id = github.bot()
        if already_reviewed(github.reviews(pinned.number), pinned, bot_id, version):
            return Outcome("skip_duplicate")
        stage = "collection"
        collected = collect(github, pinned)
        excerpts = source_context(github, pinned, collected)
        title = pinned.title.encode("utf-8")[:1024].decode("utf-8", errors="ignore")
        body = pinned.body.encode("utf-8")[:4096].decode("utf-8", errors="ignore")
        collected.manifest["partial"] |= title != pinned.title or body != pinned.body
        context = dict(collected.manifest)
        if excerpts:
            context["source_snippets"] = excerpts
        payload = core.build_payload(title, body, collected.diff,
            context=json.dumps(context, ensure_ascii=True, sort_keys=True),
            trunc_note="[PARTIAL REVIEW]" if collected.manifest["partial"] else "",
            omit_info=json.dumps(collected.manifest["omitted"], ensure_ascii=True) if collected.manifest["omitted"] else "")
        ensure_current(github, pinned)
        stage = "model"
        content = model.complete(payload)
        stage = "validation"
        result = core.process_response(content, collected.scope, expected_sha=pinned.head_sha, response_sha=pinned.head_sha)
        if not result.publishable:
            return Outcome(result.status.value, 1, stage=stage,
                           validation_reason=result.reason)
        ensure_current(github, pinned)
        if already_reviewed(github.reviews(pinned.number), pinned, bot_id, version):
            return Outcome("skip_duplicate")
        text = render(result, collected, pinned, bot_id, version, model_id=payload["model"])
        ensure_current(github, pinned)
        stage = "publication"
        try:
            published = github.post(pinned, text)
        except Problem as error:
            if error.code == "authentication_error":
                raise
            if error.status is not None and 400 <= error.status < 500:
                raise Problem("publication_error", error.status) from None
            # An accepted POST may have lost its acknowledgment. Query once, never repeat POST.
            if already_reviewed(github.reviews(pinned.number), pinned, bot_id, version):
                return Outcome("published_reconciled", published=True, stage=stage)
            raise Problem("publication_uncertain") from None
        if not already_reviewed([published], pinned, bot_id, version):
            raise Problem("publication_unconfirmed")
        return Outcome("published", published=True, stage=stage)
    except Problem as error:
        return Outcome(error.code, 1, stage=stage, usage=error.usage)  # fail closed
    except Exception:
        return Outcome("unexpected_execution_error", 1, stage=stage)
    finally:
        lock.release()


def reviewer_version():
    base = Path(__file__).resolve().parent
    files = [base / name for name in ("core.py", "runner.py", "transport.py", "evidence.py", "requirements.txt")]
    files += [base.parent / "review-schema.json", base.parent / "benchmarks/harness/prompt_builder.py"]
    digest = hashlib.sha256()
    for file in files:
        digest.update(file.name.encode() + b"\0" + file.read_bytes() + b"\0")
    return digest.hexdigest()


def main():
    # Default is fail-closed, before clients, credentials, event files or network.
    if os.environ.get("AI_REVIEW_ENABLED") != "true":
        print('{"code":"skip_disabled"}')
        return 0
    try:
        trusted = os.environ.get("AI_REVIEW_TRUSTED_SHA")
        if (os.environ.get("GITHUB_EVENT_NAME") != "pull_request_target"
                or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
                or not core._full_sha(trusted) or trusted != os.environ.get("GITHUB_SHA")):
            raise Problem("untrusted_execution_context")
        checked_out = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        if checked_out != trusted:
            raise Problem("untrusted_checkout")
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        event_snapshot(event, "true")
        from transport import OpenRouter
        outcome = run_review(event, "true", GitHub(os.environ.get("GITHUB_TOKEN")),
                             OpenRouter(os.environ.get("OPENROUTER_API_KEY")), version=reviewer_version())
    except Problem as error:
        outcome = Outcome(error.code, 0 if error.code.startswith("skip_") else 1)
    except Exception:
        outcome = Outcome("unexpected_execution_error", 1)
    diagnostic = {"code": outcome.code, "stage": outcome.stage, "published": outcome.published}
    # Only publish allowlisted numeric counters on a generation-limit error.
    # No upstream content or metadata is printed to the public Actions log.
    if outcome.code in ("model_output_limit", "reasoning_control_ignored") and outcome.usage is not None:
        diagnostic["usage"] = outcome.usage
    # Fixed application-owned labels only. Never log any model text, raw
    # provider diagnostics, JSON snippets or user-controlled paths.
    allowed_validation_reasons = frozenset({
        "invalid_json", "incomplete_json", "schema_violation",
        "missing_content", "unexpected_content_type", "response_not_complete",
        "all_findings_rejected", "invalid_result", "invalid_scope",
        "review_revision_mismatch",
    })
    if outcome.stage == "validation" and outcome.validation_reason in allowed_validation_reasons:
        diagnostic["validation_reason"] = outcome.validation_reason
    print(json.dumps(diagnostic))
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
