from __future__ import annotations

import sqlite3
from statistics import mean
from typing import Any

from .database import row_to_dict, rows_to_dicts
from .utils import dumps, now_iso


def find_cell(conn: sqlite3.Connection, query: str | None = None, cell_id: str | None = None) -> dict[str, Any] | None:
    if cell_id:
        exact = row_to_dict(conn.execute("SELECT * FROM cells WHERE cell_id = ?", (cell_id,)).fetchone())
        if exact:
            return exact
        named = row_to_dict(conn.execute("SELECT * FROM cells WHERE name = ?", (cell_id,)).fetchone())
        if named:
            return named
    if not query:
        return row_to_dict(conn.execute("SELECT * FROM cells ORDER BY cell_id LIMIT 1").fetchone())
    text = query.replace("小区", "").strip()
    rows = conn.execute("SELECT * FROM cells ORDER BY cell_id").fetchall()
    for row in rows:
        cell = row_to_dict(row) or {}
        if cell["name"] in query or cell["cell_id"] in query or text in cell["name"]:
            return cell
    for row in rows:
        cell = row_to_dict(row) or {}
        if any(part and part in cell["name"] for part in text.split("_")):
            return cell
    return row_to_dict(rows[0]) if rows else None


def list_cells(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows_to_dicts(conn.execute("SELECT * FROM cells ORDER BY cell_id").fetchall())


def list_tickets(conn: sqlite3.Connection, cell_id: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    if cell_id:
        rows = conn.execute(
            "SELECT t.*, c.name AS cell_name FROM tickets t JOIN cells c ON c.cell_id = t.cell_id WHERE t.cell_id = ? ORDER BY t.created_at DESC LIMIT ?",
            (cell_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT t.*, c.name AS cell_name FROM tickets t JOIN cells c ON c.cell_id = t.cell_id ORDER BY t.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return rows_to_dicts(rows)


def fetch_kpi(conn: sqlite3.Connection, cell_id: str, hours: int = 24) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM kpi_hourly WHERE cell_id = ? ORDER BY ts DESC LIMIT ?",
        (cell_id, hours),
    ).fetchall()
    return list(reversed(rows_to_dicts(rows)))


def kpi_summary(kpis: list[dict[str, Any]]) -> dict[str, Any]:
    if not kpis:
        return {}
    fields = ["rsrp", "sinr", "drop_rate", "access_success", "handover_success", "prb_util", "users", "ul_interference", "dl_throughput"]
    summary: dict[str, Any] = {}
    for field in fields:
        values = [float(row[field]) for row in kpis if row.get(field) is not None]
        if values:
            summary[field] = {
                "avg": round(mean(values), 2),
                "min": round(min(values), 2),
                "max": round(max(values), 2),
                "latest": round(float(kpis[-1][field]), 2),
            }
    return summary


def fetch_alarms(conn: sqlite3.Connection, cell_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM alarms WHERE cell_id = ? ORDER BY occurred_at DESC LIMIT ?",
        (cell_id, limit),
    ).fetchall()
    return rows_to_dicts(rows)


def fetch_neighbors(conn: sqlite3.Connection, cell_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT n.*, c.name AS neighbor_name
        FROM neighbors n
        JOIN cells c ON c.cell_id = n.neighbor_cell_id
        WHERE n.cell_id = ?
        ORDER BY n.distance_m
        """,
        (cell_id,),
    ).fetchall()
    return rows_to_dicts(rows)


def fetch_params(conn: sqlite3.Connection, cell_id: str) -> dict[str, Any] | None:
    return row_to_dict(conn.execute("SELECT * FROM cell_params WHERE cell_id = ?", (cell_id,)).fetchone())


def save_plan(conn: sqlite3.Connection, plan: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO optimization_plans (
            plan_id, trace_id, cell_id, root_cause, plan_type, priority, status,
            risk_level, summary, actions, risks, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan["plan_id"],
            plan.get("trace_id"),
            plan["cell_id"],
            plan["root_cause"],
            plan["plan_type"],
            plan["priority"],
            plan["status"],
            plan["risk_level"],
            plan["summary"],
            dumps(plan["actions"]),
            dumps(plan["risks"]),
            plan["created_at"],
        ),
    )
    conn.commit()


def get_plan(conn: sqlite3.Connection, plan_id: str) -> dict[str, Any] | None:
    return row_to_dict(conn.execute("SELECT * FROM optimization_plans WHERE plan_id = ?", (plan_id,)).fetchone())


def list_plans(conn: sqlite3.Connection, cell_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    if cell_id:
        rows = conn.execute(
            "SELECT * FROM optimization_plans WHERE cell_id = ? ORDER BY created_at DESC LIMIT ?",
            (cell_id, limit),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM optimization_plans ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return rows_to_dicts(rows)


def save_dispatch(conn: sqlite3.Connection, log: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO dispatch_logs (job_id, plan_id, cell_id, mode, status, submitted_at, operator, message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            log["job_id"],
            log["plan_id"],
            log["cell_id"],
            log["mode"],
            log["status"],
            log["submitted_at"],
            log["operator"],
            log["message"],
        ),
    )
    conn.execute("UPDATE optimization_plans SET status = ? WHERE plan_id = ?", (log["status"], log["plan_id"]))
    conn.commit()


def list_dispatch_logs(conn: sqlite3.Connection, cell_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    if cell_id:
        rows = conn.execute(
            "SELECT * FROM dispatch_logs WHERE cell_id = ? ORDER BY submitted_at DESC LIMIT ?",
            (cell_id, limit),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM dispatch_logs ORDER BY submitted_at DESC LIMIT ?", (limit,)).fetchall()
    return rows_to_dicts(rows)


def save_evaluation(conn: sqlite3.Connection, report: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO evaluation_reports
        (report_id, cell_id, plan_id, before_metrics, after_metrics, score, summary, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report["report_id"],
            report["cell_id"],
            report.get("plan_id"),
            dumps(report["before_metrics"]),
            dumps(report["after_metrics"]),
            report["score"],
            report["summary"],
            report["created_at"],
        ),
    )
    conn.commit()


def get_latest_evaluation(conn: sqlite3.Connection, cell_id: str) -> dict[str, Any] | None:
    return row_to_dict(
        conn.execute("SELECT * FROM evaluation_reports WHERE cell_id = ? ORDER BY created_at DESC LIMIT 1", (cell_id,)).fetchone()
    )


def save_trace_header(
    conn: sqlite3.Connection,
    trace_id: str,
    query: str,
    cell_id: str | None,
    intent: str,
    status: str,
    trace: list[dict[str, Any]],
    answer: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO traces
        (trace_id, query, cell_id, intent, status, started_at, finished_at, answer, trace)
        VALUES (
            ?,
            ?,
            ?,
            ?,
            ?,
            COALESCE((SELECT started_at FROM traces WHERE trace_id = ?), ?),
            CASE WHEN ? IN ('completed','failed') THEN ? ELSE NULL END,
            ?,
            ?
        )
        """,
        (trace_id, query, cell_id, intent, status, trace_id, now_iso(), status, now_iso(), answer, dumps(trace)),
    )
    conn.commit()


def save_skill_run(conn: sqlite3.Connection, run: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO skill_runs
        (run_id, trace_id, skill_name, status, started_at, finished_at, duration_ms, input_json, output_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run["run_id"],
            run["trace_id"],
            run["skill_name"],
            run["status"],
            run["started_at"],
            run["finished_at"],
            run["duration_ms"],
            dumps(run["input"]),
            dumps(run["output"]),
        ),
    )
    conn.commit()


def get_trace(conn: sqlite3.Connection, trace_id: str) -> dict[str, Any] | None:
    trace = row_to_dict(conn.execute("SELECT * FROM traces WHERE trace_id = ?", (trace_id,)).fetchone())
    if not trace:
        return None
    rows = conn.execute("SELECT * FROM skill_runs WHERE trace_id = ? ORDER BY started_at", (trace_id,)).fetchall()
    trace["skill_runs"] = rows_to_dicts(rows)
    return trace
