import unittest

from netopt.database import connect, init_db
from netopt.orchestrator import NetOptOrchestrator
from netopt.planner import plan_query
from netopt.repositories import list_cells
from netopt.seed import seed_database


class NetOptClawTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed_database(force=True)

    def test_seed_data(self):
        with connect() as conn:
            init_db(conn)
            cells = list_cells(conn)
        self.assertEqual(len(cells), 20)

    def test_planner_builds_solution_plan(self):
        intent = plan_query("查询高科路_001小区的工单信息，分析根因，并生成优化方案")
        self.assertIn("ticket_query", intent["skills"])
        self.assertIn("root_cause_ranker", intent["skills"])
        self.assertIn("solution_generator", intent["skills"])
        self.assertIn("risk_guard", intent["skills"])
        self.assertEqual(intent["cell_name"], "高科路_001")

    def test_orchestrator_full_loop(self):
        result = NetOptOrchestrator().run("查询高科路_001小区的工单信息，分析根因，并生成优化方案")
        self.assertTrue(result["trace_id"].startswith("TRACE-"))
        self.assertGreaterEqual(len(result["executed_skills"]), 10)
        self.assertTrue(result["root_causes"])
        self.assertTrue(result["plans"])
        self.assertIn("方案", result["answer"])

    def test_dispatch_and_evaluation_branches(self):
        dispatch = NetOptOrchestrator().run("请下发陆家嘴_003小区的容量优化方案")
        self.assertIsNotNone(dispatch["dispatch"])
        self.assertIn(dispatch["dispatch"]["status"], ("pending_approval", "simulated_dispatched"))

        evaluation = NetOptOrchestrator().run("评估昨天对陆家嘴_003小区下发方案后的效果")
        self.assertIsNotNone(evaluation["evaluation"])
        self.assertIn("效果评估", evaluation["answer"])


if __name__ == "__main__":
    unittest.main()
