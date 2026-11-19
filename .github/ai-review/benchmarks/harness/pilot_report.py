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

from pilot_guarded import CASE_IDS, PROFILES
from run_bench import load_ground_truth

PAID_PROFILES = frozenset({
    "deepseek-thinking-16k-8k", "gemini-thinking-2048",
    "gemini-thinking-4096-compact",
})
FORBIDDEN_FIELDS = frozenset({
    "review", "raw_response", "content", "messages", "choices",
    "prompt", "completion", "response", "body", "title", "evidence",
})
MAX_BYTES = 128 * 1024


def _counter(value, max_value=1_000_000):
    return type(value) is int and 0 <= value <= max_value


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
        tp, fp, fn = item.get("tp"), item.get("fp"), item.get("fn")
        if not all(_counter(v, 25) for v in (tp, fp, fn)):
            raise ValueError("invalid_counters")
        gt = load_ground_truth(cid)
        if not isinstance(gt, dict):
            raise ValueError("ground_truth_missing")
        expected = gt["expected"]["find"] is True
        if (expected and tp + fn != 1) or (not expected and (tp or fn)):
            raise ValueError("inconsistent_ground_truth_metrics")
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
        "manual_semantic_audit_required": True,
        "is_live_model_accuracy": bool(records),
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
