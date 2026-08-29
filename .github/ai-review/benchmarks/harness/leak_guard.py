#!/usr/bin/env python3
"""
leak_guard.py — Valida que cases/ NÃO contém ground truth.

Verifica:
A. Chaves proibidas: expected, expected_*, ground_truth, severity_expected, etc.
B. Substrings suspeitas que podem vazar o ground truth.
C..exit code != 0 em qualquer suspeita.
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CASES_DIR = BASE / "cases"

FORBIDDEN_KEYS = {
    "expected", "expected_find", "expected_severity", "expected_category",
    "expected_path", "expected_line", "expected_behavior",
    "ground_truth", "groundtruth",
    "severity_expected", "category_expected", "path_expected",
    "behavior_expected", "confidence_expected",
    "delta",  # ground truth documentation
}

FORBIDDEN_PATTERNS = [
    "ground truth",
    "ground_truth",
    "expected_find",
    "expected severity",
    "expected category",
]


def check_leak(data, case_id):
    errors = []
    for key in data:
        k = key.lower().strip()
        if k in FORBIDDEN_KEYS or any(k.startswith(fk) for fk in ("expected", "ground_truth", "severity_exp", "category_exp")):
            errors.append(f"  FORBIDDEN KEY '{key}' in {case_id}.json")
    for key, val in data.items():
        if isinstance(val, str):
            vl = val.lower()
            for pat in FORBIDDEN_PATTERNS:
                if pat in vl:
                    errors.append(f"  SUSPICIOUS substring '{pat}' in {case_id}.json key '{key}'")
        elif isinstance(val, dict):
            for sub_key in val:
                sk = sub_key.lower().strip()
                if sk in FORBIDDEN_KEYS or sk.startswith("expected"):
                    errors.append(f"  FORBIDDEN KEY '{key}.{sub_key}' in {case_id}.json")
    return errors


def main():
    all_errors = []
    files = sorted(CASES_DIR.glob("*.json"))
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        all_errors.extend(check_leak(data, f.stem))
    if all_errors:
        print("LEAK GUARD FAILED:")
        for e in all_errors:
            print(e)
        sys.exit(1)
    print(f"LEAK GUARD OK: {len(files)} cases validated — no ground truth leakage detected.")
    sys.exit(0)


if __name__ == "__main__":
    main()
