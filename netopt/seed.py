from __future__ import annotations

import random
from datetime import datetime, timedelta

from .database import connect, init_db, insert_json, reset_db
from .utils import CHINA_TZ, dumps, now_iso


CELL_SPECS = [
    ("CELL-001", "高科路_001", "coverage", "弱覆盖"),
    ("CELL-002", "高科路_002", "handover", "切换异常"),
    ("CELL-003", "陆家嘴_003", "capacity", "容量拥塞"),
    ("CELL-004", "陆家嘴_004", "interference", "上行干扰"),
    ("CELL-005", "世纪大道_005", "coverage", "重叠覆盖"),
    ("CELL-006", "世纪大道_006", "normal", "健康"),
    ("CELL-007", "人民广场_007", "capacity", "容量拥塞"),
    ("CELL-008", "人民广场_008", "alarm", "设备告警"),
    ("CELL-009", "外滩_009", "interference", "邻区干扰"),
    ("CELL-010", "外滩_010", "handover", "邻区漏配"),
    ("CELL-011", "张江_011", "normal", "健康"),
    ("CELL-012", "张江_012", "handover", "切换失败"),
    ("CELL-013", "徐家汇_013", "coverage", "越区覆盖"),
    ("CELL-014", "徐家汇_014", "capacity", "高负荷"),
    ("CELL-015", "虹桥_015", "alarm", "驻波告警"),
    ("CELL-016", "虹桥_016", "normal", "健康"),
    ("CELL-017", "五角场_017", "interference", "MOD3冲突"),
    ("CELL-018", "五角场_018", "coverage", "室内弱覆盖"),
    ("CELL-019", "莘庄_019", "capacity", "小区拥塞"),
    ("CELL-020", "莘庄_020", "alarm", "传输抖动"),
]


def metric_profile(scenario: str, hour: int, rng: random.Random) -> dict[str, float | int]:
    peak = 18 <= hour <= 22
    base = {
        "rsrp": rng.uniform(-96, -86),
        "sinr": rng.uniform(14, 22),
        "drop_rate": rng.uniform(0.15, 0.65),
        "access_success": rng.uniform(98.1, 99.8),
        "handover_success": rng.uniform(97.2, 99.4),
        "prb_util": rng.uniform(28, 58),
        "users": rng.randint(55, 180),
        "ul_interference": rng.uniform(-113, -104),
        "dl_throughput": rng.uniform(62, 148),
    }
    if peak:
        base["users"] += rng.randint(60, 180)
        base["prb_util"] += rng.uniform(12, 28)
        base["dl_throughput"] -= rng.uniform(8, 22)

    if scenario == "coverage":
        base["rsrp"] -= rng.uniform(12, 23)
        base["sinr"] -= rng.uniform(4, 9)
        base["drop_rate"] += rng.uniform(0.7, 2.1)
        base["access_success"] -= rng.uniform(1.1, 3.4)
        base["dl_throughput"] -= rng.uniform(15, 34)
    elif scenario == "interference":
        base["sinr"] -= rng.uniform(8, 16)
        base["ul_interference"] += rng.uniform(12, 23)
        base["drop_rate"] += rng.uniform(0.4, 1.3)
        base["dl_throughput"] -= rng.uniform(18, 42)
    elif scenario == "capacity":
        base["users"] += rng.randint(180, 420)
        base["prb_util"] += rng.uniform(26, 46)
        base["access_success"] -= rng.uniform(0.8, 2.8)
        base["dl_throughput"] -= rng.uniform(22, 55)
    elif scenario == "handover":
        base["handover_success"] -= rng.uniform(5.0, 14.0)
        base["drop_rate"] += rng.uniform(0.5, 1.8)
        base["sinr"] -= rng.uniform(1, 4)
    elif scenario == "alarm":
        base["drop_rate"] += rng.uniform(1.0, 3.3)
        base["access_success"] -= rng.uniform(1.4, 4.5)
        base["dl_throughput"] -= rng.uniform(20, 48)

    return {
        "rsrp": round(base["rsrp"], 2),
        "sinr": round(base["sinr"], 2),
        "drop_rate": round(max(0.05, base["drop_rate"]), 2),
        "access_success": round(min(99.9, max(88.0, base["access_success"])), 2),
        "handover_success": round(min(99.8, max(78.0, base["handover_success"])), 2),
        "prb_util": round(min(99.0, max(8.0, base["prb_util"])), 2),
        "users": int(max(10, base["users"])),
        "ul_interference": round(base["ul_interference"], 2),
        "dl_throughput": round(max(3.0, base["dl_throughput"]), 2),
    }


def seed_database(force: bool = True) -> None:
    rng = random.Random(20260611)
    conn = connect()
    init_db(conn)
    if force:
        reset_db(conn)

    now = datetime.now(CHINA_TZ).replace(minute=0, second=0, microsecond=0)
    city = "上海"
    districts = ["浦东", "黄浦", "徐汇", "长宁", "杨浦", "闵行"]

    for idx, (cell_id, name, scenario, scenario_label) in enumerate(CELL_SPECS, start=1):
        insert_json(
            conn,
            "cells",
            {
                "cell_id": cell_id,
                "name": name,
                "site_id": f"SITE-{idx:03d}",
                "city": city,
                "district": districts[idx % len(districts)],
                "rat": "5G" if idx % 3 else "4G",
                "band": ["n78", "n41", "B3", "B8"][idx % 4],
                "vendor": ["Huawei", "ZTE", "Ericsson"][idx % 3],
                "longitude": round(121.38 + idx * 0.011 + rng.random() * 0.006, 6),
                "latitude": round(31.12 + idx * 0.006 + rng.random() * 0.004, 6),
                "scenario": scenario,
                "scenario_label": scenario_label,
            },
        )
        insert_json(
            conn,
            "cell_params",
            {
                "cell_id": cell_id,
                "pci": 100 + idx * 7,
                "rs_power": round(15.2 + rng.random() * 3, 1),
                "azimuth": (idx * 35) % 360,
                "tilt": round(3.0 + rng.random() * 5, 1),
                "a3_offset": round(2.0 + rng.random() * 2.5, 1),
                "cio": round(rng.uniform(-2.0, 2.0), 1),
                "tac": f"31{idx:03d}",
            },
        )

    for idx, (cell_id, _, scenario, _) in enumerate(CELL_SPECS):
        for offset in (1, 2, 3):
            neighbor_idx = (idx + offset) % len(CELL_SPECS)
            neighbor_id = CELL_SPECS[neighbor_idx][0]
            conflict = 1 if scenario == "interference" and offset == 1 else 0
            missing = 1 if scenario == "handover" and offset == 2 else 0
            insert_json(
                conn,
                "neighbors",
                {
                    "cell_id": cell_id,
                    "neighbor_cell_id": neighbor_id,
                    "relation_type": "intra-frequency" if offset < 3 else "inter-frequency",
                    "distance_m": rng.randint(220, 1450),
                    "handover_attempts": rng.randint(90, 880),
                    "handover_success_rate": round(rng.uniform(82 if scenario == "handover" else 94, 99.3), 2),
                    "pci_mod3_conflict": conflict,
                    "missing_suspected": missing,
                },
            )

    ticket_templates = {
        "coverage": ("覆盖投诉", "用户反馈室内信号弱、视频加载慢，靠窗后有所改善"),
        "interference": ("速率波动", "晚高峰下载速率抖动明显，SINR 低且上行质差"),
        "capacity": ("高负荷投诉", "商圈晚高峰用户密集，视频和直播业务卡顿"),
        "handover": ("移动中掉线", "用户乘车经过该区域时出现掉线、回落或切换失败"),
        "alarm": ("设备异常", "近期小区告警频繁，业务体验突降"),
        "normal": ("体验咨询", "用户反馈偶发卡顿，需核查是否有无线侧异常"),
    }
    severity_map = {"coverage": "P2", "interference": "P1", "capacity": "P1", "handover": "P2", "alarm": "P1", "normal": "P3"}
    statuses = ["待分析", "分析中", "待处理", "已派单", "观察中"]
    for i in range(1, 101):
        cell_id, name, scenario, _ = CELL_SPECS[(i - 1) % len(CELL_SPECS)]
        title, desc = ticket_templates[scenario]
        created = now - timedelta(hours=rng.randint(1, 168), minutes=rng.randint(0, 59))
        insert_json(
            conn,
            "tickets",
            {
                "ticket_id": f"WO-202606-{i:04d}",
                "cell_id": cell_id,
                "title": f"{name}{title}",
                "description": desc,
                "severity": severity_map[scenario],
                "status": statuses[i % len(statuses)],
                "category": scenario,
                "source": ["客服投诉", "网管派单", "自动巡检", "VIP保障"][i % 4],
                "created_at": created.isoformat(),
            },
        )

    for cell_id, _, scenario, _ in CELL_SPECS:
        for h in range(7 * 24):
            ts = now - timedelta(hours=7 * 24 - h)
            metrics = metric_profile(scenario, ts.hour, rng)
            insert_json(conn, "kpi_hourly", {"cell_id": cell_id, "ts": ts.isoformat(), **metrics})

    alarm_templates = {
        "coverage": [("low_rsrp_cluster", "minor", "弱覆盖栅格聚集，MR 低电平占比异常")],
        "interference": [("uplink_interference", "major", "上行干扰抬升，疑似外部干扰或 MOD3 冲突")],
        "capacity": [("high_prb_utilization", "major", "PRB 利用率连续高位，用户数激增")],
        "handover": [("handover_failure", "major", "切换失败率异常，疑似邻区漏配或参数偏置")],
        "alarm": [("vswr", "critical", "驻波比异常，射频链路需排查"), ("transport_jitter", "major", "传输抖动导致业务质量下降")],
        "normal": [],
    }
    alarm_index = 1
    for cell_id, _, scenario, _ in CELL_SPECS:
        for alarm_type, level, desc in alarm_templates[scenario]:
            occurred = now - timedelta(hours=rng.randint(2, 72), minutes=rng.randint(0, 59))
            insert_json(
                conn,
                "alarms",
                {
                    "alarm_id": f"ALM-202606-{alarm_index:04d}",
                    "cell_id": cell_id,
                    "alarm_type": alarm_type,
                    "level": level,
                    "status": "active" if rng.random() > 0.35 else "cleared",
                    "occurred_at": occurred.isoformat(),
                    "cleared_at": None,
                    "description": desc,
                },
            )
            alarm_index += 1

    insert_json(
        conn,
        "optimization_plans",
        {
            "plan_id": "PLAN-DEMO-0001",
            "trace_id": "seed",
            "cell_id": "CELL-003",
            "root_cause": "capacity",
            "plan_type": "负荷均衡",
            "priority": "high",
            "status": "dispatched",
            "risk_level": "medium",
            "summary": "对陆家嘴_003开启负荷均衡并优化邻区 CIO，缓解晚高峰拥塞。",
            "actions": dumps([
                {"type": "parameter", "target": "CELL-003", "command": "CIO +1.0 dB 引导边缘用户切换"},
                {"type": "feature", "target": "CELL-003", "command": "开启基于 PRB 的负荷均衡"},
            ]),
            "risks": dumps(["可能导致邻区 PRB 上升，需观察 24 小时", "VIP 区域建议低峰期执行"]),
            "created_at": now_iso(),
        },
    )
    insert_json(
        conn,
        "evaluation_reports",
        {
            "report_id": "EVAL-DEMO-0001",
            "cell_id": "CELL-003",
            "plan_id": "PLAN-DEMO-0001",
            "before_metrics": {"prb_util": 94.1, "dl_throughput": 22.3, "access_success": 96.2, "drop_rate": 1.9},
            "after_metrics": {"prb_util": 77.8, "dl_throughput": 41.6, "access_success": 98.4, "drop_rate": 0.8},
            "score": 87.4,
            "summary": "下发后 PRB 利用率下降 16.3pp，下载速率提升 86.5%，接入成功率恢复至 98% 以上。",
            "created_at": now_iso(),
        },
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    seed_database(force=True)
    print("Seeded NetOptClaw mock data.")
