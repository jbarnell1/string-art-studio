"""SQLite storage for projects, runs, presets and build progress. Images and renders live on
disk under data/projects/<project id>/; the database holds metadata and JSON snapshots."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    has_image INTEGER NOT NULL DEFAULT 0,
    image_width INTEGER NOT NULL DEFAULT 0,
    image_height INTEGER NOT NULL DEFAULT 0,
    has_mask INTEGER NOT NULL DEFAULT 0,
    prep TEXT NOT NULL DEFAULT '{}',
    board TEXT NOT NULL DEFAULT '{}',
    engine TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL,
    engine TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    prep TEXT NOT NULL,
    board TEXT NOT NULL,
    params TEXT NOT NULL,
    metrics TEXT NOT NULL DEFAULT '{}',
    error_message TEXT NOT NULL DEFAULT '',
    group_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS runs_project ON runs(project_id, created_at);
CREATE TABLE IF NOT EXISTS presets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    board TEXT NOT NULL,
    engine TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS build_progress (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    step INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    device TEXT NOT NULL DEFAULT ''
);
"""


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    # Projects and presets store the engine settings as JSON; runs store the engine name ("a").
    for key in ("prep", "board", "engine", "params", "metrics"):
        if key in d and isinstance(d[key], str) and d[key][:1] in ("{", "["):
            try:
                d[key] = json.loads(d[key])
            except json.JSONDecodeError:
                d[key] = {}
    for key in ("has_image", "has_mask"):
        if key in d:
            d[key] = bool(d[key])
    return d


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def q(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [_row(r) for r in self._conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            return _row(self._conn.execute(sql, args).fetchone())

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # Projects ------------------------------------------------------------------------------

    def create_project(self, name: str, prep: dict, board: dict, engine: dict, notes: str = "") -> dict:
        pid = new_id()
        now = time.time()
        with self.tx() as c:
            c.execute(
                "INSERT INTO projects (id, name, notes, created_at, updated_at, prep, board, engine) VALUES (?,?,?,?,?,?,?,?)",
                (pid, name, notes, now, now, json.dumps(prep), json.dumps(board), json.dumps(engine)),
            )
        return self.get_project(pid)  # type: ignore[return-value]

    def get_project(self, pid: str) -> dict | None:
        return self.one("SELECT * FROM projects WHERE id=?", (pid,))

    def list_projects(self) -> list[dict]:
        rows = self.q(
            "SELECT p.*, (SELECT COUNT(*) FROM runs r WHERE r.project_id=p.id) AS run_count, "
            "(SELECT r.id FROM runs r WHERE r.project_id=p.id AND r.status='done' ORDER BY r.created_at DESC LIMIT 1) AS latest_run_id "
            "FROM projects p ORDER BY updated_at DESC"
        )
        return rows

    def update_project(self, pid: str, **fields: Any) -> dict | None:
        allowed = {"name", "notes", "prep", "board", "engine", "has_image", "image_width", "image_height", "has_mask"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            if isinstance(v, bool):
                v = int(v)
            sets.append(f"{k}=?")
            vals.append(v)
        if not sets:
            return self.get_project(pid)
        sets.append("updated_at=?")
        vals.append(time.time())
        vals.append(pid)
        with self.tx() as c:
            c.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id=?", tuple(vals))
        return self.get_project(pid)

    def delete_project(self, pid: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM projects WHERE id=?", (pid,))

    # Runs -----------------------------------------------------------------------------------

    def create_run(self, project_id: str, engine: str, prep: dict, board: dict, params: dict,
                   group_id: str = "", label: str = "") -> dict:
        rid = new_id()
        with self.tx() as c:
            c.execute(
                "INSERT INTO runs (id, project_id, created_at, status, engine, label, prep, board, params, group_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (rid, project_id, time.time(), "queued", engine, label, json.dumps(prep), json.dumps(board),
                 json.dumps(params), group_id),
            )
            c.execute("UPDATE projects SET updated_at=? WHERE id=?", (time.time(), project_id))
        return self.get_run(rid)  # type: ignore[return-value]

    def get_run(self, rid: str) -> dict | None:
        return self.one("SELECT * FROM runs WHERE id=?", (rid,))

    def list_runs(self, project_id: str) -> list[dict]:
        return self.q("SELECT * FROM runs WHERE project_id=? ORDER BY created_at DESC", (project_id,))

    def set_run_status(self, rid: str, status: str, metrics: dict | None = None, error_message: str = "") -> None:
        with self.tx() as c:
            if status in ("done", "failed", "cancelled"):
                c.execute("UPDATE runs SET status=?, metrics=COALESCE(?, metrics), error_message=?, finished_at=? WHERE id=?",
                          (status, json.dumps(metrics) if metrics is not None else None, error_message, time.time(), rid))
            else:
                c.execute("UPDATE runs SET status=?, metrics=COALESCE(?, metrics) WHERE id=?",
                          (status, json.dumps(metrics) if metrics is not None else None, rid))

    def update_run(self, rid: str, **fields: Any) -> None:
        allowed = {"label", "metrics"}
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(json.dumps(v) if isinstance(v, (dict, list)) else v)
        if sets:
            vals.append(rid)
            with self.tx() as c:
                c.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id=?", tuple(vals))

    def delete_run(self, rid: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM runs WHERE id=?", (rid,))

    def stale_runs(self) -> list[dict]:
        return self.q("SELECT * FROM runs WHERE status IN ('queued','running')")

    # Presets --------------------------------------------------------------------------------

    def list_presets(self) -> list[dict]:
        return self.q("SELECT * FROM presets ORDER BY name")

    def create_preset(self, name: str, board: dict, engine: dict) -> dict:
        pid = new_id()
        with self.tx() as c:
            c.execute("INSERT INTO presets (id, name, created_at, board, engine) VALUES (?,?,?,?,?)",
                      (pid, name, time.time(), json.dumps(board), json.dumps(engine)))
        return self.one("SELECT * FROM presets WHERE id=?", (pid,))  # type: ignore[return-value]

    def delete_preset(self, pid: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM presets WHERE id=?", (pid,))

    # Build progress -------------------------------------------------------------------------

    def get_progress(self, run_id: str) -> dict:
        row = self.one("SELECT * FROM build_progress WHERE run_id=?", (run_id,))
        return row or {"run_id": run_id, "step": 0, "updated_at": 0, "device": ""}

    def set_progress(self, run_id: str, step: int, device: str = "") -> dict:
        with self.tx() as c:
            c.execute(
                "INSERT INTO build_progress (run_id, step, updated_at, device) VALUES (?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET step=excluded.step, updated_at=excluded.updated_at, device=excluded.device",
                (run_id, int(step), time.time(), device),
            )
        return self.get_progress(run_id)
