from __future__ import annotations

import json
import mimetypes
import sqlite3
import argparse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from netopt.database import DB_PATH, connect, init_db
from netopt.orchestrator import orchestrator
from netopt.planner import plan_query
from netopt.repositories import (
    fetch_kpi,
    find_cell,
    get_latest_evaluation,
    get_plan,
    list_cells,
    list_dispatch_logs,
    list_plans,
    list_tickets,
    save_dispatch,
)
from netopt.seed import seed_database
from netopt.utils import now_iso


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"


def ensure_ready() -> None:
    with connect() as conn:
        init_db(conn)
        count = conn.execute("SELECT COUNT(*) FROM cells").fetchone()[0]
    if count == 0:
        seed_database(force=True)


def json_bytes(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def sse_frame(event: dict) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")


class NetOptHandler(BaseHTTPRequestHandler):
    server_version = "NetOptClaw/0.1"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{now_iso()}] {self.address_string()} {format % args}")

    def send_json(self, data: object, status: int = 200) -> None:
        body = json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, body: str, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/api/health":
                self.send_json({"ok": True, "db": str(DB_PATH), "time": now_iso()})
                return
            if path == "/api/skills":
                self.send_json({"skills": orchestrator.registry.list()})
                return
            if path == "/api/cells":
                with connect() as conn:
                    self.send_json({"cells": list_cells(conn)})
                return
            if path == "/api/tickets":
                with connect() as conn:
                    self.send_json({"tickets": list_tickets(conn, query.get("cell_id", [None])[0], 50)})
                return
            if path == "/api/dashboard":
                self.send_json(orchestrator.dashboard_data(query.get("cell_id", [None])[0]))
                return
            if path.startswith("/api/traces/"):
                trace_id = path.rsplit("/", 1)[-1]
                trace = orchestrator.get_trace(trace_id)
                self.send_json(trace or {"error": "trace not found"}, 200 if trace else 404)
                return
            if path.startswith("/api/evaluation/"):
                cell_id = path.rsplit("/", 1)[-1]
                with connect() as conn:
                    report = get_latest_evaluation(conn, cell_id)
                self.send_json({"evaluation": report})
                return
            if path.startswith("/api/kpi/"):
                cell_id = path.rsplit("/", 1)[-1]
                hours = int(query.get("hours", ["24"])[0])
                with connect() as conn:
                    self.send_json({"kpi": fetch_kpi(conn, cell_id, hours)})
                return
            self.serve_static(path)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/seed":
                seed_database(force=True)
                self.send_json({"ok": True, "message": "mock data generated"})
                return
            if path == "/api/chat":
                body = self.read_body()
                query = body.get("query", "")
                if not query:
                    self.send_json({"error": "query is required"}, 400)
                    return
                self.send_json(orchestrator.run(query))
                return
            if path == "/api/chat/stream":
                body = self.read_body()
                query = body.get("query", "")
                if not query:
                    self.send_json({"error": "query is required"}, 400)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                for event in orchestrator.stream(query):
                    self.wfile.write(sse_frame(event))
                    self.wfile.flush()
                    if event["type"] == "planner_start":
                        time.sleep(0.28)
                    elif event["type"] == "planner_done":
                        time.sleep(0.42)
                    elif event["type"] == "reasoning_step":
                        time.sleep(0.2)
                    elif event["type"] == "skill_start":
                        time.sleep(0.28)
                    elif event["type"] == "skill_done":
                        time.sleep(0.34)
                return
            if path == "/api/plan":
                body = self.read_body()
                query = body.get("query", "")
                if not query:
                    self.send_json({"error": "query is required"}, 400)
                    return
                intent = plan_query(query)
                self.send_json(
                    {
                        "intent": intent,
                        "dag": [
                            {
                                "id": index + 1,
                                "skill": skill,
                                "depends_on": [] if index == 0 else [intent["skills"][index - 1]],
                                "runtime": "whitelist-python-skill",
                            }
                            for index, skill in enumerate(intent["skills"])
                        ],
                        "policy": {
                            "skill_mode": "white_list_only",
                            "dispatch_mode": "simulation_first",
                            "trace_enabled": True,
                            "human_approval": "required_for_high_risk",
                        },
                    }
                )
                return
            if path.startswith("/api/dispatch/"):
                plan_id = path.rsplit("/", 1)[-1]
                with connect() as conn:
                    plan = get_plan(conn, plan_id)
                    if not plan:
                        self.send_json({"error": "plan not found"}, 404)
                        return
                    log = {
                        "job_id": f"JOB-MANUAL-{now_iso().replace(':', '').replace('-', '')}",
                        "plan_id": plan_id,
                        "cell_id": plan["cell_id"],
                        "mode": "simulation",
                        "status": "simulated_dispatched",
                        "submitted_at": now_iso(),
                        "operator": "UI",
                        "message": "UI 触发模拟下发完成，真实下发需人工审批。",
                    }
                    save_dispatch(conn, log)
                    self.send_json({"dispatch": log})
                return
            self.send_json({"error": "not found"}, 404)
        except sqlite3.Error as exc:
            self.send_json({"error": f"database error: {exc}"}, 500)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def serve_static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        safe = Path(path.lstrip("/"))
        file_path = (WEB_DIR / safe).resolve()
        if not str(file_path).startswith(str(WEB_DIR.resolve())) or not file_path.exists() or not file_path.is_file():
            self.send_text("Not Found", 404)
            return
        ctype = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    ensure_ready()
    server = ThreadingHTTPServer((host, port), NetOptHandler)
    print(f"NetOptClaw running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run NetOptClaw demo server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    run(args.host, args.port)
