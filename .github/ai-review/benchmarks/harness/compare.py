#!/usr/bin/env python3
"""
compare.py — Compara dois runs de benchmark.

Uso:
  python .github/ai-review/benchmarks/harness/compare.py runA.jsonl runB.jsonl
"""
import json
import sys
from pathlib import Path

from run_bench import LEGACY_RESULT_VERSION, summarize_versioned_run


def load_run(path):
    results = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            results.append(json.loads(line))
    return results


def _version(results):
    versions = {row.get("benchmark_version", "1.0.0") for row in results}
    if not versions:
        raise ValueError("relatório vazio, sem versão verificável")
    if len(versions) != 1 or not versions <= {"1.0.0", LEGACY_RESULT_VERSION}:
        raise ValueError("versões de relatório misturadas ou não suportadas")
    return versions.pop()


def summarize(results):
    if _version(results) == LEGACY_RESULT_VERSION:
        summary = summarize_versioned_run(results)
        aliases = {"cases": "cases_run", "severity_acc": "severity_accuracy",
                   "category_acc": "category_accuracy", "location_acc": "location_accuracy",
                   "injection": "prompt_injection_resistance", "input_bytes": "total_input_bytes",
                   "output_tokens": "total_output_tokens", "duration": "total_duration"}
        return {**summary, **{key: summary.get(value) for key, value in aliases.items()}}
    n = len(results)
    tp = sum(r["match"]["tp"] for r in results)
    fp = sum(r["match"]["fp"] for r in results)
    fn = sum(r["match"]["fn"] for r in results)
    p = tp / max(tp + fp, 1)
    r = tp / max(tp + fn, 1)
    f1 = 2 * p * r / max(p + r, 1e-9)
    sev = sum(r["match"]["severity_match"] for r in results) / max(tp, 1)
    cat = sum(r["match"]["category_match"] for r in results) / max(tp, 1)
    loc = sum(r["match"]["location_accuracy"] for r in results) / max(tp, 1)
    inj_cases = [r for r in results if r.get("type") == "injection"]
    inj = sum(1 for r in inj_cases if r["match"].get("injection_handled")) / max(len(inj_cases), 1)
    total_in = sum(r.get("input_bytes", 0) for r in results)
    total_out = sum(r.get("output_tokens") or 0 for r in results)
    total_el = sum(r.get("elapsed", 0) for r in results)
    return {"cases": n, "TP": tp, "FP": fp, "FN": fn,
            "precision": round(p, 3), "recall": round(r, 3), "F1": round(f1, 3),
            "severity_acc": round(sev, 3), "category_acc": round(cat, 3),
            "location_acc": round(loc, 3), "injection": round(inj, 3),
            "input_bytes": total_in, "output_tokens": total_out,
            "duration": round(total_el, 2)}


def compare(path_a, path_b):
    run_a = load_run(path_a)
    run_b = load_run(path_b)
    if _version(run_a) != _version(run_b):
        raise ValueError("comparação entre versões 1 e 2 não é válida")
    sa = summarize(run_a)
    sb = summarize(run_b)

    print(f"{'Metric':<25} {'A':>10} {'B':>10} {'Delta':>10}")
    print("-" * 57)
    coverage = ("cases_evaluable", "cases_not_evaluable") if _version(run_a) == LEGACY_RESULT_VERSION else ()
    for key in ("cases", *coverage, "TP", "FP", "FN", "precision", "recall", "F1",
                "severity_acc", "category_acc", "location_acc", "injection",
                "input_bytes", "output_tokens", "duration"):
        va, vb = sa.get(key), sb.get(key)
        delta = f"{vb - va:+g}" if va is not None and vb is not None else "N/A"
        print(f"{key:<25} {str(va) if va is not None else 'N/A':>10} "
              f"{str(vb) if vb is not None else 'N/A':>10} {delta:>10}")

    by_id_a = {r["case_id"]: r for r in run_a}
    by_id_b = {r["case_id"]: r for r in run_b}
    all_ids = sorted(set(list(by_id_a.keys()) + list(by_id_b.keys())))
    if all_ids:
        print(f"\n{'Case':<12} {'A-TP':>4} {'A-FP':>4} {'B-TP':>4} {'B-FP':>4} {'Delta':>6}")
        print("-" * 40)
        for cid in all_ids:
            a = by_id_a.get(cid, {}).get("match")
            b = by_id_b.get(cid, {}).get("match")
            delta = f"{(b['tp'] - a['tp']) - (b['fp'] - a['fp']):+d}" if a and b else "N/A"
            cells = [row[key] if row else "N/A" for row in (a, b) for key in ("tp", "fp")]
            print(f"{cid:<12} " + " ".join(f"{v!s:>4}" for v in cells) + f" {delta:>6}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: compare.py <run_a.jsonl> <run_b.jsonl>")
        sys.exit(1)
    try:
        compare(sys.argv[1], sys.argv[2])
    except ValueError as exc:
        sys.exit(str(exc))

