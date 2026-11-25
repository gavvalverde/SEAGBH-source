"""Offline-only comparison of the public OpenRouter model catalog with K68's pin.

This is evidence from a supplied snapshot, not a copy of effective router policy.
It never fetches a catalog, reads credentials, or sends an inference request.
"""

from decimal import Decimal, InvalidOperation

from contract_v2 import PROVIDER_ENDPOINT as PIN, OUTPUT_USD_M_CEILING
from runner import PRODUCTION_MODEL as MODEL, PRODUCTION_INPUT_USD_M_CEILING

CAPS_USD_PER_MILLION = {
    "prompt": Decimal(str(PRODUCTION_INPUT_USD_M_CEILING)),
    "completion": Decimal(str(OUTPUT_USD_M_CEILING)),
}
REQUIRED_ADVERTISED = frozenset({"reasoning", "max_tokens", "temperature", "structured_outputs"})


def inspect_snapshot(snapshot: dict) -> dict:
    """Return pin presence and independent advertised price/parameter comparisons."""
    data = snapshot.get("data")
    if not isinstance(data, dict) or data.get("id") != MODEL:
        raise ValueError("catalog model mismatch")
    endpoints = data.get("endpoints")
    if not isinstance(endpoints, list):
        raise ValueError("catalog endpoints missing")
    pinned = []
    for endpoint in endpoints:
        tag = endpoint.get("tag") if isinstance(endpoint, dict) else None
        if not isinstance(tag, str) or not tag:
            raise ValueError("catalog endpoint tag missing")
        if tag != PIN:
            continue
        pricing = endpoint.get("pricing")
        rates = {}
        for key, ceiling in CAPS_USD_PER_MILLION.items():
            raw = pricing.get(key) if isinstance(pricing, dict) else None
            try:
                per_token = Decimal(str(raw))
            except InvalidOperation:
                per_token = Decimal("NaN")
            rates[key] = (
                None if not per_token.is_finite() or per_token < 0
                else per_token <= ceiling / Decimal("1000000")
            )
        advertised = endpoint.get("supported_parameters")
        known = isinstance(advertised, list) and all(isinstance(p, str) for p in advertised)
        pinned.append({
            "tag": tag,
            "within_price_cap": rates,
            "missing_advertised": sorted(REQUIRED_ADVERTISED.difference(advertised)) if known else None,
            # The wire field is response_format; OpenRouter documents structured_outputs
            # as the capability indicator. Keep the literal field marker separately.
            "response_format_listed": "response_format" in advertised if known else None,
        })
    return {"endpoint_count": len(endpoints), "pinned": pinned}
