from __future__ import annotations

import re
from typing import Any

from .database import connect
from .repositories import find_cell
from .utils import normalize_text


BASE_ANALYSIS = [
    "ticket_query",
    "cell_profile",
    "kpi_fetch",
    "alarm_correlate",
    "coverage_analysis",
    "interference_analysis",
    "capacity_analysis",
    "handover_analysis",
    "root_cause_ranker",
]


def parse_cell_hint(query: str) -> str | None:
    patterns = [
        r"([\u4e00-\u9fa5A-Za-z0-9]{2,12}_\d{3})",
        r"(CELL-\d{3})",
        r"([A-Za-z0-9\u4e00-\u9fa5]+小区)",
    ]
    for pattern in patterns:
        match = re.search(pattern, query)
        if match:
            hint = match.group(1).replace("小区", "")
            return hint[-8:] if "_" in hint and len(hint) > 8 else hint
    return None


def plan_query(query: str) -> dict[str, Any]:
    text = normalize_text(query)
    wants_ticket = any(word in text for word in ("工单", "投诉", "派单", "查询"))
    wants_root = any(word in text for word in ("根因", "原因", "分析", "诊断", "问题"))
    wants_solution = any(word in text for word in ("方案", "建议", "优化", "处理"))
    wants_risk = any(word in text for word in ("风险", "校验", "审批", "审阅"))
    no_dispatch = any(word in text for word in ("不要下发", "不下发", "先不下发", "无需下发", "下发前", "不要执行", "不执行"))
    wants_dispatch = any(word in text for word in ("下发", "执行", "提交")) and not no_dispatch
    wants_eval = any(word in text for word in ("效果评估", "效果", "复盘", "前后", "下发后", "评估昨天", "评估下发"))
    if wants_risk and "风险评估" in text and not any(word in text for word in ("效果", "复盘", "前后", "下发后")):
        wants_eval = False

    if not any([wants_ticket, wants_root, wants_solution, wants_risk, wants_dispatch, wants_eval]):
        wants_ticket = wants_root = wants_solution = True

    plan: list[str] = []
    if wants_ticket:
        plan.append("ticket_query")
    if wants_root or wants_solution or wants_risk or wants_dispatch or wants_eval:
        for skill in BASE_ANALYSIS:
            if skill not in plan:
                plan.append(skill)
    if wants_solution or wants_risk or wants_dispatch:
        plan.append("solution_generator")
    if wants_solution or wants_risk or wants_dispatch:
        plan.append("risk_guard")
    if wants_dispatch:
        plan.append("plan_dispatch")
    if wants_eval:
        if "kpi_fetch" not in plan:
            plan.insert(0, "kpi_fetch")
        plan.append("effect_evaluator")
    if "report_writer" not in plan:
        plan.append("report_writer")

    cell_hint = parse_cell_hint(query)
    with connect() as conn:
        cell = find_cell(conn, query, cell_hint)
    return {
        "query": query,
        "cell_hint": cell["name"] if cell else cell_hint,
        "cell_id": cell["cell_id"] if cell else None,
        "cell_name": cell["name"] if cell else None,
        "ticket": wants_ticket,
        "root_cause": wants_root,
        "solution": wants_solution,
        "risk": wants_risk,
        "dispatch": wants_dispatch,
        "evaluation": wants_eval,
        "skills": plan,
    }
