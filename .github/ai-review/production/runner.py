"""Trusted Actions entrypoint. All PR input is data; no PR code is fetched/executed."""
from __future__ import annotations
from dataclasses import dataclass, field
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
from transport import HTTPS, Problem, decode

REPOSITORY = "gavvalverde/SEAGBH-source"
BOT_LOGIN = "github-actions[bot]"
MAX_FILES = 50
MAX_DIFF = 81920
MAX_CONTEXT = 16384
MAX_PATCH = 32768
MAX_REVIEWS = 1000
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
        supplied[path] = core.FileScope(diff_ranges=tuple(map(tuple, ranges)), allow_file_level=entry["status"] == "removed")
    if not chunks:
        raise Problem("diff_no_reviewable_content")
    manifest = {"base_sha": pinned.base_sha, "head_sha": pinned.head_sha,
                "partial": bool(omitted), "files": entries, "omitted": omitted}
    if len(json.dumps(manifest, ensure_ascii=True).encode("utf-8")) > MAX_CONTEXT:
        raise Problem("context_limit")
    # The API is queried again to detect a changing PR; patches themselves came from fixed SHAs.
    ensure_current(github, pinned)
    return Collected("".join(chunks), core.ReviewScope(pinned.head_sha, supplied), manifest)


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


def render(result, collected, pinned, bot_id, version):
    if not result.publishable or result.sha != pinned.head_sha:
        raise Problem("publication_not_validated")
    review = result.review  # exclusively the accepted object, never the API envelope
    def block(value):
        return "<pre>" + html.escape(str(value)).replace("@", "&#64;") + "</pre>"
    partial = collected.manifest["partial"]
    parts = ["## AI Review (MiniMax M2.7)",
             "Revis?o PARCIAL: somente os arquivos/hunks fornecidos." if partial else "Escopo: diff fornecido nesta revis?o.",
             block(review["summary"] if not partial and not result.rejected else
                   "Somente os findings aceitos no escopo fornecido est?o listados abaixo.")]
    if review["findings"]:
        for item in review["findings"]:
            parts.extend([block(item["severity"] + ": " + item["title"]),
                          block(item["path"] + ":" + str(item["line"])),
                          block(item["body"]), block(item["evidence"])])
    else:
        parts.append("Nenhum finding no escopo fornecido pelo modelo e validado.")
    if result.rejected:
        parts.append(f"Findings rejeitados pela pol?tica de escopo: {len(result.rejected)}.")
    parts.append(marker(pinned, bot_id, version))
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
        title = pinned.title.encode("utf-8")[:1024].decode("utf-8", errors="ignore")
        body = pinned.body.encode("utf-8")[:4096].decode("utf-8", errors="ignore")
        collected.manifest["partial"] |= title != pinned.title or body != pinned.body
        payload = core.build_payload(title, body, collected.diff,
            context=json.dumps(collected.manifest, ensure_ascii=True, sort_keys=True),
            trunc_note="[PARTIAL REVIEW]" if collected.manifest["partial"] else "",
            omit_info=json.dumps(collected.manifest["omitted"], ensure_ascii=True) if collected.manifest["omitted"] else "")
        ensure_current(github, pinned)
        stage = "model"
        content = model.complete(payload)
        stage = "validation"
        result = core.process_response(content, collected.scope, expected_sha=pinned.head_sha, response_sha=pinned.head_sha)
        if not result.publishable:
            return Outcome(result.status.value, 1, stage=stage)
        ensure_current(github, pinned)
        if already_reviewed(github.reviews(pinned.number), pinned, bot_id, version):
            return Outcome("skip_duplicate")
        text = render(result, collected, pinned, bot_id, version)
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
        return Outcome(error.code, 1, stage=stage)  # no untrusted fallback review is posted
    except Exception:
        return Outcome("unexpected_execution_error", 1, stage=stage)
    finally:
        lock.release()


def reviewer_version():
    base = Path(__file__).resolve().parent
    files = [base / name for name in ("core.py", "runner.py", "transport.py", "requirements.txt")]
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
    print(json.dumps({"code": outcome.code, "stage": outcome.stage, "published": outcome.published}))
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
