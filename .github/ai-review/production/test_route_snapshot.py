"""Synthetic public-catalog shapes only; no networking or runtime integration."""

import unittest

from route_snapshot import MODEL, inspect_snapshot


def snapshot(*endpoints):
    return {"data": {"id": MODEL, "endpoints": list(endpoints)}}


def row(tag="baidu", prompt="0.00000042", completion="0.00000175",
        parameters=("reasoning", "max_tokens", "temperature", "structured_outputs")):
    return {"tag": tag, "pricing": {"prompt": prompt, "completion": completion},
            "supported_parameters": list(parameters)}


class RouteSnapshotTests(unittest.TestCase):
    def test_absent_pin_cannot_select_alternative_or_name_prefix(self):
        result = inspect_snapshot(snapshot(row("chutes/int4"), row("baidu-other")))
        self.assertEqual(result, {"endpoint_count": 2, "pinned": []})

    def test_price_comparison_has_inclusive_per_million_caps(self):
        result = inspect_snapshot(snapshot(
            row("baidu/fp8"),
            row("baidu/bf16", prompt="0.000000421", completion="0.000001751"),
        ))["pinned"]
        self.assertEqual(result[0]["within_price_cap"], {"prompt": True, "completion": True})
        self.assertEqual(result[1]["within_price_cap"], {"prompt": False, "completion": False})

    def test_missing_advertised_capabilities_are_independent_of_price(self):
        result = inspect_snapshot(snapshot(row(parameters=("max_tokens", "response_format"))))["pinned"][0]
        self.assertEqual(result["within_price_cap"], {"prompt": True, "completion": True})
        self.assertEqual(result["missing_advertised"], ["reasoning", "structured_outputs", "temperature"])
        self.assertTrue(result["response_format_listed"])

    def test_structured_outputs_marker_need_not_match_wire_field_name(self):
        result = inspect_snapshot(snapshot(row()))["pinned"][0]
        self.assertEqual(result["missing_advertised"], [])
        self.assertFalse(result["response_format_listed"])

    def test_unknown_metadata_is_not_reported_as_within_cap_or_supported(self):
        item = row(prompt=None, completion="NaN")
        del item["supported_parameters"]
        result = inspect_snapshot(snapshot(item))["pinned"][0]
        self.assertEqual(result["within_price_cap"], {"prompt": None, "completion": None})
        self.assertIsNone(result["missing_advertised"])
        self.assertIsNone(result["response_format_listed"])

    def test_malformed_snapshot_is_distinct_from_absent_pin(self):
        with self.assertRaisesRegex(ValueError, "endpoints"):
            inspect_snapshot({"data": {"id": MODEL}})


if __name__ == "__main__":
    unittest.main()
