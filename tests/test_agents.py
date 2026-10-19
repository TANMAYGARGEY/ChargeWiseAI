"""Unit tests for ChargeWise AI multi-agent workflow.
Run with: python -m unittest discover -s tests -v
"""
import unittest
import pandas as pd
from data import make_demo_requests, make_price_table, make_scenario_requests, DEFAULT_CHARGERS
from tools import required_energy_kwh, required_slots, validate_schedule
from agents import run_workflow, request_analysis_agent
from llm_agent import mock_chargewise_ai, ask_chargewise_local_ai

class ChargeWiseTests(unittest.TestCase):
    def setUp(self):
        self.requests = make_demo_requests()
        self.prices = make_price_table(12)

    def test_energy_calculation(self):
        self.assertAlmostEqual(required_energy_kwh(20, 70), 30.0)

    def test_required_slots(self):
        # 30 kWh at 22 kW, half-hour slots: 11 kWh per slot => 3 slots.
        self.assertEqual(required_slots(20, 70, 22), 3)

    def test_analysis_adds_urgency(self):
        analysis, _ = request_analysis_agent(self.requests)
        self.assertIn("Urgency Score", analysis.columns)
        self.assertTrue((analysis["Urgency Score"] >= 0).all())

    def test_workflow_final_schedule_valid(self):
        result = run_workflow(self.requests, self.prices, DEFAULT_CHARGERS, 12)
        self.assertTrue(result["validation"]["valid"], result["validation"]["errors"])
        if not result["schedule"].empty:
            self.assertFalse(result["schedule"].duplicated(["Charger", "Slot"]).any())
            self.assertFalse(result["schedule"].duplicated(["EV ID", "Slot"]).any())

    def test_all_decision_policies_produce_valid_workflows(self):
        for policy in ["Balanced", "Cost saver", "Urgent first"]:
            with self.subTest(policy=policy):
                result = run_workflow(self.requests, self.prices, DEFAULT_CHARGERS, 12,
                                      strategy=policy)
                self.assertEqual(result["strategy"], policy)
                self.assertTrue(result["validation"]["valid"], result["validation"]["errors"])
                self.assertTrue(any(policy in line for line in result["log"]))

    def test_replanning_path_runs_two_attempts(self):
        result = run_workflow(
            self.requests, self.prices, DEFAULT_CHARGERS, 12,
            demonstrate_replanning=True
        )
        self.assertEqual(result["attempts"], 2)
        self.assertTrue(result["validation"]["valid"], result["validation"]["errors"])
        self.assertTrue(any("REJECT" in line for line in result["log"]))
        self.assertTrue(any("replanning" in line.lower() for line in result["log"]))

    def test_validator_detects_charger_conflict(self):
        schedule = pd.DataFrame([
            {"EV ID": "EV-101", "Charger": "Charger 1", "Slot": 0,
             "Power (kW)": 22, "Energy (kWh)": 11, "Cost (₹)": 93.5},
            {"EV ID": "EV-102", "Charger": "Charger 1", "Slot": 0,
             "Power (kW)": 11, "Energy (kWh)": 5.5, "Cost (₹)": 46.75},
        ])
        result = validate_schedule(schedule, self.requests, self.prices, DEFAULT_CHARGERS, 12)
        self.assertFalse(result["valid"])
        self.assertTrue(any("Charger conflict" in error for error in result["errors"]))

    def test_scenarios_generate_valid_schedules(self):
        for scenario in ["Standard Fleet (Balanced 10 EVs)", "Morning Rush (Tight Deadlines)", "Delivery Vans (Critical Low Battery)"]:
            with self.subTest(scenario=scenario):
                df = make_scenario_requests(scenario)
                self.assertFalse(df.empty)
                res = run_workflow(df, self.prices, DEFAULT_CHARGERS, 12)
                self.assertTrue(res["validation"]["valid"])

    def test_mock_explanation_agent(self):
        res = run_workflow(self.requests, self.prices, DEFAULT_CHARGERS, 12)
        ctx = {
            "selected_policy": "Balanced",
            "latest_result": {
                "schedule": res["schedule"].to_dict(orient="records"),
                "cost_summary": res["cost_summary"],
                "validation": res["validation"],
                "attempts": res["attempts"]
            }
        }
        ans = ask_chargewise_local_ai("Why was this schedule selected?", ctx, provider="mock")
        self.assertIn("Balanced", ans)
        self.assertTrue(len(ans) > 50)

if __name__ == "__main__":
    unittest.main()
