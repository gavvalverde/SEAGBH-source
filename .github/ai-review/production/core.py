"""Offline review integration core; no HTTP, GitHub, logging or publication.

TRUST: load this module, the schema and prompt_builder from the SAME trusted
repository revision. Future privileged Actions jobs must never execute PR-head
code. PR strings are data, not module locations or executable instructions.

Only process_response results with publishable=True are publication candidates.
review returns a detached copy of the accepted JSON; diagnostics contain codes
and finding indices only, never input content, paths or validator messages.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
import importlib.util
import json
import math
from pathlib import Path
import re
from types import MappingProxyType

from jsonschema import Draft7Validator
from referencing import Registry
from referencing.exceptions import NoSuchResource


_BASE = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "_seagbh_trusted_prompt", _BASE / "benchmarks/harness/prompt_builder.py"
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("Trusted prompt module unavailable")
_prompt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_prompt)


def build_payload(title, body, diff, context="", trunc_note="", omit_info="", model=None):
    """Forward every argument unchanged to the existing benchmark contract."""
    return _prompt.build_request(
        title, body, diff, context=context, trunc_note=trunc_note,
        omit_info=omit_info, model=model,
    )


def _no_remote_schema(uri):
    raise NoSuchResource(ref=uri)


_schema = json.loads((_BASE / "review-schema.json").read_text(encoding="utf-8"))
if _schema.get("$schema") != "http://json-schema.org/draft-07/schema#":
    raise RuntimeError("Unexpected trusted schema draft")
Draft7Validator.check_schema(_schema)
_validator = Draft7Validator(_schema, registry=Registry(retrieve=_no_remote_schema))


class Status(str, Enum):
    REVIEW = "valid_with_findings"
    CLEAN = "valid_without_findings"
    TRANSPORT_ERROR = "transport_error"
    HTTP_ERROR = "http_error"
    EMPTY_RESPONSE = "empty_response"
    INCOMPLETE_RESPONSE = "incomplete_response"
    PARSE_ERROR = "parse_error"
    SCHEMA_ERROR = "schema_error"
    FILTER_ERROR = "filter_error"
    SHA_MISMATCH = "sha_mismatch"


@dataclass(frozen=True)
class Rejection:
    index: int
    reason: str


@dataclass(frozen=True)
class ReviewResult:
    status: Status
    reason: str = ""
    sha: str | None = None
    http_status: int | None = None
    rejected: tuple[Rejection, ...] = ()
    _review_json: str | None = field(default=None, repr=False)

    @property
    def ok(self):
        return self.status in (Status.REVIEW, Status.CLEAN)

    @property
    def publishable(self):
        return self.ok and self.sha is not None and self._review_json is not None

    @property
    def review(self):
        """Return a new copy; callers cannot mutate the retained validated JSON."""
        return json.loads(self._review_json) if self._review_json is not None else None


def _valid(review, *, sha=None, rejected=()):
    status = Status.REVIEW if review["findings"] else Status.CLEAN
    return ReviewResult(
        status, sha=sha, rejected=tuple(rejected),
        _review_json=json.dumps(review, ensure_ascii=True, allow_nan=False),
    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non_json_constant")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non_finite_number")
    return number


def validate_review(content: str | None, *, complete: bool = True) -> ReviewResult:
    """Parse one strict JSON document and enforce the complete trusted Draft 7.

    This intermediate result is NOT publishable: scope and SHA are unchecked.
    complete=False signals known truncation, even if the JSON happens to parse.
    Fences/prose, duplicate keys and nonstandard/non-finite numbers are rejected.
    """
    if not complete:
        return ReviewResult(Status.INCOMPLETE_RESPONSE, "response_not_complete")
    if content is None or (isinstance(content, str) and not content.strip()):
        return ReviewResult(Status.EMPTY_RESPONSE, "missing_content")
    if not isinstance(content, str):
        return ReviewResult(Status.SCHEMA_ERROR, "unexpected_content_type")
    try:
        review = json.loads(
            content, object_pairs_hook=_unique_object,
            parse_constant=_reject_constant, parse_float=_finite_float,
        )
    except json.JSONDecodeError as error:
        incomplete = error.msg.startswith("Unterminated string") or error.pos >= len(content.rstrip())
        return ReviewResult(
            Status.INCOMPLETE_RESPONSE if incomplete else Status.PARSE_ERROR,
            "incomplete_json" if incomplete else "invalid_json",
        )
    except (ValueError, RecursionError):
        return ReviewResult(Status.PARSE_ERROR, "invalid_json")
    try:
        if next(_validator.iter_errors(review), None) is not None:
            return ReviewResult(Status.SCHEMA_ERROR, "schema_violation")
    except RecursionError:
        return ReviewResult(Status.SCHEMA_ERROR, "schema_violation")
    return _valid(review)


def _safe_path(path):
    return (
        isinstance(path, str) and bool(path)
        and not path.startswith(("/", "\\")) and "\\" not in path
        and re.match(r"^[A-Za-z]:", path) is None
        and not any(ord(char) < 32 or ord(char) == 127 for char in path)
        and all(part not in ("", ".", "..") for part in path.split("/"))
    )


def _full_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


@dataclass(frozen=True)
class FileScope:
    """Inclusive NEW-file line ranges actually supplied, never the whole PR.

    context_ranges are supplied contextual lines outside the sent diff. They
    require out_of_diff=True and the schema's justification. A null line is
    allowed only when the caller explicitly attests file-level review scope.
    """
    diff_ranges: tuple[tuple[int, int], ...] = ()
    context_ranges: tuple[tuple[int, int], ...] = ()
    allow_file_level: bool = False

    def __post_init__(self):
        if type(self.allow_file_level) is not bool:
            raise ValueError("invalid_file_scope")
        for name in ("diff_ranges", "context_ranges"):
            ranges = tuple(tuple(pair) for pair in getattr(self, name))
            if any(
                len(pair) != 2 or any(type(v) is not int for v in pair)
                or pair[0] < 1 or pair[1] < pair[0]
                for pair in ranges
            ):
                raise ValueError("invalid_line_ranges")
            object.__setattr__(self, name, ranges)


@dataclass(frozen=True)
class ReviewScope:
    sha: str
    files: Mapping[str, FileScope] = field(repr=False)

    def __post_init__(self):
        if not _full_sha(self.sha) or not isinstance(self.files, Mapping):
            raise ValueError("invalid_review_scope")
        copied = dict(self.files)
        if any(not _safe_path(p) or not isinstance(s, FileScope) for p, s in copied.items()):
            raise ValueError("invalid_review_scope")
        object.__setattr__(self, "files", MappingProxyType(copied))


def _contains(ranges, line):
    return any(start <= line <= end for start, end in ranges)


def filter_findings(
    result: ReviewResult, scope: ReviewScope, *, expected_sha: str, response_sha: str,
) -> ReviewResult:
    """Keep only schema-valid findings anchored to the explicitly sent scope.

    A fully filtered nonempty review becomes FILTER_ERROR, never CLEAN. Mixed
    reviews retain accepted findings and sanitized rejections. No location or
    evidence is rewritten; this function does not prove a finding's causal claim.
    """
    if not isinstance(result, ReviewResult):
        return ReviewResult(Status.FILTER_ERROR, "invalid_result")
    if not result.ok:
        return result
    if not isinstance(scope, ReviewScope):
        return ReviewResult(Status.FILTER_ERROR, "invalid_scope")
    if (not _full_sha(expected_sha) or response_sha != expected_sha
            or scope.sha != expected_sha or result.sha not in (None, expected_sha)):
        return ReviewResult(Status.SHA_MISMATCH, "review_revision_mismatch")
    # Recheck the schema at this public boundary, including manually built results.
    validated = validate_review(result._review_json)
    if not validated.ok:
        return validated
    review = validated.review
    accepted, rejected = [], []
    for index, finding in enumerate(review["findings"]):
        path, line = finding["path"], finding["line"]
        supplied = scope.files.get(path) if _safe_path(path) else None
        if supplied is None:
            reason = "path_not_supplied"
        elif line is None:
            reason = "" if supplied.allow_file_level else "file_level_not_allowed"
        elif finding.get("out_of_diff", False):
            reason = "" if _contains(supplied.context_ranges, line) else "context_line_not_supplied"
        else:
            reason = "" if _contains(supplied.diff_ranges, line) else "diff_line_not_supplied"
        if reason:
            rejected.append(Rejection(index, reason))
        else:
            accepted.append(finding)
    if rejected and not accepted:
        return ReviewResult(Status.FILTER_ERROR, "all_findings_rejected", rejected=tuple(rejected))
    review["findings"] = accepted
    return _valid(review, sha=expected_sha, rejected=rejected)


def process_response(
    content: str | None, scope: ReviewScope, *, expected_sha: str, response_sha: str,
    http_status: int = 200, transport_error: bool = False, complete: bool = True,
) -> ReviewResult:
    """Offline response interface; performs no request and no publication.

    response_sha must come from the request snapshot, never from model output.
    The caller must construct scope from the actual bytes/ranges sent. Transport
    and HTTP failures cannot be interpreted as an empty, successful review.
    """
    if transport_error:
        return ReviewResult(Status.TRANSPORT_ERROR, "transport_failed")
    if type(http_status) is not int or http_status != 200:
        code = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        return ReviewResult(Status.HTTP_ERROR, "http_not_successful", http_status=code)
    result = validate_review(content, complete=complete)
    return filter_findings(result, scope, expected_sha=expected_sha, response_sha=response_sha)
