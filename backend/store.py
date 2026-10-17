"""Original records stay immutable; SQLite owns updates, DuckDB projects analytics."""
import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import duckdb

from .models import KnowledgeCommand


class Store:
    def __init__(self, source: Path, state: Path):
        self.source = source.resolve()
        state.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.sql = sqlite3.connect(state / "knowledge.sqlite", check_same_thread=False)
        self.sql.row_factory = sqlite3.Row
        self.sql.executescript("""
          CREATE TABLE IF NOT EXISTS events(
            version INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT UNIQUE,
            payload TEXT NOT NULL, created_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS memory(
            id INTEGER PRIMARY KEY, user_id TEXT, thread_id TEXT,
            question TEXT, answer TEXT, assets TEXT, created_at TEXT);
          CREATE VIRTUAL TABLE IF NOT EXISTS documents USING fts5(path UNINDEXED, body);
        """)
        self.db = duckdb.connect(str(state / "analytics.duckdb"))
        self.db.execute("SET TimeZone='UTC'")
        self.db.execute("CREATE TABLE IF NOT EXISTS records(id VARCHAR PRIMARY KEY, kind VARCHAR, payload JSON, path VARCHAR, line INTEGER)")
        self.users = {}
        self.assets = {}
        self.rules = {}
        self.area_records = []
        self.data_hash = ""
        self.ingest()

    @property
    def version(self):
        return self.sql.execute("SELECT COALESCE(MAX(version),0) FROM events").fetchone()[0]

    def ingest(self):
        if not (self.source / "assets").is_dir():
            raise ValueError(f"Dataset not found at {self.source}")
        with self.lock:
            rows = []
            digest = hashlib.sha256()
            for path in sorted(self.source.rglob("*")):
                if not path.is_file() or path.suffix not in {".json", ".jsonl", ".md"}:
                    continue
                raw = path.read_text(encoding="utf-8")
                rel = path.relative_to(self.source).as_posix()
                digest.update(rel.encode() + raw.encode())
                if path.suffix == ".md":
                    continue
                if path.parent.name == "fleet":
                    if path.name == "users.json":
                        users = json.loads(raw)
                        users = users["users"] if isinstance(users, dict) else users
                        self.users = {u["user_id"]: u for u in users}
                    continue
                kind = path.stem
                objects = [json.loads(raw)] if path.suffix == ".json" else [json.loads(line) for line in raw.splitlines() if line.strip()]
                for n, obj in enumerate(objects, 1):
                    key = {"metadata": "asset_id", "inspections": "inspection_id", "maintenance": "maintenance_id", "regulatory-events": "event_id", "context": "note_id"}.get(kind)
                    if key:
                        rows.append((obj[key], kind, json.dumps(obj), rel, n))
                    if kind == "metadata":
                        self.assets[obj["asset_id"]] = obj
            self.data_hash = digest.hexdigest()[:16]
            ids = [row[0] for row in rows]
            if len(set(ids)) != len(ids):
                raise ValueError("Duplicate source record IDs; ingestion aborted")
            # JSON batch ingestion avoids a Python insert per inspection.
            payload = json.dumps([dict(zip(["id", "kind", "payload", "path", "line"], r)) for r in rows])
            self.db.execute("BEGIN")
            try:
                self.db.execute("DELETE FROM records")
                self.db.execute("INSERT INTO records SELECT value->>'id',value->>'kind',CAST(value->>'payload' AS JSON),value->>'path',CAST(value->>'line' AS INTEGER) FROM json_each(?)", [payload])
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            with self.sql:
                self.sql.execute("DELETE FROM documents")
                for path in sorted(self.source.rglob("*.md")):
                    self.sql.execute("INSERT INTO documents(path,body) VALUES(?,?)", (path.relative_to(self.source).as_posix(), path.read_text(encoding="utf-8")))
            areas_path = self.source / "fleet" / "areas.json"
            area_document = json.loads(areas_path.read_text(encoding="utf-8"))
            self.area_records = area_document["areas"] if isinstance(area_document, dict) else area_document
            for path in sorted((self.source / "fleet" / "well-types").glob("*.md")):
                text = path.read_text(encoding="utf-8")
                threshold_match = re.search(r"wellhead temperature is at least\s+\*\*(\d+)\s*°C above", text, re.I)
                if not threshold_match:
                    threshold_match = re.search(r"wellhead temperature is at least\s+(\d+)\s*°C above", text, re.I)
                matches = [threshold_match.group(1)] if threshold_match else re.findall(r"at least\s+\*\*(\d+)\s*°C above", text)
                if len(matches) != 1:
                    raise ValueError(f"Cannot unambiguously extract production rule from {path.name}")
                self.rules[path.stem] = {"threshold": float(matches[0]), "path": path.relative_to(self.source).as_posix()}
            self.refresh()

    def records(self, kind):
        return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM records WHERE kind=? ORDER BY id", [kind]).fetchall()]

    def refresh(self):
        """Rebuild effective inspection projection atomically under the store lock."""
        inspections = {r["inspection_id"]: r for r in self.records("inspections")}
        changes = {}
        for note in self.records("context"):
            if note["note_type"] == "data_correction" and note.get("inspection_id") in inspections:
                field = note.get("field")
                if field in {"wellhead_temp_c", "ambient_temp_c", "spill_est_volume_bbl", "spill_detected"}:
                    inspections[note["inspection_id"]][field] = note.get("corrected_value")
                    changes.setdefault(note["inspection_id"], []).append(note["note_id"])
            elif note["note_type"] == "correction" and note.get("inspection_id") in inspections:
                # Narrow verified source pattern. Unrecognized corrections are disclosed.
                if re.search(r"not (?:a )?(?:spill|crude)|rainwater|false positive", note["text"], re.I):
                    inspections[note["inspection_id"]]["spill_detected"] = False
                    changes.setdefault(note["inspection_id"], []).append(note["note_id"])
        for event in self.sql.execute("SELECT version,payload FROM events ORDER BY version"):
            p = json.loads(event["payload"])
            if p["kind"] == "inspection":
                inspections[p["record_id"]] = {**p["inspection"], "inspection_id": p["record_id"], "asset_id": p["asset_id"], "inspector_user_id": p["user_id"]}
            elif p["kind"] == "correction" and p["target_id"] in inspections:
                inspections[p["target_id"]][p["field"]] = p["value"]
                changes.setdefault(p["target_id"], []).append(f"UPDATE-{event['version']}")
        self.effective = inspections
        self.changes = changes
        rows = []
        for r in inspections.values():
            asset = self.assets[r["asset_id"]]
            inspected_on = r["inspected_at"][:10]
            historical_type = next((period["well_type"] for period in asset.get("type_history", []) if period["from"] <= inspected_on and (not period.get("to") or inspected_on <= period["to"])), asset["well_type"])
            rule = self.rules[historical_type]
            delta = round(r["wellhead_temp_c"] - r["ambient_temp_c"], 6)
            decommissioned = asset.get("decommissioned_on") and inspected_on >= asset["decommissioned_on"]
            team_id = next((period.get("team_id") for period in asset.get("team_history", []) if period["from"] <= inspected_on and (not period.get("to") or inspected_on <= period["to"])), asset.get("team_id") or next((area.get("team_id") for area in self.area_records if area["area_id"] == asset["area_id"]), None))
            rows.append({**r, "area_id": asset["area_id"], "team_id": team_id, "well_type": historical_type, "decommissioned": bool(decommissioned), "delta_c": delta, "producing": delta >= rule["threshold"] and not decommissioned, "threshold_c": rule["threshold"], "lat": asset["location"]["lat"], "lon": asset["location"]["lon"]})
        self.db.execute("CREATE OR REPLACE TABLE effective AS SELECT value->>'inspection_id' inspection_id, value->>'asset_id' asset_id, CAST(value->>'inspected_at' AS TIMESTAMPTZ) inspected_at, value->>'inspector_user_id' inspector_user_id, value->>'method' AS \"method\", value->>'area_id' area_id, value->>'team_id' team_id, value->>'well_type' well_type, CAST(value->>'decommissioned' AS BOOLEAN) decommissioned, CAST(value->>'delta_c' AS DOUBLE) delta_c, CAST(value->>'wellhead_temp_c' AS DOUBLE) wellhead_temp_c, CAST(value->>'ambient_temp_c' AS DOUBLE) ambient_temp_c, CAST(value->>'producing' AS BOOLEAN) producing, CAST(value->>'threshold_c' AS DOUBLE) threshold_c, CAST(value->>'spill_detected' AS BOOLEAN) spill_detected, CAST(value->>'spill_est_volume_bbl' AS DOUBLE) spill_est_volume_bbl, CAST(value->>'lat' AS DOUBLE) lat, CAST(value->>'lon' AS DOUBLE) lon, value->>'notes' notes FROM json_each(?)", [json.dumps(rows)])

    def check_user(self, user_id):
        if user_id not in self.users:
            raise ValueError("Unknown demo user")

    def commit(self, command: KnowledgeCommand):
        with self.lock:
            self.check_user(command.user_id)
            if command.kind != "policy" and command.asset_id not in self.assets:
                raise ValueError("Unknown asset")
            existing = self.sql.execute("SELECT version,payload FROM events WHERE request_id=?", (command.request_id,)).fetchone()
            payload = command.model_dump(mode="json")
            if existing:
                original = json.loads(existing["payload"])
                original.pop("record_id", None)
                if original != payload:
                    raise ValueError("Request ID already used for a different update")
                return {"version": existing["version"], "duplicate": True}
            if command.expected_version != self.version:
                raise RuntimeError("Knowledge changed. Refresh and review before submitting again.")
            if command.kind == "correction":
                target = self.effective.get(command.target_id)
                if not target or target["asset_id"] != command.asset_id:
                    raise ValueError("Target inspection does not belong to this asset")
            payload["record_id"] = f"FIELD-{uuid4().hex[:12]}"
            with self.sql:
                self.sql.execute("INSERT INTO events(request_id,payload,created_at) VALUES(?,?,?)", (command.request_id, json.dumps(payload), datetime.now(timezone.utc).isoformat()))
            try:
                self.refresh()
            except Exception:
                # Persisted ledger is authoritative; never claim stale projections are current.
                self.projection_failed = True
                raise
            self.projection_failed = False
            return {"version": self.version, "record_id": payload["record_id"], "duplicate": False}

    def source_record(self, record_id):
        with self.lock:
            row = self.db.execute("SELECT payload,path,line FROM records WHERE id=?", [record_id]).fetchone()
            if row:
                return {"id": record_id, "record": json.loads(row[0]), "path": row[1], "line": row[2], "corrections": self.changes.get(record_id, [])}
            if record_id.startswith("UPDATE-"):
                row = self.sql.execute("SELECT version,payload,created_at FROM events WHERE version=?", (record_id.removeprefix("UPDATE-"),)).fetchone()
                if row:
                    return {"id": record_id, "record": json.loads(row["payload"]), "created_at": row["created_at"]}
            if record_id in self.effective:
                return {"id": record_id, "record": self.effective[record_id], "corrections": self.changes.get(record_id, [])}
            raise ValueError("Evidence record not found")

    def search(self, query, limit=8):
        words = re.findall(r"[\w-]+", query)[:15]
        if not words:
            return []
        match = " OR ".join('"' + w + '"' for w in words)
        return [dict(row) for row in self.sql.execute("SELECT path,snippet(documents,1,'','', ' … ',48) excerpt FROM documents WHERE documents MATCH ? ORDER BY rank LIMIT ?", (match, limit))]

    def history(self, user_id, thread_id=None):
        self.check_user(user_id)
        clause = "user_id=?"
        args = [user_id]
        if thread_id:
            clause += " AND thread_id=?"
            args.append(thread_id)
        return [dict(row) for row in self.sql.execute(f"SELECT question,answer,assets,thread_id,created_at FROM memory WHERE {clause} ORDER BY id DESC LIMIT 6", args)]

    def remember(self, user, thread, question, answer, assets):
        with self.sql:
            self.sql.execute("INSERT INTO memory(user_id,thread_id,question,answer,assets,created_at) VALUES(?,?,?,?,?,?)", (user, thread, question, answer, json.dumps(assets), datetime.now(timezone.utc).isoformat()))
