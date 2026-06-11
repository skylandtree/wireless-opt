from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .repositories import save_skill_run
from .utils import now_iso


SkillOutput = dict[str, Any]


@dataclass
class ExecutionContext:
    trace_id: str
    query: str
    cell_id: str | None
    conn: sqlite3.Connection
    intent: dict[str, Any]
    memory: dict[str, Any] = field(default_factory=dict)


class Skill:
    name = "base"
    description = "Base skill"
    input_schema: dict[str, Any] = {}
    output_schema: dict[str, Any] = {}

    def run(self, ctx: ExecutionContext, payload: dict[str, Any]) -> SkillOutput:
        raise NotImplementedError


class FunctionSkill(Skill):
    def __init__(
        self,
        name: str,
        description: str,
        func: Callable[[ExecutionContext, dict[str, Any]], SkillOutput],
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.func = func
        self.input_schema = input_schema or {}
        self.output_schema = output_schema or {}

    def run(self, ctx: ExecutionContext, payload: dict[str, Any]) -> SkillOutput:
        return self.func(ctx, payload)


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if skill.name in self._skills:
            raise ValueError(f"Skill already registered: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill:
        if name not in self._skills:
            raise KeyError(f"Unknown skill: {name}")
        return self._skills[name]

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "name": skill.name,
                "description": skill.description,
                "input_schema": skill.input_schema,
                "output_schema": skill.output_schema,
            }
            for skill in self._skills.values()
        ]


class DAGExecutor:
    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

    def execute(self, ctx: ExecutionContext, plan: list[str]) -> list[dict[str, Any]]:
        trace: list[dict[str, Any]] = []
        for skill_name in plan:
            skill = self.registry.get(skill_name)
            started = time.perf_counter()
            started_at = now_iso()
            payload = {
                "query": ctx.query,
                "cell_id": ctx.cell_id,
                "intent": ctx.intent,
                "memory": ctx.memory,
            }
            try:
                output = skill.run(ctx, payload)
                status = "success"
            except Exception as exc:  # pragma: no cover - surfaced to trace for demo robustness
                output = {
                    "summary": f"{skill_name} 执行失败: {exc}",
                    "evidence": [],
                    "confidence": 0.0,
                    "next_recommended_skills": [],
                    "error": str(exc),
                }
                status = "failed"
            finished_at = now_iso()
            duration_ms = int((time.perf_counter() - started) * 1000)
            ctx.memory[skill_name] = output
            run = {
                "run_id": f"RUN-{uuid.uuid4().hex[:12].upper()}",
                "trace_id": ctx.trace_id,
                "skill_name": skill_name,
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_ms": duration_ms,
                "input": payload,
                "output": output,
            }
            save_skill_run(ctx.conn, run)
            trace.append(
                {
                    "skill_name": skill_name,
                    "status": status,
                    "duration_ms": duration_ms,
                    "summary": output.get("summary", ""),
                    "confidence": output.get("confidence", 0),
                    "evidence": output.get("evidence", []),
                }
            )
            if status == "failed":
                break
        return trace

    def execute_stream(self, ctx: ExecutionContext, plan: list[str]):
        trace: list[dict[str, Any]] = []
        for skill_name in plan:
            skill = self.registry.get(skill_name)
            yield {
                "type": "reasoning_step",
                "phase": "observe",
                "skill_name": skill_name,
                "message": f"读取用户任务与上下文记忆，准备判断是否需要调用 {skill_name}。",
                "memory_keys": list(ctx.memory.keys()),
            }
            yield {
                "type": "reasoning_step",
                "phase": "hypothesis",
                "skill_name": skill_name,
                "message": self._reasoning_message(skill_name),
                "memory_keys": list(ctx.memory.keys()),
            }
            yield {
                "type": "skill_start",
                "skill_name": skill_name,
                "description": skill.description,
                "memory_keys": list(ctx.memory.keys()),
                "cell_id": ctx.cell_id,
            }
            started = time.perf_counter()
            started_at = now_iso()
            payload = {
                "query": ctx.query,
                "cell_id": ctx.cell_id,
                "intent": ctx.intent,
                "memory": ctx.memory,
            }
            try:
                output = skill.run(ctx, payload)
                status = "success"
            except Exception as exc:  # pragma: no cover
                output = {
                    "summary": f"{skill_name} 执行失败: {exc}",
                    "evidence": [],
                    "confidence": 0.0,
                    "next_recommended_skills": [],
                    "error": str(exc),
                }
                status = "failed"
            finished_at = now_iso()
            duration_ms = int((time.perf_counter() - started) * 1000)
            ctx.memory[skill_name] = output
            run = {
                "run_id": f"RUN-{uuid.uuid4().hex[:12].upper()}",
                "trace_id": ctx.trace_id,
                "skill_name": skill_name,
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_ms": duration_ms,
                "input": payload,
                "output": output,
            }
            save_skill_run(ctx.conn, run)
            trace_item = {
                "skill_name": skill_name,
                "status": status,
                "duration_ms": duration_ms,
                "summary": output.get("summary", ""),
                "confidence": output.get("confidence", 0),
                "evidence": output.get("evidence", []),
            }
            trace.append(trace_item)
            yield {
                "type": "skill_done",
                "skill_name": skill_name,
                "status": status,
                "duration_ms": duration_ms,
                "summary": output.get("summary", ""),
                "evidence": output.get("evidence", [])[:4],
                "confidence": output.get("confidence", 0),
                "next_recommended_skills": output.get("next_recommended_skills", []),
                "memory_keys": list(ctx.memory.keys()),
            }
            yield {
                "type": "reasoning_step",
                "phase": "conclusion",
                "skill_name": skill_name,
                "message": f"{skill_name} 已产出结构化结果，写入 Context Memory，并把证据传递给后续 Skill。",
                "memory_keys": list(ctx.memory.keys()),
            }
            if status == "failed":
                break
        return trace

    def _reasoning_message(self, skill_name: str) -> str:
        messages = {
            "ticket_query": "先查询工单流，确认用户投诉、派单状态和问题类别，避免脱离业务现场。",
            "cell_profile": "需要建立小区画像，读取制式、频段、参数和邻区关系作为后续分析底座。",
            "kpi_fetch": "需要读取最近 KPI 趋势，判断问题是否在覆盖、干扰、容量或切换指标上显性化。",
            "alarm_correlate": "需要把 KPI 异常与告警时间线对齐，排除设备、传输、驻波等硬件侧原因。",
            "coverage_analysis": "检查 RSRP、SINR、掉线率和天馈参数，验证是否存在弱覆盖或覆盖失衡。",
            "interference_analysis": "检查 SINR、上行干扰和 PCI/MOD3 冲突，验证是否存在内外部干扰。",
            "capacity_analysis": "检查 PRB、用户数、吞吐和接通率，验证是否为容量拥塞导致体验下降。",
            "handover_analysis": "检查切换成功率、邻区漏配和 A3/CIO 参数，验证移动性问题。",
            "root_cause_ranker": "融合各分析 Skill 的证据和置信度，形成根因 TopN，而不是单指标拍脑袋。",
            "solution_generator": "根据首要根因选择参数、邻区、天馈、扩容或现场测试动作，生成方案草案。",
            "risk_guard": "对方案做影响面、审批要求和回退策略校验，防止自动化建议直接伤网。",
            "plan_dispatch": "仅执行模拟下发或进入审批队列，真实网元变更必须人工确认。",
            "effect_evaluator": "对比前后 KPI，判断方案是否形成优化闭环。",
            "report_writer": "把调度轨迹、证据链、根因和方案整理成工程师可读报告。",
        }
        return messages.get(skill_name, f"根据 DAG 编排调用 {skill_name}，补充当前任务证据。")
