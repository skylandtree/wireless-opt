from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .utils import dumps, loads


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "netopt.sqlite3"


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key in ("evidence", "risks", "actions", "metrics", "before_metrics", "after_metrics", "trace"):
        if key in data and isinstance(data[key], str):
            data[key] = loads(data[key], [])
    return data


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(row) or {} for row in rows]


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cells (
            cell_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            site_id TEXT NOT NULL,
            city TEXT NOT NULL,
            district TEXT NOT NULL,
            rat TEXT NOT NULL,
            band TEXT NOT NULL,
            vendor TEXT NOT NULL,
            longitude REAL NOT NULL,
            latitude REAL NOT NULL,
            scenario TEXT NOT NULL,
            scenario_label TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id TEXT PRIMARY KEY,
            cell_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            severity TEXT NOT NULL,
            status TEXT NOT NULL,
            category TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(cell_id) REFERENCES cells(cell_id)
        );

        CREATE TABLE IF NOT EXISTS kpi_hourly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cell_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            rsrp REAL NOT NULL,
            sinr REAL NOT NULL,
            drop_rate REAL NOT NULL,
            access_success REAL NOT NULL,
            handover_success REAL NOT NULL,
            prb_util REAL NOT NULL,
            users INTEGER NOT NULL,
            ul_interference REAL NOT NULL,
            dl_throughput REAL NOT NULL,
            FOREIGN KEY(cell_id) REFERENCES cells(cell_id)
        );

        CREATE TABLE IF NOT EXISTS alarms (
            alarm_id TEXT PRIMARY KEY,
            cell_id TEXT NOT NULL,
            alarm_type TEXT NOT NULL,
            level TEXT NOT NULL,
            status TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            cleared_at TEXT,
            description TEXT NOT NULL,
            FOREIGN KEY(cell_id) REFERENCES cells(cell_id)
        );

        CREATE TABLE IF NOT EXISTS neighbors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cell_id TEXT NOT NULL,
            neighbor_cell_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            distance_m INTEGER NOT NULL,
            handover_attempts INTEGER NOT NULL,
            handover_success_rate REAL NOT NULL,
            pci_mod3_conflict INTEGER NOT NULL DEFAULT 0,
            missing_suspected INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(cell_id) REFERENCES cells(cell_id),
            FOREIGN KEY(neighbor_cell_id) REFERENCES cells(cell_id)
        );

        CREATE TABLE IF NOT EXISTS cell_params (
            cell_id TEXT PRIMARY KEY,
            pci INTEGER NOT NULL,
            rs_power REAL NOT NULL,
            azimuth INTEGER NOT NULL,
            tilt REAL NOT NULL,
            a3_offset REAL NOT NULL,
            cio REAL NOT NULL,
            tac TEXT NOT NULL,
            FOREIGN KEY(cell_id) REFERENCES cells(cell_id)
        );

        CREATE TABLE IF NOT EXISTS optimization_plans (
            plan_id TEXT PRIMARY KEY,
            trace_id TEXT,
            cell_id TEXT NOT NULL,
            root_cause TEXT NOT NULL,
            plan_type TEXT NOT NULL,
            priority TEXT NOT NULL,
            status TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            summary TEXT NOT NULL,
            actions TEXT NOT NULL,
            risks TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(cell_id) REFERENCES cells(cell_id)
        );

        CREATE TABLE IF NOT EXISTS dispatch_logs (
            job_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL,
            cell_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            operator TEXT NOT NULL,
            message TEXT NOT NULL,
            FOREIGN KEY(plan_id) REFERENCES optimization_plans(plan_id),
            FOREIGN KEY(cell_id) REFERENCES cells(cell_id)
        );

        CREATE TABLE IF NOT EXISTS evaluation_reports (
            report_id TEXT PRIMARY KEY,
            cell_id TEXT NOT NULL,
            plan_id TEXT,
            before_metrics TEXT NOT NULL,
            after_metrics TEXT NOT NULL,
            score REAL NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(cell_id) REFERENCES cells(cell_id)
        );

        CREATE TABLE IF NOT EXISTS traces (
            trace_id TEXT PRIMARY KEY,
            query TEXT NOT NULL,
            cell_id TEXT,
            intent TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            answer TEXT,
            trace TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS skill_runs (
            run_id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            skill_name TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            duration_ms INTEGER NOT NULL,
            input_json TEXT NOT NULL,
            output_json TEXT NOT NULL,
            FOREIGN KEY(trace_id) REFERENCES traces(trace_id)
        );
        """
    )
    conn.commit()


def reset_db(conn: sqlite3.Connection) -> None:
    tables = [
        "skill_runs",
        "traces",
        "evaluation_reports",
        "dispatch_logs",
        "optimization_plans",
        "cell_params",
        "neighbors",
        "alarms",
        "kpi_hourly",
        "tickets",
        "cells",
    ]
    for table in tables:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def insert_json(conn: sqlite3.Connection, table: str, data: dict[str, Any]) -> None:
    encoded = {}
    for key, value in data.items():
        encoded[key] = dumps(value) if isinstance(value, (dict, list)) else value
    cols = ",".join(encoded.keys())
    placeholders = ",".join("?" for _ in encoded)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", list(encoded.values()))
