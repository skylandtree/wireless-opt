from __future__ import annotations

import uuid
from typing import Any

from .database import connect, init_db
from .planner import plan_query
from .repositories import get_trace, list_dispatch_logs, list_plans, list_tickets, save_trace_header
from .skill_runtime import DAGExecutor, ExecutionContext
from .skills import build_registry


class NetOptOrchestrator:
    def __init__(self) -> None:
        self.registry = build_registry()
        self.executor = DAGExecutor(self.registry)

    def run(self, query: str) -> dict[str, Any]:
        trace_id = f"TRACE-{uuid.uuid4().hex[:12].upper()}"
        intent = plan_query(query)
        conn = connect()
        init_db(conn)
        save_trace_header(conn, trace_id, query, intent.get("cell_id"), "chat", "running", [], None)
        ctx = ExecutionContext(trace_id=trace_id, query=query, cell_id=intent.get("cell_id"), conn=conn, intent=intent)
        trace = self.executor.execute(ctx, intent["skills"])
        report = ctx.memory.get("report_writer", {})
        answer = report.get("answer") or report.get("summary") or "执行完成。"
        save_trace_header(conn, trace_id, query, ctx.cell_id, "chat", "completed", trace, answer)

        root_causes = ctx.memory.get("root_cause_ranker", {}).get("root_causes", [])
        plan = ctx.memory.get("solution_generator", {}).get("plan")
        risk = ctx.memory.get("risk_guard", {}).get("risk")
        evaluation = ctx.memory.get("effect_evaluator", {}).get("evaluation")
        kpis = ctx.memory.get("kpi_fetch", {}).get("kpis", [])
        tickets = ctx.memory.get("ticket_query", {}).get("tickets", [])
        cell = ctx.memory.get("cell_profile", {}).get("cell") or ctx.memory.get("ticket_query", {}).get("cell")
        dispatch = ctx.memory.get("plan_dispatch", {}).get("dispatch")
        response = {
            "trace_id": trace_id,
            "query": query,
            "answer": answer,
            "intent": intent,
            "dag": [
                {
                    "id": index + 1,
                    "skill": skill,
                    "depends_on": [] if index == 0 else [intent["skills"][index - 1]],
                    "status": next((item["status"] for item in trace if item["skill_name"] == skill), "pending"),
                }
                for index, skill in enumerate(intent["skills"])
            ],
            "runtime_policy": {
                "skill_mode": "white_list_only",
                "dispatch_mode": "simulation_first",
                "trace_enabled": True,
                "human_approval": "required_for_high_risk",
            },
            "cell": cell,
            "executed_skills": trace,
            "root_causes": root_causes,
            "plans": [plan] if plan else [],
            "risks": risk,
            "evaluation": evaluation,
            "tickets": tickets,
            "dispatch": dispatch,
            "charts_data": {
                "kpi": kpis[-24:],
                "root_causes": root_causes,
            },
        }
        conn.close()
        return response

    def stream(self, query: str):
        trace_id = f"TRACE-{uuid.uuid4().hex[:12].upper()}"
        yield {"type": "planner_start", "query": query, "message": "Planner 正在解析意图、识别小区和选择 Skill。"}
        intent = plan_query(query)
        dag = [
            {
                "id": index + 1,
                "skill": skill,
                "depends_on": [] if index == 0 else [intent["skills"][index - 1]],
                "status": "queued",
            }
            for index, skill in enumerate(intent["skills"])
        ]
        yield {
            "type": "planner_done",
            "trace_id": trace_id,
            "intent": intent,
            "dag": dag,
            "message": f"Planner 已生成 {len(dag)} 个 Skill 节点，目标小区：{intent.get('cell_name') or '未识别'}。",
        }
        conn = connect()
        init_db(conn)
        save_trace_header(conn, trace_id, query, intent.get("cell_id"), "chat_stream", "running", [], None)
        ctx = ExecutionContext(trace_id=trace_id, query=query, cell_id=intent.get("cell_id"), conn=conn, intent=intent)
        trace: list[dict[str, Any]] = []
        for event in self.executor.execute_stream(ctx, intent["skills"]):
            if event["type"] == "skill_done":
                trace.append(
                    {
                        "skill_name": event["skill_name"],
                        "status": event["status"],
                        "duration_ms": event["duration_ms"],
                        "summary": event["summary"],
                        "confidence": event["confidence"],
                        "evidence": event["evidence"],
                    }
                )
            yield event
        report = ctx.memory.get("report_writer", {})
        answer = report.get("answer") or report.get("summary") or "执行完成。"
        save_trace_header(conn, trace_id, query, ctx.cell_id, "chat_stream", "completed", trace, answer)
        root_causes = ctx.memory.get("root_cause_ranker", {}).get("root_causes", [])
        plan = ctx.memory.get("solution_generator", {}).get("plan")
        risk = ctx.memory.get("risk_guard", {}).get("risk")
        evaluation = ctx.memory.get("effect_evaluator", {}).get("evaluation")
        kpis = ctx.memory.get("kpi_fetch", {}).get("kpis", [])
        tickets = ctx.memory.get("ticket_query", {}).get("tickets", [])
        cell = ctx.memory.get("cell_profile", {}).get("cell") or ctx.memory.get("ticket_query", {}).get("cell")
        dispatch = ctx.memory.get("plan_dispatch", {}).get("dispatch")
        yield {
            "type": "final",
            "trace_id": trace_id,
            "query": query,
            "answer": answer,
            "intent": intent,
            "dag": [
                {
                    "id": index + 1,
                    "skill": skill,
                    "depends_on": [] if index == 0 else [intent["skills"][index - 1]],
                    "status": next((item["status"] for item in trace if item["skill_name"] == skill), "pending"),
                }
                for index, skill in enumerate(intent["skills"])
            ],
            "runtime_policy": {
                "skill_mode": "white_list_only",
                "dispatch_mode": "simulation_first",
                "trace_enabled": True,
                "human_approval": "required_for_high_risk",
            },
            "cell": cell,
            "executed_skills": trace,
            "root_causes": root_causes,
            "plans": [plan] if plan else [],
            "risks": risk,
            "evaluation": evaluation,
            "tickets": tickets,
            "dispatch": dispatch,
            "charts_data": {"kpi": kpis[-24:], "root_causes": root_causes},
            "message": "Runtime 执行完成，Trace 已落库。",
        }
        conn.close()

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        with connect() as conn:
            return get_trace(conn, trace_id)

    def dashboard_data(self, cell_id: str | None = None) -> dict[str, Any]:
        with connect() as conn:
            return {
                "tickets": list_tickets(conn, cell_id, 20),
                "plans": list_plans(conn, cell_id, 12),
                "dispatch_logs": list_dispatch_logs(conn, cell_id, 12),
            }


orchestrator = NetOptOrchestrator()
