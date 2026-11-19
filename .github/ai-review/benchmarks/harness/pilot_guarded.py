#!/usr/bin/env python3
"""Piloto de revisão de IA: um caso, uma chamada, nenhum dado bruto em disco.

Por padrão, só produz um preflight OFFLINE. A rota paga exige opções
explícitas, variável separada e bloqueia execução em CI. É necessário definir
um limite financeiro REAL na chave do OpenRouter antes de usar --paid.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent.parent
PRODUCTION = BASE.parent / "production"
sys.path.insert(0, str(PRODUCTION))

from core import validate_review
from transport import HTTPS, Problem, _safe_output_usage, decode
from prompt_builder import build_request
from run_bench import (
    load_cases, load_ground_truth, evaluate_review, match_score,
    _line_close, _semantic_match,
)

CASE_IDS = ("r22-typ-001", "r22-ddl-001", "r22-ci-001")
PROFILES = {
    "deepseek-baseline": {
        "model": "deepseek/deepseek-v4.1-flash-1017",
        "max_tokens": 8192,
        "reasoning": {"effort": "none"},
    },
    "deepseek-thinking-16k-8k": {
        "model": "deepseek/deepseek-v4.1-flash-1017",
        # Total shared budget, NOT an independent visible-output limit.
        "max_tokens": 24576,
        # A requested target: providers may map this to effort, not a hard cap.
        "reasoning": {"max_tokens": 16384},
        "max_visible_tokens": 8192,
    },
    "gemini-thinking-2048": {
        "model": "google/gemini-2.5-flash",
        "max_tokens": 8192,
        "reasoning": {"max_tokens": 2048},
    },
    "gemini-thinking-4096-compact": {
        "model": "google/gemini-2.5-flash",
        # Shared ceiling for hidden reasoning and visible JSON.
        "max_tokens": 6144,
        "reasoning": {"max_tokens": 4096},
    },
}
# USD per million tokens, OpenRouter list price consulted 2026-11-19.
# Estimates exclude credit purchase fees, provider price changes and rebates.
GEMINI_INPUT_USD_M = 0.30
GEMINI_OUTPUT_USD_M = 2.50
DEEPSEEK_INPUT_USD_M = 0.036
DEEPSEEK_OUTPUT_USD_M = 0.50
MAX_PILOT_USD = 0.05
MAX_REQUEST_BYTES = 65536

# Fixed, non-provider-controlled diagnostic codes. Never log native_finish_reason,
# raw response, message content, provider error text or unrecognized strings.
INCOMPLETE_FINISH_CODES = {
    "length": "pilot_incomplete_length",
    "content_filter": "pilot_incomplete_content_filter",
    "tool_calls": "pilot_incomplete_tool_calls",
    "error": "pilot_incomplete_provider_error",
}
INCOMPLETE_CODES = frozenset(INCOMPLETE_FINISH_CODES.values()) | {
    "pilot_incomplete_response",
}


def _safe_incomplete_usage(usage):
    """Fail closed: export only small validated numeric counters."""
    if not isinstance(usage, dict):
        return None
    completed = usage.get("completion_tokens")
    if type(completed) is not int or not 0 <= completed <= 1_000_000:
        return None
    result = {"completion_tokens": completed}
    reasoning = usage.get("reasoning_tokens")
    visible = usage.get("visible_tokens")
    if (type(reasoning) is int and type(visible) is int
            and 0 <= reasoning <= completed and visible == completed - reasoning):
        result["reasoning_tokens"] = reasoning
        result["visible_tokens"] = visible
    return result


def build_pilot_request(case, profile):
    if profile not in PROFILES:
        raise ValueError("profile_not_allowed")
    selected = PROFILES[profile]
    request = build_request(
        case["pr_title"], case["pr_body"], case["diff"],
        case.get("context", ""), model=selected["model"],
    )
    # Apply only the reviewed, static allowlist; no caller-provided model IDs.
    request["max_tokens"] = selected["max_tokens"]
    request["reasoning"] = dict(selected["reasoning"])
    if profile in ("gemini-thinking-2048", "gemini-thinking-4096-compact"):
        request["temperature"] = 1.0
        request["response_format"] = {"type": "json_object"}
        request["provider"] = {"require_parameters": True}
    if profile == "deepseek-thinking-16k-8k":
        # Do not alter production. Only this allowlisted pilot profile opts in.
        request["response_format"] = {"type": "json_object"}
        request["provider"] = {"require_parameters": True}
    return request


def encode_request(request):
    raw = json.dumps(request, ensure_ascii=True, allow_nan=False).encode("utf-8")
    if len(raw) > MAX_REQUEST_BYTES:
        raise Problem("pilot_input_too_large")
    return raw


def planned_cost_usd(payload_bytes, profile):
    """Proxy financeiro para planejamento, NÃO limite real de faturamento."""
    if profile in ("gemini-thinking-2048", "gemini-thinking-4096-compact"):
        input_price, output_price = GEMINI_INPUT_USD_M, GEMINI_OUTPUT_USD_M
    elif profile == "deepseek-thinking-16k-8k":
        input_price, output_price = DEEPSEEK_INPUT_USD_M, DEEPSEEK_OUTPUT_USD_M
    else:
        return None  # baseline não autorizado para piloto pago
    return round((payload_bytes * input_price
                  + PROFILES[profile]["max_tokens"] * output_price)
                 / 1_000_000, 6)


def send_single_request(request, key, *, http=None):
    """Uma única POST, sem retry nem persistência de texto bruto."""
    if not isinstance(key, str) or not key or "\r" in key or "\n" in key:
        raise Problem("pilot_authentication_error")
    raw = encode_request(request)
    # Allow the larger DeepSeek response sufficient wall time, without retries.
    large_output = request.get("max_tokens") == 24576
    client = http if http is not None else HTTPS(
        read_timeout=240 if large_output else 120,
        total_timeout=330 if large_output else 180,
        max_response=1024 * 1024,
    )
    response = client.send(
        "openrouter.ai", "POST", "/api/v1/chat/completions",
        {"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        raw,
    )
    if response.status != 200:
        raise Problem("pilot_http_error", response.status)
    envelope = decode(response)
    if not isinstance(envelope, dict) or "error" in envelope:
        raise Problem("pilot_provider_error")
    choices = envelope.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise Problem("pilot_invalid_envelope")
    choice = choices[0]
    if choice.get("finish_reason") != "stop":
        # OpenRouter normalizes finish_reason; only our mapped codes are exposed.
        # Incomplete output may already be billed; NEVER retry automatically.
        code = INCOMPLETE_FINISH_CODES.get(
            choice.get("finish_reason"), "pilot_incomplete_response"
        ) if type(choice.get("finish_reason")) is str else "pilot_incomplete_response"
        raise Problem(code, usage=_safe_output_usage(envelope))
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant" or message.get("tool_calls"):
        raise Problem("pilot_invalid_envelope")
    content = message.get("content")
    if not isinstance(content, str):
        raise Problem("pilot_invalid_envelope")
    usage = _safe_output_usage(envelope)
    if (usage is not None and
            usage["completion_tokens"] > request["max_tokens"]):
        raise Problem("pilot_completion_budget_exceeded", usage=usage)
    reasoning = request.get("reasoning")
    reasoning_limit = reasoning.get("max_tokens") if isinstance(reasoning, dict) else None
    if (type(reasoning_limit) is int and usage is not None
            and usage.get("reasoning_tokens", 0) > reasoning_limit):
        raise Problem("pilot_reasoning_budget_exceeded", usage=usage)
    if (request.get("model") == "deepseek/deepseek-v4.1-flash-1017"
            and request.get("max_tokens") == 24576
            and usage is not None and usage.get("visible_tokens", 0) > 8192):
        # Post-response check only; not a provider-side independent cap.
        raise Problem("pilot_visible_budget_exceeded", usage=usage)
    validated = validate_review(content)
    # Do not expose, save, copy or print the raw model content.
    if not validated.ok:
        raise Problem("pilot_invalid_review", usage=usage)
    return validated.review, usage


def safe_finding_audit(review, gt):
    """Bounded, structured diagnostics only. NEVER include provider text.

    NOT a proof of semantic correctness and NOT a replacement for human audit.
    Paths, titles, bodies, evidence, symbols and raw model text are excluded.
    Severity/category/confidence are schema-constrained enums.
    """
    findings = review.get("findings", [])
    expected = gt.get("expected", {}) if gt else {}
    expected_find = expected.get("find") is True
    diagnostics = []
    for index, finding in enumerate(findings[:25]):
        location = expected.get("location", {}) if expected_find else {}
        line = finding.get("line")
        text = " ".join(
            str(finding.get(key) or "") for key in ("title", "body", "evidence")
        ).casefold()
        cause_signals = None
        if gt and gt.get("id") == "r22-typ-001":
            # Coarse heuristic cues, not quotes or a verdict on correctness.
            cause_signals = {
                "type_error": bool(re.search(r"\btypeerror\b", text)),
                "unhashable_input": bool(re.search(
                    r"unhash|n[aã]o.hash|lista|dicion|\blist\b|\bdict\b|\[\]|\{\}",
                    text,
                )),
                "membership_site": (
                    "_operacoes" in text or "operacao not in" in text
                    or "frozenset" in text
                ),
            }
        diagnostics.append({
            "index": index,
            "severity": finding["severity"],
            "category": finding["category"],
            "confidence": finding["confidence"],
            "path_matches_expected": (
                finding.get("path") == expected.get("path")
                if expected_find else None
            ),
            "line_supplied": line is not None,
            "line_near_expected": (
                _line_close(line, expected["line"])
                if expected_find and line is not None
                and expected.get("line") is not None else None
            ),
            "semantic_match": (
                _semantic_match(finding, location)
                if expected_find else None
            ),
            "heuristic_match": (
                match_score(finding, gt) >= 0 if expected_find else False
            ),
            "causal_cues": cause_signals,
        })
    return {
        "version": 1,
        "expected_find": expected_find,
        "finding_count": len(findings),
        "findings": diagnostics,
        "manual_audit_required": True,
    }


def run_one(profile, case_id, key, *, http=None):
    """Returns only numeric grading metrics and fixed model/case metadata."""
    if case_id not in CASE_IDS or profile not in PROFILES:
        raise ValueError("pilot_not_allowlisted")
    case = load_cases(case_id=case_id)[0]
    request = build_pilot_request(case, profile)
    result, usage = send_single_request(request, key, http=http)
    gt = load_ground_truth(case_id)
    match = evaluate_review(result, gt)
    audit = safe_finding_audit(result, gt)
    return {
        "mode": "paid_result",
        "profile": profile,
        "case_id": case_id,
        "model": PROFILES[profile]["model"],
        "status": "review_valid",
        "tp": match["tp"],
        "fp": match["fp"],
        "fn": match["fn"],
        "audit": audit,
        "usage": usage,
        # If the provider omits counters, compliance remains UNVERIFIED.
        "reasoning_budget_verified": bool(
            usage is not None and "reasoning_tokens" in usage
        ),
        "visible_budget_verified": bool(
            usage is not None and "visible_tokens" in usage
        ) if profile == "deepseek-thinking-16k-8k" else None,
        "calls_attempted": 1,
        "review_published": False,
    }


def command(args):
    case = load_cases(case_id=args.case)
    if args.case not in CASE_IDS or len(case) != 1:
        raise Problem("pilot_case_not_allowed")
    if args.profile not in PROFILES:
        raise Problem("pilot_profile_not_allowed")
    request = build_pilot_request(case[0], args.profile)
    size = len(encode_request(request))
    estimate = planned_cost_usd(size, args.profile)
    if not args.paid:
        return {
            "mode": "dry_run", "case_id": args.case,
            "profile": args.profile, "model": PROFILES[args.profile]["model"],
            "request_bytes": size, "max_completion_tokens": request["max_tokens"],
            "reasoning": request["reasoning"], "planning_estimate_usd": estimate,
            "calls_attempted": 0, "note": "estimate_is_not_a_billing_cap",
        }
    if os.environ.get("CI", "").lower() == "true":
        trusted_manual_action = all((
            os.environ.get("SEAGBH_AI_PILOT_TRUSTED_ACTIONS") == "true",
            os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch",
            os.environ.get("GITHUB_REPOSITORY") == "gavvalverde/SEAGBH-source",
            os.environ.get("GITHUB_REF") == "refs/heads/main",
            os.environ.get("GITHUB_ACTOR") == "gavvalverde",
            os.environ.get("GITHUB_TRIGGERING_ACTOR") == "gavvalverde",
            os.environ.get("GITHUB_RUN_ATTEMPT") == "1",
        ))
        if not trusted_manual_action:
            raise Problem("pilot_paid_blocked_in_ci")
    if args.profile not in ("gemini-thinking-2048", "gemini-thinking-4096-compact",
                            "deepseek-thinking-16k-8k"):
        raise Problem("pilot_price_not_verified")
    if (not args.confirm_single_paid_call
            or os.environ.get("SEAGBH_AI_PILOT_PAID_ENABLED") != "owner_confirmed"):
        raise Problem("pilot_paid_not_authorized")
    if (type(args.max_usd) is not float or not 0 < args.max_usd <= MAX_PILOT_USD
            or estimate is None or estimate > args.max_usd):
        raise Problem("pilot_budget_not_approved")
    # Key-level spending limit must ALSO be configured externally at OpenRouter.
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise Problem("pilot_authentication_error")
    return run_one(args.profile, args.case, key)


def main(argv=None):
    parser = argparse.ArgumentParser(description="SEAGBH: piloto A/B seguro, dry-run por padrão")
    parser.add_argument("--case", default=CASE_IDS[0], choices=CASE_IDS)
    parser.add_argument("--profile", default="gemini-thinking-2048", choices=tuple(PROFILES))
    parser.add_argument("--paid", action="store_true")
    parser.add_argument("--confirm-single-paid-call", action="store_true")
    parser.add_argument("--max-usd", type=float)
    args = parser.parse_args(argv)
    try:
        output = command(args)
        print(json.dumps(output, sort_keys=True, ensure_ascii=True))
        return 0
    except Problem as error:
        # Fixed codes and validated counts only. Never print provider strings,
        # raw messages or keys. A previous incomplete response cannot be recovered.
        output = {"mode": "blocked_or_failed", "code": error.code,
                  "calls_may_be_billed": bool(args.paid)}
        if error.code in INCOMPLETE_CODES:
            output["reported_usage"] = _safe_incomplete_usage(error.usage)
            # Trusted static profile limits for reading token consumption.
            selected = PROFILES.get(args.profile)
            if selected is not None:
                output["requested_max_completion_tokens"] = selected["max_tokens"]
                budget = selected["reasoning"].get("max_tokens")
                if type(budget) is int:
                    output["requested_reasoning_tokens"] = budget
        print(json.dumps(output, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
