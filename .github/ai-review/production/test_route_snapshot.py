"""Synthetic public-catalog shapes only; no networking or runtime integration."""

import unittest

from route_snapshot import MODEL, inspect_snapshot


def snapshot(*endpoints):
    return {"data": {"id": MODEL, "endpoints": list(endpoints)}}


def row(tag="chutes/int4", prompt="0.00000042", completion="0.00000180",
        parameters=("reasoning", "max_tokens", "temperature", "structured_outputs")):
    return {"tag": tag, "pricing": {"prompt": prompt, "completion": completion},
            "supported_parameters": list(parameters)}


class RouteSnapshotTests(unittest.TestCase):
    def test_absent_pin_cannot_select_alternative_or_name_prefix(self):
        result = inspect_snapshot(snapshot(
            row("baidu"), row("chutes"), row("chutes/bf16"), row("chutes/int4-other")))
        self.assertEqual(result, {"endpoint_count": 4, "pinned": []})

    def test_public_price_fixture_is_exclusive_and_within_authorized_caps(self):
        result = inspect_snapshot(snapshot(
            row("chutes/bf16"), row(prompt="0.00000039"), row("baidu")))["pinned"]
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tag"], "chutes/int4")
        self.assertEqual(result[0]["within_price_cap"], {"prompt": True, "completion": True})
        self.assertEqual(result[0]["missing_advertised"], [])

    def test_price_comparison_has_inclusive_per_million_caps(self):
        for item, expected in (
            (row(), {"prompt": True, "completion": True}),
            (row(prompt="0.000000421"), {"prompt": False, "completion": True}),
            (row(completion="0.000001801"), {"prompt": True, "completion": False}),
        ):
            with self.subTest(expected=expected):
                result = inspect_snapshot(snapshot(item))["pinned"]
                self.assertEqual(result[0]["within_price_cap"], expected)

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
