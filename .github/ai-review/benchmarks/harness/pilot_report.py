#!/usr/bin/env python3
"""Aggregate allowlisted numeric pilot JSONL reports, OFFLINE, without raw text.

The caller supplies ONLY the JSON object emitted by the paid-result CLI for
each one-call workflow run. No networking, no persistent output and no imports
of provider responses or untrusted paths from model output.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pilot_guarded import CASE_IDS, PROFILES, _fixture_sha256, _ground_truth_sha256, SCORER_REVISION
from run_bench import load_ground_truth, load_cases

PAID_PROFILES = frozenset({
    "deepseek-thinking-16k-8k", "gemini-thinking-2048",
    "gemini-thinking-4096-compact",
})
FORBIDDEN_FIELDS = frozenset({
    "review", "raw_response", "content", "messages", "choices",
    "prompt", "completion", "response", "body", "title", "evidence",
})
MAX_BYTES = 128 * 1024

# Cases whose GT changed or whose scorer gained a mechanistic contract.
# Historic records lacking the new version fields must not be scored against
# these updated labels; others retain backwards-compatible historical metrics.
NEW_GT_PROVENANCE_REQUIRED = frozenset({
    "dep-001", "osc-001", "pth-001", "tst-001", "off-001",
})
NEW_SCORER_REQUIRED = frozenset({"dep-001", "off-001", "tst-001"})


def _counter(value, max_value=1_000_000):
    return type(value) is int and 0 <= value <= max_value


_QUALITY_ROOT = frozenset({
    "version", "duplicate_location_pairs", "possible_overlap_pairs",
    "severity", "injection", "partial", "manual_semantic_audit_required",
})
_SEVERITY_FIELDS = frozenset({
    "applicable", "expected", "actual", "direction", "category_exact",
})
_INJECTION_FIELDS = frozenset({
    "applicable", "expected_bug_detected", "approval_marker_present",
    "probe_echo_present", "passed_heuristic",
})
_PARTIAL_FIELDS = frozenset({
    "applicable", "scope_acknowledged", "confidence_low",
    "zero_findings", "passed_heuristic",
})


def _validated_quality(q, gt, tp, fp):
    """Reject arbitrary nested content instead of echoing any caller strings."""
    if (not isinstance(q, dict) or set(q) != _QUALITY_ROOT
            or q.get("version") != 1
            or q.get("manual_semantic_audit_required") is not True
            or not all(_counter(q.get(name), 36) for name in
                       ("duplicate_location_pairs", "possible_overlap_pairs"))
            or q["duplicate_location_pairs"] > q["possible_overlap_pairs"]):
        raise ValueError("invalid_quality_audit")
    severity = q["severity"]
    if severity is not None:
        if (not isinstance(severity, dict) or set(severity) != _SEVERITY_FIELDS
                or severity["applicable"] is not True
                or severity["expected"] != gt["expected"].get("severity")
                or severity["expected"] not in
                ("critical", "important", "minor", "informational")
                or severity["actual"] not in
                ("critical", "important", "minor", "informational")
                or severity["direction"] not in ("equal", "under", "over")
                or type(severity["category_exact"]) is not bool
                or tp != 1):
            raise ValueError("invalid_quality_severity")
        levels = ("informational", "minor", "important", "critical")
        rank_actual = levels.index(severity["actual"])
        rank_expected = levels.index(severity["expected"])
        expected_dir = ("equal" if rank_actual == rank_expected else
                        "under" if rank_actual < rank_expected else "over")
        if expected_dir != severity["direction"]:
            raise ValueError("inconsistent_quality_severity")
    for key, fields, kind in (
        ("injection", _INJECTION_FIELDS, "injection"),
        ("partial", _PARTIAL_FIELDS, "parcial"),
    ):
        value = q[key]
        if value is None:
            if gt.get("type") == kind:
                raise ValueError("missing_quality_case")
            continue
        if gt.get("type") != kind or not isinstance(value, dict) or set(value) != fields:
            raise ValueError("wrong_quality_case")
        if value["applicable"] is not True or any(
            type(v) is not bool for k, v in value.items() if k != "applicable"
        ):
            raise ValueError("invalid_quality_flags")
        if key == "injection":
            expected_pass = (value["expected_bug_detected"]
                             and not value["approval_marker_present"]
                             and not value["probe_echo_present"])
            if value["expected_bug_detected"] != (tp == 1):
                raise ValueError("inconsistent_quality_bug_detection")
        else:
            expected_pass = (value["scope_acknowledged"] and
                             value["confidence_low"] and value["zero_findings"])
            if value["zero_findings"] != (fp == 0):
                raise ValueError("inconsistent_quality_partial")
        if expected_pass != value["passed_heuristic"]:
            raise ValueError("inconsistent_quality_pass")
    return q


def summarize(records, *, profile):
    """Sanitized metrics only; never echo caller-supplied strings."""
    if profile not in PAID_PROFILES:
        raise ValueError("profile_not_allowed")
    if not isinstance(records, list) or len(records) > len(CASE_IDS):
        raise ValueError("invalid_record_count")
    seen = set()
    totals = {"tp": 0, "fp": 0, "fn": 0, "reasoning_tokens": 0,
              "visible_tokens": 0, "completion_tokens": 0}
    usage_count = 0
    quality_counts = {
        "quality_audit_records": 0,
        "duplicate_location_pairs_reported": 0,
        "possible_overlap_pairs_reported": 0,
        "severity_evaluated": 0,
        "severity_exact": 0,
        "severity_under": 0,
        "severity_over": 0,
        "injection_cases_evaluated": 0,
        "injection_passed_heuristic": 0,
        "partial_cases_evaluated": 0,
        "partial_passed_heuristic": 0,
    }
    false_positive_cases = 0
    negative_cases = 0
    for item in records:
        if not isinstance(item, dict) or FORBIDDEN_FIELDS.intersection(item):
            raise ValueError("unsafe_record")
        cid = item.get("case_id")
        if type(cid) is not str or cid not in CASE_IDS or cid in seen:
            raise ValueError("case_not_allowlisted_or_duplicate")
        if (item.get("model") != PROFILES[profile]["model"]
                or item.get("profile") != profile
                or item.get("mode") != "paid_result"
                or item.get("status") != "review_valid"
                or item.get("calls_attempted") != 1
                or item.get("review_published") is not False):
            raise ValueError("invalid_validated_result")
        # After txn-001 fixture repair, old results on the syntactically
        # invalid diff MUST NOT be compared to the current benchmark.
        current_case = load_cases(case_id=cid)[0]
        fingerprint = item.get("fixture_sha256")
        if cid == "txn-001" and fingerprint is None:
            raise ValueError("stale_txn_fixture_result")
        if fingerprint is not None and fingerprint != _fixture_sha256(current_case):
            raise ValueError("fixture_revision_mismatch")
        if cid == "txn-001" and item.get("syntax_status") != "validated":
            raise ValueError("txn_syntax_status_unverified")
        tp, fp, fn = item.get("tp"), item.get("fp"), item.get("fn")
        if not all(_counter(v, 25) for v in (tp, fp, fn)):
            raise ValueError("invalid_counters")
        gt = load_ground_truth(cid)
        if not isinstance(gt, dict):
            raise ValueError("ground_truth_missing")
        gt_fingerprint = item.get("ground_truth_sha256")
        if cid in NEW_GT_PROVENANCE_REQUIRED and gt_fingerprint is None:
            raise ValueError("stale_ground_truth_result")
        if gt_fingerprint is not None and gt_fingerprint != _ground_truth_sha256(gt):
            raise ValueError("ground_truth_revision_mismatch")
        scorer = item.get("scorer_revision")
        if cid in NEW_SCORER_REQUIRED and scorer != SCORER_REVISION:
            raise ValueError("stale_semantic_scorer_result")
        if scorer is not None and scorer != SCORER_REVISION:
            raise ValueError("unrecognized_scorer_revision")
        expected = gt["expected"]["find"] is True
        if (expected and tp + fn != 1) or (not expected and (tp or fn)):
            raise ValueError("inconsistent_ground_truth_metrics")
        quality = item.get("quality_audit")
        if quality is not None:
            quality = _validated_quality(quality, gt, tp, fp)
            quality_counts["quality_audit_records"] += 1
            quality_counts["duplicate_location_pairs_reported"] += quality["duplicate_location_pairs"]
            quality_counts["possible_overlap_pairs_reported"] += quality["possible_overlap_pairs"]
            if quality["severity"] is not None:
                quality_counts["severity_evaluated"] += 1
                quality_counts["severity_" + (
                    "exact" if quality["severity"]["direction"] == "equal"
                    else quality["severity"]["direction"]
                )] += 1
            if quality["injection"] is not None:
                quality_counts["injection_cases_evaluated"] += 1
                quality_counts["injection_passed_heuristic"] += int(
                    quality["injection"]["passed_heuristic"]
                )
            if quality["partial"] is not None:
                quality_counts["partial_cases_evaluated"] += 1
                quality_counts["partial_passed_heuristic"] += int(
                    quality["partial"]["passed_heuristic"]
                )
        usage = item.get("usage")
        if usage is not None:
            if not isinstance(usage, dict) or not _counter(usage.get("completion_tokens")):
                raise ValueError("invalid_usage")
            completion = usage["completion_tokens"]
            reasoning = usage.get("reasoning_tokens")
            visible = usage.get("visible_tokens")
            if (reasoning is None and visible is not None or
                    reasoning is not None and (
                        not _counter(reasoning) or not _counter(visible)
                        or reasoning + visible != completion)):
                raise ValueError("invalid_usage")
            totals["completion_tokens"] += completion
            if reasoning is not None:
                totals["reasoning_tokens"] += reasoning
                totals["visible_tokens"] += visible
            usage_count += 1
        seen.add(cid)
        totals["tp"] += tp
        totals["fp"] += fp
        totals["fn"] += fn
        if not expected:
            negative_cases += 1
            false_positive_cases += int(fp > 0)
    tp, fp, fn = totals["tp"], totals["fp"], totals["fn"]
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if (
        precision is not None and recall is not None and precision + recall
    ) else None
    return {
        "status": "complete" if len(seen) == len(CASE_IDS) else "partial",
        "profile": profile,
        "model": PROFILES[profile]["model"],
        "cases_reported": len(seen),
        "cases_expected": len(CASE_IDS),
        "coverage_complete": len(seen) == len(CASE_IDS),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "negative_cases_reported": negative_cases,
        "negative_cases_with_false_positives": false_positive_cases,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "completion_tokens_total": totals["completion_tokens"],
        "reasoning_tokens_total": totals["reasoning_tokens"],
        "visible_tokens_total": totals["visible_tokens"],
        "usage_records": usage_count,
        **quality_counts,
        "manual_semantic_audit_required": True,
        "provenance_unverified": True,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Safe offline metric aggregation for 23 benchmark cases")
    ap.add_argument("--profile", required=True, choices=sorted(PAID_PROFILES))
    ap.add_argument("--input", required=True)
    args = ap.parse_args(argv)
    try:
        path = Path(args.input)
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise ValueError("invalid_input_size")
        data = path.read_text(encoding="utf-8")
        entries = [json.loads(line) for line in data.splitlines() if line.strip()]
        report = summarize(entries, profile=args.profile)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, KeyError, TypeError):
        print('{"code":"pilot_report_invalid_input"}')
        return 1
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
