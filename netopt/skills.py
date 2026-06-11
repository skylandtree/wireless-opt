from __future__ import annotations

import uuid
from typing import Any

from .repositories import (
    fetch_alarms,
    fetch_kpi,
    fetch_neighbors,
    fetch_params,
    find_cell,
    get_latest_evaluation,
    get_plan,
    kpi_summary,
    list_dispatch_logs,
    list_plans,
    list_tickets,
    save_dispatch,
    save_evaluation,
    save_plan,
)
from .skill_runtime import ExecutionContext, FunctionSkill, SkillRegistry
from .utils import clamp, now_iso, weighted_score


def standard_output(summary: str, evidence: list[Any], confidence: float, next_skills: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "summary": summary,
        "evidence": evidence,
        "confidence": round(confidence, 3),
        "next_recommended_skills": next_skills,
        **extra,
    }


def skill_ticket_query(ctx: ExecutionContext, payload: dict[str, Any]) -> dict[str, Any]:
    cell = find_cell(ctx.conn, ctx.query, ctx.cell_id)
    if not cell:
        return standard_output("未找到匹配小区。", [], 0.0, ["cell_profile"], tickets=[])
    ctx.cell_id = cell["cell_id"]
    tickets = list_tickets(ctx.conn, cell["cell_id"], limit=8)
    severity_weight = {"P1": 3, "P2": 2, "P3": 1}
    score = sum(severity_weight.get(t["severity"], 1) for t in tickets[:5])
    summary = f"找到 {cell['name']} 近期待处理/观察工单 {len(tickets)} 条，其中 P1 {sum(1 for t in tickets if t['severity'] == 'P1')} 条。"
    evidence = [
        f"{t['ticket_id']} {t['severity']} {t['title']} {t['status']} 来源:{t['source']}"
        for t in tickets[:4]
    ]
    return standard_output(summary, evidence, clamp(0.55 + score / 30, 0.55, 0.96), ["cell_profile", "kpi_fetch"], cell=cell, tickets=tickets)


def skill_cell_profile(ctx: ExecutionContext, payload: dict[str, Any]) -> dict[str, Any]:
    cell = find_cell(ctx.conn, ctx.query, ctx.cell_id)
    if not cell:
        return standard_output("未找到小区画像。", [], 0.0, ["ticket_query"])
    ctx.cell_id = cell["cell_id"]
    params = fetch_params(ctx.conn, cell["cell_id"])
    neighbors = fetch_neighbors(ctx.conn, cell["cell_id"])
    summary = f"{cell['name']} 为 {cell['rat']} {cell['band']} 小区，场景标签为{cell['scenario_label']}，邻区 {len(neighbors)} 个。"
    evidence = [
        f"站点 {cell['site_id']} / {cell['district']} / {cell['vendor']}",
        f"PCI {params['pci']}，RS功率 {params['rs_power']} dBm，方位角 {params['azimuth']}，下倾 {params['tilt']}" if params else "未找到参数",
        f"最近邻区: {', '.join(n['neighbor_name'] for n in neighbors[:3])}",
    ]
    return standard_output(summary, evidence, 0.92, ["kpi_fetch", "alarm_correlate"], cell=cell, params=params, neighbors=neighbors)


def skill_kpi_fetch(ctx: ExecutionContext, payload: dict[str, Any]) -> dict[str, Any]:
    cell_id = ctx.cell_id or (ctx.memory.get("cell_profile", {}).get("cell") or {}).get("cell_id")
    kpis = fetch_kpi(ctx.conn, cell_id, hours=24) if cell_id else []
    summary_data = kpi_summary(kpis)
    if not kpis:
        return standard_output("未找到 KPI 数据。", [], 0.0, ["alarm_correlate"], kpis=[], kpi_summary={})
    s = summary_data
    summary = (
        f"最近24小时 KPI：RSRP均值 {s['rsrp']['avg']} dBm，SINR均值 {s['sinr']['avg']} dB，"
        f"掉线率均值 {s['drop_rate']['avg']}%，PRB峰值 {s['prb_util']['max']}%。"
    )
    evidence = [
        f"最新 RSRP {s['rsrp']['latest']} dBm / SINR {s['sinr']['latest']} dB",
        f"接通率最低 {s['access_success']['min']}%，切换成功率最低 {s['handover_success']['min']}%",
        f"下行吞吐均值 {s['dl_throughput']['avg']} Mbps，用户数峰值 {s['users']['max']}",
    ]
    return standard_output(summary, evidence, 0.94, ["coverage_analysis", "interference_analysis", "capacity_analysis", "handover_analysis"], kpis=kpis, kpi_summary=summary_data)


def skill_alarm_correlate(ctx: ExecutionContext, payload: dict[str, Any]) -> dict[str, Any]:
    cell_id = ctx.cell_id
    alarms = fetch_alarms(ctx.conn, cell_id) if cell_id else []
    active = [alarm for alarm in alarms if alarm["status"] == "active"]
    critical = [alarm for alarm in alarms if alarm["level"] in ("critical", "major")]
    if alarms:
        summary = f"关联到 {len(alarms)} 条告警，当前活跃 {len(active)} 条，重大/严重 {len(critical)} 条。"
        evidence = [f"{a['alarm_id']} {a['level']} {a['alarm_type']} {a['description']}" for a in alarms[:4]]
        confidence = clamp(0.62 + len(critical) * 0.12, 0.62, 0.95)
    else:
        summary = "未发现活跃关键告警，根因更可能来自覆盖、干扰、容量或邻区参数。"
        evidence = ["告警库近 72 小时无严重告警"]
        confidence = 0.72
    return standard_output(summary, evidence, confidence, ["root_cause_ranker"], alarms=alarms)


def skill_coverage_analysis(ctx: ExecutionContext, payload: dict[str, Any]) -> dict[str, Any]:
    summary_data = ctx.memory.get("kpi_fetch", {}).get("kpi_summary", {})
    params = ctx.memory.get("cell_profile", {}).get("params") or {}
    rsrp = summary_data.get("rsrp", {})
    sinr = summary_data.get("sinr", {})
    drop = summary_data.get("drop_rate", {})
    score = 0.0
    evidence = []
    if rsrp.get("avg", 0) < -105:
        score += 0.45
        evidence.append(f"RSRP 均值 {rsrp['avg']} dBm，低于 -105 dBm 弱覆盖阈值")
    if rsrp.get("min", 0) < -115:
        score += 0.15
        evidence.append(f"RSRP 最差 {rsrp['min']} dBm，存在低电平栅格")
    if sinr.get("avg", 99) < 8:
        score += 0.15
        evidence.append(f"SINR 均值 {sinr['avg']} dB，覆盖边缘质量差")
    if drop.get("avg", 0) > 1.2:
        score += 0.1
        evidence.append(f"掉线率均值 {drop['avg']}%，弱覆盖可能引发保持性问题")
    if params and params.get("tilt", 0) > 6.5:
        score += 0.08
        evidence.append(f"机械/电子下倾 {params['tilt']}，需核查覆盖收缩")
    confidence = clamp(score, 0.12, 0.94)
    label = "疑似弱覆盖/覆盖失衡" if confidence >= 0.55 else "覆盖证据不足"
    return standard_output(label, evidence or ["覆盖指标未触发主要阈值"], confidence, ["root_cause_ranker"], analysis_type="coverage", score=confidence)


def skill_interference_analysis(ctx: ExecutionContext, payload: dict[str, Any]) -> dict[str, Any]:
    summary_data = ctx.memory.get("kpi_fetch", {}).get("kpi_summary", {})
    neighbors = ctx.memory.get("cell_profile", {}).get("neighbors", [])
    sinr = summary_data.get("sinr", {})
    uli = summary_data.get("ul_interference", {})
    score = 0.0
    evidence = []
    if sinr.get("avg", 99) < 8:
        score += 0.35
        evidence.append(f"SINR 均值 {sinr['avg']} dB，明显偏低")
    if uli.get("avg", -120) > -100:
        score += 0.35
        evidence.append(f"上行干扰均值 {uli['avg']} dBm，高于 -100 dBm 风险线")
    conflicts = [n for n in neighbors if n.get("pci_mod3_conflict")]
    if conflicts:
        score += 0.2
        evidence.append(f"发现 MOD3/PCI 冲突邻区: {', '.join(n['neighbor_name'] for n in conflicts)}")
    confidence = clamp(score, 0.1, 0.95)
    label = "疑似上行/邻区干扰" if confidence >= 0.55 else "干扰证据不足"
    return standard_output(label, evidence or ["SINR、上行干扰和 PCI 冲突未形成强证据"], confidence, ["root_cause_ranker"], analysis_type="interference", score=confidence)


def skill_capacity_analysis(ctx: ExecutionContext, payload: dict[str, Any]) -> dict[str, Any]:
    summary_data = ctx.memory.get("kpi_fetch", {}).get("kpi_summary", {})
    prb = summary_data.get("prb_util", {})
    users = summary_data.get("users", {})
    throughput = summary_data.get("dl_throughput", {})
    access = summary_data.get("access_success", {})
    score = 0.0
    evidence = []
    if prb.get("avg", 0) > 75:
        score += 0.38
        evidence.append(f"PRB 均值 {prb['avg']}%，超过 75% 高负荷阈值")
    if prb.get("max", 0) > 90:
        score += 0.18
        evidence.append(f"PRB 峰值 {prb['max']}%，晚高峰逼近满载")
    if users.get("max", 0) > 450:
        score += 0.18
        evidence.append(f"用户数峰值 {users['max']}，存在热点拥塞")
    if throughput.get("avg", 999) < 45:
        score += 0.14
        evidence.append(f"下行吞吐均值 {throughput['avg']} Mbps，容量体验下降")
    if access.get("min", 100) < 97:
        score += 0.08
        evidence.append(f"接通率最低 {access['min']}%，拥塞影响接入")
    confidence = clamp(score, 0.12, 0.96)
    label = "疑似容量拥塞" if confidence >= 0.55 else "容量拥塞证据不足"
    return standard_output(label, evidence or ["PRB、用户数和吞吐未触发容量阈值"], confidence, ["root_cause_ranker"], analysis_type="capacity", score=confidence)


def skill_handover_analysis(ctx: ExecutionContext, payload: dict[str, Any]) -> dict[str, Any]:
    summary_data = ctx.memory.get("kpi_fetch", {}).get("kpi_summary", {})
    neighbors = ctx.memory.get("cell_profile", {}).get("neighbors", [])
    params = ctx.memory.get("cell_profile", {}).get("params") or {}
    hosr = summary_data.get("handover_success", {})
    drop = summary_data.get("drop_rate", {})
    score = 0.0
    evidence = []
    if hosr.get("avg", 100) < 94:
        score += 0.42
        evidence.append(f"切换成功率均值 {hosr['avg']}%，低于 94%")
    if hosr.get("min", 100) < 88:
        score += 0.15
        evidence.append(f"切换成功率最低 {hosr['min']}%，存在局部失败")
    if drop.get("avg", 0) > 1.2:
        score += 0.12
        evidence.append(f"掉线率均值 {drop['avg']}%，切换异常可能引发掉话")
    missing = [n for n in neighbors if n.get("missing_suspected")]
    weak_neighbors = [n for n in neighbors if n.get("handover_success_rate", 100) < 90]
    if missing:
        score += 0.18
        evidence.append(f"疑似邻区漏配: {', '.join(n['neighbor_name'] for n in missing)}")
    if weak_neighbors:
        score += 0.08
        evidence.append(f"低切换成功邻区: {', '.join(n['neighbor_name'] for n in weak_neighbors[:2])}")
    if params and params.get("a3_offset", 0) > 3.5:
        score += 0.06
        evidence.append(f"A3 offset {params['a3_offset']}，可能导致切换偏迟")
    confidence = clamp(score, 0.1, 0.95)
    label = "疑似切换/邻区参数异常" if confidence >= 0.55 else "切换证据不足"
    return standard_output(label, evidence or ["切换成功率和邻区关系未触发主要阈值"], confidence, ["root_cause_ranker"], analysis_type="handover", score=confidence)


def skill_root_cause_ranker(ctx: ExecutionContext, payload: dict[str, Any]) -> dict[str, Any]:
    analysis_names = ["coverage_analysis", "interference_analysis", "capacity_analysis", "handover_analysis"]
    candidates = []
    alarm_output = ctx.memory.get("alarm_correlate", {})
    alarms = alarm_output.get("alarms", [])
    alarm_boost = 0.0
    alarm_tags = []
    for alarm in alarms:
        atype = alarm["alarm_type"]
        alarm_tags.append(atype)
        if alarm["level"] in ("critical", "major"):
            alarm_boost += 0.08
    for name in analysis_names:
        item = ctx.memory.get(name, {})
        analysis_type = item.get("analysis_type", name.replace("_analysis", ""))
        score = float(item.get("score", item.get("confidence", 0)))
        if analysis_type == "interference" and any("interference" in tag or "uplink" in tag for tag in alarm_tags):
            score += alarm_boost
        if analysis_type == "capacity" and any("prb" in tag for tag in alarm_tags):
            score += alarm_boost
        if analysis_type == "handover" and any("handover" in tag for tag in alarm_tags):
            score += alarm_boost
        if analysis_type == "coverage" and any("rsrp" in tag or "coverage" in tag for tag in alarm_tags):
            score += alarm_boost
        candidates.append(
            {
                "cause": analysis_type,
                "title": {
                    "coverage": "覆盖问题",
                    "interference": "干扰问题",
                    "capacity": "容量拥塞",
                    "handover": "切换/邻区异常",
                }.get(analysis_type, analysis_type),
                "score": round(clamp(score, 0.0, 0.98), 3),
                "summary": item.get("summary", ""),
                "evidence": item.get("evidence", []),
            }
        )
    candidates.sort(key=lambda x: x["score"], reverse=True)
    top = candidates[0] if candidates else {"title": "未知", "score": 0, "evidence": []}
    confidence = weighted_score((top["score"], 0.7), (alarm_output.get("confidence", 0.5), 0.3))
    summary = f"首要根因判断为{top['title']}，综合置信度 {round(confidence * 100)}%。"
    evidence = top["evidence"][:3] + [f"告警关联: {alarm_output.get('summary', '无')}"]
    return standard_output(summary, evidence, confidence, ["solution_generator"], root_causes=candidates)


def plan_for_cause(cause: str, cell: dict[str, Any], params: dict[str, Any] | None) -> dict[str, Any]:
    name = cell["name"]
    if cause == "coverage":
        return {
            "plan_type": "覆盖优化",
            "priority": "high",
            "actions": [
                {"type": "parameter", "target": name, "command": "RS功率 +1 dB，低峰期灰度生效"},
                {"type": "antenna", "target": name, "command": "复核方位角与下倾，必要时下倾减少 1 度"},
                {"type": "drive-test", "target": name, "command": "对投诉栅格开展 CQT/DT 复测"},
            ],
            "risks": ["功率提升可能增加邻区干扰", "天馈调整需现场施工窗口"],
        }
    if cause == "interference":
        return {
            "plan_type": "干扰治理",
            "priority": "high",
            "actions": [
                {"type": "pci", "target": name, "command": "核查 MOD3 冲突并重规划 PCI"},
                {"type": "spectrum", "target": name, "command": "发起上行干扰扫频定位"},
                {"type": "neighbor", "target": name, "command": "优化强干扰邻区 CIO，降低乒乓切换"},
            ],
            "risks": ["PCI 调整需同步邻区关系", "扫频定位期间需跨团队协同"],
        }
    if cause == "capacity":
        return {
            "plan_type": "容量优化",
            "priority": "high",
            "actions": [
                {"type": "feature", "target": name, "command": "开启基于 PRB 的负荷均衡"},
                {"type": "parameter", "target": name, "command": "热点邻区 CIO +1 dB，分流边缘用户"},
                {"type": "capacity", "target": name, "command": "评估扩容载波或开通小站补盲"},
            ],
            "risks": ["分流可能推高邻区负荷", "扩容涉及资源与工程周期"],
        }
    if cause == "handover":
        return {
            "plan_type": "切换优化",
            "priority": "medium",
            "actions": [
                {"type": "neighbor", "target": name, "command": "补齐疑似漏配邻区并复核双向关系"},
                {"type": "parameter", "target": name, "command": "A3 offset 下调 0.5 dB，缩短迟切换区间"},
                {"type": "kpi-watch", "target": name, "command": "观察切换成功率、掉线率和乒乓切换 24 小时"},
            ],
            "risks": ["A3 调整过大可能引起乒乓切换", "邻区变更需避开重大保障时段"],
        }
    return {
        "plan_type": "综合优化",
        "priority": "medium",
        "actions": [{"type": "analysis", "target": name, "command": "补充现场测试和历史案例比对"}],
        "risks": ["根因证据不足，建议人工复核后执行"],
    }


def skill_solution_generator(ctx: ExecutionContext, payload: dict[str, Any]) -> dict[str, Any]:
    cell = ctx.memory.get("cell_profile", {}).get("cell") or find_cell(ctx.conn, ctx.query, ctx.cell_id)
    params = ctx.memory.get("cell_profile", {}).get("params")
    roots = ctx.memory.get("root_cause_ranker", {}).get("root_causes", [])
    top = roots[0] if roots else {"cause": "unknown", "title": "未知", "score": 0.0}
    template = plan_for_cause(top["cause"], cell, params)
    plan_id = f"PLAN-{uuid.uuid4().hex[:10].upper()}"
    risk_level = "high" if top["score"] > 0.82 and template["plan_type"] in ("干扰治理", "容量优化") else "medium"
    plan = {
        "plan_id": plan_id,
        "trace_id": ctx.trace_id,
        "cell_id": cell["cell_id"],
        "cell_name": cell["name"],
        "root_cause": top["cause"],
        "plan_type": template["plan_type"],
        "priority": template["priority"],
        "status": "draft",
        "risk_level": risk_level,
        "summary": f"针对{cell['name']}的{top['title']}，建议执行{template['plan_type']}方案，先灰度后观察。",
        "actions": template["actions"],
        "risks": template["risks"],
        "created_at": now_iso(),
    }
    save_plan(ctx.conn, plan)
    evidence = [action["command"] for action in plan["actions"][:3]]
    return standard_output(plan["summary"], evidence, 0.88, ["risk_guard"], plan=plan, plans=[plan])


def skill_risk_guard(ctx: ExecutionContext, payload: dict[str, Any]) -> dict[str, Any]:
    plan = ctx.memory.get("solution_generator", {}).get("plan")
    if not plan:
        plans = list_plans(ctx.conn, ctx.cell_id, limit=1)
        plan = plans[0] if plans else None
    if not plan:
        return standard_output("没有可审阅的方案。", [], 0.0, ["solution_generator"], risk=None)
    risk_points = list(plan.get("risks", []))
    level = plan.get("risk_level", "medium")
    roots = ctx.memory.get("root_cause_ranker", {}).get("root_causes", [])
    if roots and roots[0]["score"] < 0.55:
        level = "high"
        risk_points.append("根因置信度不足，必须人工复核")
    if any(action["type"] in ("pci", "antenna", "capacity") for action in plan.get("actions", [])):
        risk_points.append("涉及跨小区影响，建议低峰窗口执行并保留回退参数")
    approval_required = level in ("high", "critical") or any(action["type"] in ("pci", "antenna", "capacity") for action in plan.get("actions", []))
    review = {
        "plan_id": plan["plan_id"],
        "risk_level": level,
        "approval_required": approval_required,
        "risk_points": risk_points,
        "guardrails": ["模拟下发默认开启", "真实网元下发需二次确认", "执行后 24 小时 KPI 自动观察"],
    }
    summary = f"风险校验完成：风险等级 {level}，{'需要' if approval_required else '不需要'}人工审批。"
    return standard_output(summary, risk_points, 0.9, ["plan_dispatch" if ctx.intent.get("dispatch") else "report_writer"], risk=review)


def skill_plan_dispatch(ctx: ExecutionContext, payload: dict[str, Any]) -> dict[str, Any]:
    plan = ctx.memory.get("solution_generator", {}).get("plan")
    if not plan:
        plan_id = ctx.intent.get("plan_id")
        plan = get_plan(ctx.conn, plan_id) if plan_id else None
    if not plan:
        return standard_output("未找到可下发方案。", [], 0.0, ["solution_generator"], dispatch=None)
    risk = ctx.memory.get("risk_guard", {}).get("risk", {})
    status = "pending_approval" if risk.get("approval_required") else "simulated_dispatched"
    log = {
        "job_id": f"JOB-{uuid.uuid4().hex[:10].upper()}",
        "plan_id": plan["plan_id"],
        "cell_id": plan["cell_id"],
        "mode": "simulation",
        "status": status,
        "submitted_at": now_iso(),
        "operator": "NetOptClaw",
        "message": "已写入模拟下发队列，真实下发需人工确认。" if status == "pending_approval" else "模拟下发完成，进入效果观察。",
    }
    save_dispatch(ctx.conn, log)
    return standard_output(log["message"], [f"job_id={log['job_id']}", f"plan_id={log['plan_id']}", f"status={status}"], 0.93, ["effect_evaluator"], dispatch=log)


def skill_effect_evaluator(ctx: ExecutionContext, payload: dict[str, Any]) -> dict[str, Any]:
    cell_id = ctx.cell_id
    if not cell_id:
        return standard_output("无法评估，缺少小区。", [], 0.0, ["cell_profile"])
    existing = get_latest_evaluation(ctx.conn, cell_id)
    if existing:
        return standard_output(existing["summary"], [f"评分 {existing['score']}", f"报告 {existing['report_id']}"], 0.89, ["report_writer"], evaluation=existing)
    kpis = fetch_kpi(ctx.conn, cell_id, hours=48)
    before = kpi_summary(kpis[:24])
    after = kpi_summary(kpis[24:])
    if not before or not after:
        return standard_output("KPI 数据不足，无法完成效果评估。", [], 0.2, ["kpi_fetch"], evaluation=None)
    before_flat = {k: v["avg"] for k, v in before.items() if isinstance(v, dict)}
    after_flat = {k: v["avg"] for k, v in after.items() if isinstance(v, dict)}
    prb_gain = before_flat.get("prb_util", 0) - after_flat.get("prb_util", 0)
    speed_gain = after_flat.get("dl_throughput", 0) - before_flat.get("dl_throughput", 0)
    access_gain = after_flat.get("access_success", 0) - before_flat.get("access_success", 0)
    drop_gain = before_flat.get("drop_rate", 0) - after_flat.get("drop_rate", 0)
    score = round(clamp(65 + prb_gain * 0.8 + speed_gain * 0.5 + access_gain * 4 + drop_gain * 5, 0, 98), 1)
    summary = f"效果评估得分 {score}：PRB变化 {round(prb_gain, 2)}pp，吞吐变化 {round(speed_gain, 2)}Mbps，掉线率变化 {round(drop_gain, 2)}pp。"
    report = {
        "report_id": f"EVAL-{uuid.uuid4().hex[:10].upper()}",
        "cell_id": cell_id,
        "plan_id": (ctx.memory.get("solution_generator", {}).get("plan") or {}).get("plan_id"),
        "before_metrics": before_flat,
        "after_metrics": after_flat,
        "score": score,
        "summary": summary,
        "created_at": now_iso(),
    }
    save_evaluation(ctx.conn, report)
    return standard_output(summary, [f"接入成功率变化 {round(access_gain, 2)}pp", f"下行吞吐变化 {round(speed_gain, 2)}Mbps"], 0.86, ["report_writer"], evaluation=report)


def skill_report_writer(ctx: ExecutionContext, payload: dict[str, Any]) -> dict[str, Any]:
    cell = ctx.memory.get("cell_profile", {}).get("cell") or find_cell(ctx.conn, ctx.query, ctx.cell_id)
    tickets = ctx.memory.get("ticket_query", {}).get("tickets", [])
    roots = ctx.memory.get("root_cause_ranker", {}).get("root_causes", [])
    plan = ctx.memory.get("solution_generator", {}).get("plan")
    risk = ctx.memory.get("risk_guard", {}).get("risk")
    evaluation = ctx.memory.get("effect_evaluator", {}).get("evaluation")
    dispatch = ctx.memory.get("plan_dispatch", {}).get("dispatch")
    top = roots[0] if roots else None
    lines = [
        f"已完成 {cell['name'] if cell else '目标小区'} 的网优智能分析。",
        f"工单侧：共关联 {len(tickets)} 条近期工单。" if tickets else "工单侧：未要求或未发现重点工单。",
    ]
    if top:
        lines.append(f"根因侧：首要根因是{top['title']}，置信度 {round(top['score'] * 100)}%，证据为：{'；'.join(top['evidence'][:2])}。")
    if plan:
        lines.append(f"方案侧：推荐{plan['plan_type']}，包含 {len(plan['actions'])} 个动作，方案号 {plan['plan_id']}。")
    if risk:
        lines.append(f"风控侧：风险等级 {risk['risk_level']}，{'需要人工审批' if risk['approval_required'] else '可模拟下发'}。")
    if dispatch:
        lines.append(f"下发侧：{dispatch['message']}")
    if evaluation:
        lines.append(f"效果评估：{evaluation['summary']}")
    answer = "\n".join(lines)
    return standard_output(answer, lines[1:], 0.91, [], answer=answer)


def build_registry() -> SkillRegistry:
    registry = SkillRegistry()
    specs = [
        ("ticket_query", "查询小区工单、投诉与派单状态", skill_ticket_query),
        ("cell_profile", "查询小区画像、参数和邻区关系", skill_cell_profile),
        ("kpi_fetch", "获取最近 KPI 趋势与统计摘要", skill_kpi_fetch),
        ("alarm_correlate", "关联告警并判断告警影响", skill_alarm_correlate),
        ("coverage_analysis", "分析弱覆盖、越区覆盖和覆盖失衡", skill_coverage_analysis),
        ("interference_analysis", "分析上行干扰、邻区干扰和 PCI/MOD3 冲突", skill_interference_analysis),
        ("capacity_analysis", "分析 PRB、用户数和吞吐容量瓶颈", skill_capacity_analysis),
        ("handover_analysis", "分析切换失败、邻区漏配和参数异常", skill_handover_analysis),
        ("root_cause_ranker", "融合多证据生成根因 TopN", skill_root_cause_ranker),
        ("solution_generator", "生成无线优化方案和执行动作", skill_solution_generator),
        ("risk_guard", "进行方案风险校验和审批判断", skill_risk_guard),
        ("plan_dispatch", "模拟方案下发并记录执行日志", skill_plan_dispatch),
        ("effect_evaluator", "对比 KPI 并输出效果评估", skill_effect_evaluator),
        ("report_writer", "汇总为网优工程师可读报告", skill_report_writer),
    ]
    for name, description, func in specs:
        registry.register(
            FunctionSkill(
                name=name,
                description=description,
                func=func,
                input_schema={"type": "object", "properties": {"query": {"type": "string"}, "cell_id": {"type": "string"}}},
                output_schema={"type": "object", "required": ["summary", "evidence", "confidence", "next_recommended_skills"]},
            )
        )
    return registry
