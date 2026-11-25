"""K68-E offline contracts. The responses here are mocks, not Kimi evidence."""
from dataclasses import replace
from decimal import Decimal
import json
import socket
import unittest
from unittest.mock import Mock

import pilot_offline as pilot
from oracles.expectations import prove

CLEAN = {"schema_version": 2, "summary": "Revisão inventada", "reviewed_scope": "Caso inventado",
         "analysis_status": "complete", "confidence_overall": "high", "findings": []}


def reply(doc, status=200):
    return pilot.OfflineReply(status, json.dumps(doc))


def finding(plan):
    anchor = next(iter(plan.review_plan.anchors))
    return {"anchor_id": anchor, "severity": "important", "category": "regression",
            "confidence": "high", "title": "Synthetic candidate",
            "trigger": "An invented trigger reaches the changed return",
            "mechanism": "The changed predicate affects the synthetic behavior",
            "impact": "The synthetic caller sees a different result",
            "counterevidence": "Neighboring synthetic inputs were also considered",
            "suggested_fix": "Check the intended contract for the fictional caller"}


def verdict(candidate, decision, state):
    return {"schema_version": 2, "analysis_status": "complete", "confidence_overall": "high",
            "verdicts": [{"candidate_id": pilot.contract.candidate_id(candidate, 0),
                          "decision": decision, "current_state": state,
                          "behavior_path": "A synthetic branch was checked offline",
                          "countercheck": "An adjacent input is still handled as before"}]}


class OfflinePilot(unittest.TestCase):
    def setUp(self):
        self.plans = [pilot.make_plan(case) for case in pilot.CASES]

    def mocks(self, overrides=()):
        mocks = {p.case_id: Mock(side_effect=[reply(CLEAN)]) for p in self.plans}
        for index, responses in overrides:
            mocks[self.plans[index].case_id] = Mock(side_effect=responses)
        return mocks

    def test_oracles_plan_integrity_and_no_oracle_payload(self):
        self.assertEqual([prove(case) for case in pilot.CASES], [True, True, False, False])
        self.assertEqual(len(self.plans), 4)
        for plan in self.plans:
            data = json.loads(plan.discovery[0]["messages"][1]["content"])
            self.assertNotIn("candidates_to_refute", data)
            self.assertEqual(plan.discovery[0]["model"], pilot.MODEL)
            self.assertEqual(plan.discovery[0]["provider"]["only"], ["chutes/int4"])
            self.assertEqual(plan.review_plan.coverage["files_supplied"], 1)
            self.assertEqual(plan.review_plan.coverage["hunks_supplied"], 1)
            self.assertEqual(plan.review_plan.coverage["files_omitted"], 0)
            self.assertEqual(set(data["anchors"]), set(plan.review_plan.files))
            self.assertNotIn("ORACLES", json.dumps(data))
        with pilot.no_network(), self.assertRaisesRegex(AssertionError, "Real network forbidden"):
            socket.create_connection(("example.invalid", 443))

    def test_success_stages_distinguish_retained_positive_refuted_negative_and_missed_positive(self):
        yes, false = finding(self.plans[0]), finding(self.plans[3])
        mocks = self.mocks([
            (0, [reply(dict(CLEAN, findings=[yes])), reply(verdict(yes, "retain", "reachable_regression"))]),
            (3, [reply(dict(CLEAN, findings=[false])), reply(verdict(false, "refute", "fixed_by_patch"))]),
        ])
        reports = pilot.run_offline(mocks)
        grades = [pilot.grade(c, r, {item["candidate_id"]: i == 0 for item in r["candidates"]})
                  for i, (c, r) in enumerate(zip(pilot.CASES, reports))]
        self.assertEqual([r["calls"] for r in reports], [2, 1, 1, 2])
        self.assertTrue(all(r["origin"] == "offline_mock" and
                            r["coverage"]["chunks_completed"] == 1 for r in reports))
        self.assertEqual(reports[3]["retained"], ())
        self.assertEqual(grades[1]["match"], {"tp": 0, "fp": 0, "fn": 1})
        self.assertTrue(grades[1]["semantic_error"])
        self.assertEqual(pilot.summarize(grades)["FP"], 0)
        self.assertEqual(pilot.summarize(grades, stage="discovery_match")["FP"], 1)
        self.assertEqual(pilot.summarize(grades)["TP"], 1)
        self.assertIsNone(pilot.grade(pilot.CASES[0], reports[0], {})["match"])

    def test_timeout_http_incomplete_invalid_schema_and_out_of_scope_are_not_evaluable(self):
        variants = [
            (TimeoutError(), "operational_failure", "timeout"),
            (reply(CLEAN, 503), "operational_failure", "invalid_http_response"),
            (reply(dict(CLEAN, analysis_status="incomplete")), "abstention", "v2_model_declared_incomplete"),
            (reply(dict(CLEAN, schema_version=1)), "operational_failure", "v2_schema_const_root_schema_version"),
            (reply(dict(CLEAN, findings=[dict(finding(self.plans[0]), anchor_id="a" + "f" * 16)])),
             "operational_failure", "v2_anchor_out_of_scope"),
        ]
        for response, status, reason in variants:
            with self.subTest(reason=reason):
                mocks = self.mocks([(0, [response])])
                report = pilot.run_offline(mocks)[0]
                self.assertEqual((report["status"], report["reason"], report["calls"]), (status, reason, 1))
                mocks["K68E-01"].assert_called_once()
                self.assertIsNone(pilot.grade(pilot.CASES[0], report, {})["match"])

    def test_uncertain_refutation_is_abstention_and_has_no_denominator(self):
        candidate = finding(self.plans[0])
        mocks = self.mocks([(0, [reply(dict(CLEAN, findings=[candidate])),
                                  reply(verdict(candidate, "uncertain", "insufficient_context"))])])
        report = pilot.run_offline(mocks)[0]
        self.assertEqual((report["status"], report["reason"], report["calls"]),
                         ("abstention", "v2_refutation_uncertain", 2))
        summary = pilot.summarize([pilot.grade(pilot.CASES[0], report, {})])
        self.assertEqual(summary["cases_evaluable"], 0)
        self.assertTrue(all(summary[k] is None for k in ("TP", "FP", "FN", "precision", "recall", "F1")))

    def test_financial_and_call_caps_hold_before_any_mock(self):
        bound = pilot.preflight(self.plans)
        self.assertEqual(bound["calls_max"], 8)
        self.assertFalse(bound["budget_supplied"])
        self.assertEqual(bound["usd_max"], "0.40661304")
        self.assertEqual(pilot.preflight(self.plans, ceiling=Decimal(bound["usd_max"]))["budget_supplied"], True)
        self.assertTrue(all(Decimal(s) <= Decimal("0.20") for s in bound["per_case_usd"].values()))
        mocks = self.mocks()
        with self.assertRaisesRegex(ValueError, "pilot budget exceeded"):
            pilot.run_offline(mocks, ceiling=Decimal("0.01"))
        self.assertTrue(all(m.call_count == 0 for m in mocks.values()))
        too_many = [replace(self.plans[0], discovery=self.plans[0].discovery * 2), *self.plans[1:]]
        with self.assertRaisesRegex(ValueError, "call cap"):
            pilot.preflight(too_many)
        with self.assertRaisesRegex(pilot.Problem, "v2_total_budget"):
            excessive = [{"test": "x" * 300_000}]
            pilot.preflight([replace(self.plans[0], discovery=excessive), *self.plans[1:]])


if __name__ == "__main__":
    unittest.main()
