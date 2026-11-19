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
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent.parent
PRODUCTION = BASE.parent / "production"
sys.path.insert(0, str(PRODUCTION))

from core import validate_review
from transport import HTTPS, Problem, _safe_output_usage, decode
from prompt_builder import build_request
from run_bench import load_cases, load_ground_truth, evaluate_review

CASE_IDS = ("r22-typ-001", "r22-ddl-001", "r22-ci-001")
PROFILES = {
    "deepseek-baseline": {
        "model": "deepseek/deepseek-v4.1-flash-1017",
        "max_tokens": 8192,
        "reasoning": {"effort": "none"},
    },
    "gemini-thinking-2048": {
        "model": "google/gemini-2.5-flash",
        "max_tokens": 8192,
        "reasoning": {"max_tokens": 2048},
    },
}
# USD per million tokens, OpenRouter list price consulted 2026-11-19.
# Estimates exclude credit purchase fees, provider price changes and rebates.
GEMINI_INPUT_USD_M = 0.30
GEMINI_OUTPUT_USD_M = 2.50
MAX_PILOT_USD = 0.05
MAX_REQUEST_BYTES = 65536


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
    if profile == "gemini-thinking-2048":
        request["temperature"] = 1.0
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
    if profile != "gemini-thinking-2048":
        return None  # preço do modelo legado não fixado neste piloto
    return round((payload_bytes * GEMINI_INPUT_USD_M
                  + PROFILES[profile]["max_tokens"] * GEMINI_OUTPUT_USD_M)
                 / 1_000_000, 6)


def send_single_request(request, key, *, http=None):
    """Uma única POST, sem retry nem persistência de texto bruto."""
    if not isinstance(key, str) or not key or "\r" in key or "\n" in key:
        raise Problem("pilot_authentication_error")
    raw = encode_request(request)
    client = http if http is not None else HTTPS(
        read_timeout=120, total_timeout=180, max_response=1024 * 1024,
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
        raise Problem("pilot_incomplete_response", usage=_safe_output_usage(envelope))
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant" or message.get("tool_calls"):
        raise Problem("pilot_invalid_envelope")
    content = message.get("content")
    if not isinstance(content, str):
        raise Problem("pilot_invalid_envelope")
    usage = _safe_output_usage(envelope)
    if (request.get("reasoning") == {"max_tokens": 2048}
            and usage is not None and usage.get("reasoning_tokens", 0) > 2048):
        raise Problem("pilot_reasoning_budget_exceeded", usage=usage)
    validated = validate_review(content)
    # Do not expose, save, copy or print the raw model content.
    if not validated.ok:
        raise Problem("pilot_invalid_review", usage=usage)
    return validated.review, usage


def run_one(profile, case_id, key, *, http=None):
    """Returns only numeric grading metrics and fixed model/case metadata."""
    if case_id not in CASE_IDS or profile not in PROFILES:
        raise ValueError("pilot_not_allowlisted")
    case = load_cases(case_id=case_id)[0]
    request = build_pilot_request(case, profile)
    result, usage = send_single_request(request, key, http=http)
    match = evaluate_review(result, load_ground_truth(case_id))
    return {
        "mode": "paid_result",
        "profile": profile,
        "case_id": case_id,
        "model": PROFILES[profile]["model"],
        "status": "review_valid",
        "tp": match["tp"],
        "fp": match["fp"],
        "fn": match["fn"],
        "usage": usage,
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
        raise Problem("pilot_paid_blocked_in_ci")
    if args.profile != "gemini-thinking-2048":
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
        # Fixed codes only. Never print exception context, raw messages or key.
        print(json.dumps({"mode": "blocked_or_failed", "code": error.code,
                          "calls_may_be_billed": bool(args.paid)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
