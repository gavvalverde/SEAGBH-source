"""Offline candidates only. No credentials, HTTP, GT, CLI or PR publication.

The active runner and shared benchmark defaults are deliberately unchanged.
Capabilities must come from a separately reviewed endpoint snapshot, never PR
or model text. This record validates the declared contract, not remote truth.
"""
from dataclasses import dataclass
import json
from types import MappingProxyType

import core
from transport import OpenRouter, Problem


MAX_REQUEST_BYTES = 65536
MAX_TOKENS = 8192
_REQUIRED_PARAMETERS = frozenset({"temperature", "max_tokens", "reasoning", "response_format"})


@dataclass(frozen=True)
class Profile:
    model: str
    provider: str
    disable_reasoning: bool
    input_usd_m_ceiling: float
    output_usd_m_ceiling: float


PROFILES = MappingProxyType({
    "kimi-k26-nonthinking": Profile("moonshotai/kimi-k2.6", "moonshotai", True, 1.0, 4.0),
    "glm53-flash-low": Profile("z-ai/glm-5.3-flash", "deepinfra", False, 0.2, 0.6),
})


@dataclass(frozen=True)
class EndpointCapabilities:
    model: str
    provider: str
    supported_parameters: frozenset[str]
    json_object: bool | None = None
    max_completion_tokens: int | None = None
    can_disable_reasoning: bool | None = None
    reasoning_efforts: frozenset[str] = frozenset()


def _profile(name):
    if not isinstance(name, str) or name not in PROFILES:
        raise Problem("experimental_profile_not_allowed")
    return PROFILES[name]


def _verified_endpoint(profile, capabilities):
    if capabilities is None:
        raise Problem("experimental_endpoint_unverified")
    if type(capabilities) is not EndpointCapabilities:
        raise Problem("experimental_endpoint_invalid")
    if (capabilities.model, capabilities.provider) != (profile.model, profile.provider):
        raise Problem("experimental_endpoint_mismatch")
    if (type(capabilities.supported_parameters) is not frozenset
            or not _REQUIRED_PARAMETERS <= capabilities.supported_parameters
            or capabilities.json_object is not True
            or type(capabilities.max_completion_tokens) is not int
            or capabilities.max_completion_tokens < MAX_TOKENS):
        raise Problem("experimental_parameters_unsupported")
    if profile.disable_reasoning:
        if capabilities.can_disable_reasoning is not True:
            raise Problem("experimental_reasoning_unsupported")
    elif (type(capabilities.reasoning_efforts) is not frozenset
          or "low" not in capabilities.reasoning_efforts):
        raise Problem("experimental_reasoning_unsupported")


def _controls(selected):
    return {
        "model": selected.model, "temperature": 0.3, "max_tokens": MAX_TOKENS,
        "reasoning": {"enabled": False} if selected.disable_reasoning else {"effort": "low"},
        "response_format": {"type": "json_object"},
        "provider": {
            "only": [selected.provider], "allow_fallbacks": False,
            "require_parameters": True,
            "max_price": {"prompt": selected.input_usd_m_ceiling,
                          "completion": selected.output_usd_m_ceiling},
        },
    }


def _check_payload(payload, selected):
    expected = _controls(selected)
    if not isinstance(payload, dict) or set(payload) != set(expected) | {"messages"}:
        raise Problem("experimental_payload_mismatch")
    actual = {k: payload[k] for k in expected}
    def encode(data):
        try:
            return json.dumps(data, sort_keys=True, ensure_ascii=True, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            raise Problem("experimental_payload_invalid") from None
    if encode(actual) != encode(expected):
        raise Problem("experimental_payload_mismatch")
    if len(encode(payload).encode("utf-8")) > MAX_REQUEST_BYTES:
        raise Problem("experimental_request_size_limit")


def build_experimental_payload(name, title, body, diff, *, capabilities=None,
                               context="", trunc_note="", omit_info=""):
    """Same prompt bytes; only an explicit candidate profile changes parameters.

    No real capability snapshot is bundled: unverified endpoints fail closed.
    There is no model/provider override from PR data or environment variables.
    """
    selected = _profile(name)
    _verified_endpoint(selected, capabilities)
    payload = core.build_payload(title, body, diff, context=context,
        trunc_note=trunc_note, omit_info=omit_info, model=selected.model)
    if payload.get("model") != selected.model:
        raise Problem("experimental_model_mismatch")
    # OpenRouter's unified parameter, NOT Moonshot's native `thinking` field.
    payload.update(_controls(selected))
    _check_payload(payload, selected)
    return payload


class _ExperimentalTransport(OpenRouter):
    def __init__(self, selected, key, http):
        super().__init__(key, http=http, max_attempts=1,
                         require_reasoning_usage=selected.disable_reasoning)
        self._selected = selected

    def complete(self, payload):
        self.last_usage = None
        self.last_http_elapsed_ms = None
        self.last_reasoning_control = None
        _check_payload(payload, self._selected)
        return super().complete(payload)


def experimental_transport(name, key, *, capabilities=None, http):
    """Explicit injected transport only; no live HTTP default or key discovery.

    Future paid execution needs separate authorization and an external budget.
    No retry or provider fallback is authorized by constructing this client.
    """
    selected = _profile(name)
    _verified_endpoint(selected, capabilities)
    if http is None or not callable(getattr(http, "send", None)):
        raise Problem("experimental_transport_required")
    return _ExperimentalTransport(selected, key, http)
