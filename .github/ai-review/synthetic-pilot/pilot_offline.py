"""Pinned synthetic v2 pilot. No real transport, credentials or publication API."""
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from difflib import unified_diff
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "production"))
sys.path.insert(0, str(ROOT / "benchmarks" / "harness"))

import contract_v2 as contract
from inputs.cases import CASES
import planning_v2 as planning
from production_v2 import _adjudication_chunk
from run_bench import summarize_versioned_run
import runner
from transport import Problem

IMPLEMENTATION = "fe508a6915ae4c7f6aa7b4c897cebc9b777c5f7e"
MODEL = "moonshotai/kimi-k2.6"
MAX_CALLS = 8
DUMMY_BASE, DUMMY_HEAD = "a" * 40, "b" * 40


def verify_pins():
    lock = json.loads((ROOT / "synthetic-pilot/contract-lock.json").read_text(encoding="utf-8"))
    if lock["implementation_sha"] != IMPLEMENTATION or lock["model"] != MODEL:
        raise ValueError("experiment lock identity changed")
    for path, digest in lock["sha256"].items():
        if hashlib.sha256((ROOT / path).read_bytes().replace(b"\r\n", b"\n")).hexdigest() != digest:
            raise ValueError(f"experiment contract drift: {path}")
    if runner.PRODUCTION_MODEL != MODEL or contract.PROVIDER_ENDPOINT != "chutes/int4":
        raise ValueError("experiment model or endpoint changed")


@contextmanager
def no_network():
    def deny(*args, **kwargs):
        raise AssertionError("Real network forbidden in K68-E")

    with patch("socket.socket", side_effect=deny), patch("socket.create_connection", side_effect=deny), patch(
        "socket.getaddrinfo", side_effect=deny
    ):
        yield


@dataclass(frozen=True)
class Plan:
    case_id: str
    pinned: object
    review_plan: object
    discovery: tuple
    full_diff: object


def make_plan(case):
    if case not in CASES:
        raise ValueError("only fixed invented cases may be used")
    repo = {"full_name": runner.REPOSITORY}
    pr = {"number": 1, "state": "open", "draft": False,
          "title": f"Synthetic change {case.id}", "body": "Invented offline fixture",
          "changed_files": 1, "head": {"sha": DUMMY_HEAD, "repo": repo},
          "base": {"sha": DUMMY_BASE, "ref": "main", "repo": repo}}
    diff = list(unified_diff(case.before.splitlines(), case.after.splitlines(), lineterm=""))[2:]
    files = [{"filename": case.path, "status": "modified", "patch": "\n".join(diff),
              "additions": sum(x.startswith("+") for x in diff),
              "deletions": sum(x.startswith("-") for x in diff)}]

    class SyntheticComparison:
        # Deliberately no methods for authentication, publication or reviews.
        def pr(self, number):
            if number != 1:
                raise AssertionError("unexpected synthetic PR")
            return pr

        def compare(self, pinned):
            return {"base_commit": {"sha": DUMMY_BASE}, "files": files}

    pinned = runner.event_snapshot({"number": 1, "action": "opened",
                                    "repository": dict(repo, default_branch="main"),
                                    "pull_request": pr}, "true")
    plan = planning.build_plan(SyntheticComparison(), pinned)
    full_diff = _adjudication_chunk(plan)
    contract.refutation_preflight(pinned, plan, full_diff)
    payloads = tuple(contract.make_payload(pinned, plan, chunk) for chunk in plan.chunks)
    return Plan(case.id, pinned, plan, payloads, full_diff)


def preflight(plans, *, ceiling=None):
    if len(plans) != len(CASES) or {p.case_id for p in plans} != {c.id for c in CASES}:
        raise ValueError("the pilot requires four preregistered cases")
    calls = sum(len(item.discovery) + 1 for item in plans)
    if calls > MAX_CALLS:
        raise ValueError("pilot call cap exceeded")
    # Per case, production's own gate reserves a full refutation, even if unused.
    for item in plans:
        contract.worst_case_usd(item.discovery)
    # Recompute the same conservative bound with exact decimal arithmetic; no
    # float rounding may lower the independently enforced batch ceiling.
    inp = Decimal(str(runner.PRODUCTION_INPUT_USD_M_CEILING))
    out = Decimal(str(contract.OUTPUT_USD_M_CEILING))
    exact = [(inp * (sum(contract.wire_bytes(p) for p in item.discovery) + contract.MAX_REFUTATION_BYTES)
              + out * contract.MAX_COMPLETION_TOKENS * (len(item.discovery) + 1)) / 1_000_000
             for item in plans]
    if any(amount > Decimal(str(contract.MAX_TOTAL_USD)) for amount in exact):
        raise ValueError("production review cap exceeded")
    total = sum(exact)
    if ceiling is not None and (not isinstance(ceiling, Decimal) or
                                not ceiling.is_finite() or ceiling <= 0 or total > ceiling):
        raise ValueError("pilot budget exceeded or invalid")
    return {"calls_max": calls, "usd_max": str(total), "per_case_usd": {
        p.case_id: str(bound) for p, bound in zip(plans, exact)
    }, "budget_supplied": ceiling is not None, "offline_only": True}


@dataclass(frozen=True)
class OfflineReply:
    http_status: int
    body: str


def _review(plan, fake_send):
    """Use only an injected offline mock. Store synthetic evidence in memory."""
    calls = 0
    chunks = 0
    original = []

    def call(payload):
        nonlocal calls
        calls += 1
        reply = fake_send(payload)
        if (not isinstance(reply, OfflineReply) or type(reply.http_status) is not int
                or reply.http_status != 200):
            raise Problem("invalid_http_response")
        return reply.body

    def row(status, reason, evidence=(), retained=(), verdicts=()):
        return {"case_id": plan.case_id, "origin": "offline_mock", "status": status,
                "reason": reason, "calls": calls, "manifest": plan.review_plan.digest,
                "coverage": dict(plan.review_plan.coverage, chunks_completed=chunks),
                "candidates": evidence, "retained": retained, "verdicts": verdicts}

    try:
        for payload, chunk in zip(plan.discovery, plan.review_plan.chunks):
            answer = contract.validate_reply(call(payload), frozenset(a[0] for a in chunk.anchors))
            contract.require_semantic_completion(answer)
            original.extend(answer["findings"])
            chunks += 1
            if len(original) > contract.MAX_CANDIDATES:
                raise Problem("v2_candidate_limit")
        immutable = [{"candidate_id": contract.candidate_id(item, i), "finding": item}
                     for i, item in enumerate(original)]
        ids = frozenset(item["candidate_id"] for item in immutable)
        if len(ids) != len(immutable):
            raise Problem("v2_candidate_collision")
        retained, verdicts = (), ()
        if immutable:
            payload = contract.make_payload(plan.pinned, plan.review_plan, plan.full_diff,
                                            candidates=immutable)
            contract.worst_case_usd(plan.discovery + (payload,), reserve_refutation=False)
            adjudication = contract.validate_refutation_reply(call(payload), ids)
            verdicts = tuple(adjudication["verdicts"])
            retained = tuple(v["candidate_id"] for v in verdicts if v["decision"] == "retain")
        evidence = tuple({
            "candidate_id": item["candidate_id"],
            "path": plan.review_plan.anchors[item["finding"]["anchor_id"]][1],
            "line": plan.review_plan.anchors[item["finding"]["anchor_id"]][2],
            **{key: item["finding"][key] for key in ("trigger", "mechanism", "impact", "counterevidence")},
        } for item in immutable)
        if chunks != len(plan.discovery):
            raise Problem("incomplete_coverage")
        return row("awaiting_causal_labels", None, evidence, retained, verdicts)
    except TimeoutError:
        return row("operational_failure", "timeout")
    except Problem as error:
        abstentions = {"v2_model_declared_incomplete", "v2_model_low_confidence", "v2_refutation_uncertain"}
        return row("abstention" if error.code in abstentions else "operational_failure",
                   error.code)


def run_offline(mocks, *, ceiling=None):
    """Execute oracles and financial preflight before any mock call."""
    with no_network():
        verify_pins()
        from oracles.expectations import prove
        for case in CASES:
            prove(case)
        plans = [make_plan(case) for case in CASES]
        if set(mocks) != {p.case_id for p in plans}:
            raise ValueError("mock inventory mismatch")
        preflight(plans, ceiling=ceiling)
        return [_review(plan, mocks[plan.case_id]) for plan in plans]


def grade(case, report, causal_matches):
    """Human-labelled causal equivalence per candidate; never grade invalid/uncertain."""
    from oracles.expectations import prove
    positive = prove(case)
    if report["case_id"] != case.id:
        raise ValueError("case mismatch")
    expected = {item["candidate_id"] for item in report["candidates"]}
    if (report["status"] != "awaiting_causal_labels" or not isinstance(causal_matches, dict)
            or set(causal_matches) != expected or any(type(v) is not bool for v in causal_matches.values())):
        return {"status": "not_evaluable", "expected_find": positive, "match": None, "discovery_match": None}

    def counts(ids):
        tp = int(positive and any(causal_matches[cid] for cid in ids))
        return {"tp": tp, "fp": len(ids) - tp, "fn": int(positive) - tp}

    final = counts(report["retained"])
    return {"status": "evaluated", "expected_find": positive, "match": final,
            "semantic_error": bool(final["fp"] or final["fn"]),
            "discovery_match": counts(expected)}


def summarize(grades, *, stage="match"):
    # Reuse the corrected legacy denominator semantics, not its lexical
    # heuristic matching. These rows contain no reviews or raw provider output.
    rows = []
    for row in grades:
        score = row[stage]
        rows.append(dict(row, input_bytes=0, output_tokens=None, elapsed=0,
                         match=dict(score, severity_match=0, category_match=0,
                                    location_accuracy=0, invalid_findings=0) if score is not None else None))
    result = summarize_versioned_run(rows)
    return {key: result[key] for key in ("cases_run", "cases_evaluable", "TP", "FP", "FN", "precision", "recall", "F1")}


def main():
    with no_network():
        verify_pins()
        from oracles.expectations import prove
        for case in CASES:
            prove(case)
        print(json.dumps(preflight([make_plan(case) for case in CASES]), sort_keys=True))


if __name__ == "__main__":
    main()
