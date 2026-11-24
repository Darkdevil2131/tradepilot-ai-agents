from __future__ import annotations
import json, os, sqlite3
from pathlib import Path
from .models import ShipmentAnalysis

class ShipmentStore:
    """SQLite store. On free hosts without a mounted disk the database is ephemeral."""
    def __init__(self,db_path:str|None=None):
        self.db_path=db_path or os.getenv("TRADEPILOT_DB_PATH","./data/tradepilot.sqlite3")
        if self.db_path != ":memory:": Path(self.db_path).parent.mkdir(parents=True,exist_ok=True)
        self._initialize()
    def _connect(self): return sqlite3.connect(self.db_path,timeout=5)
    def _initialize(self):
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS shipments (shipment_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)")
            conn.execute("""CREATE TABLE IF NOT EXISTS revisions (
                shipment_id TEXT, revision INTEGER, payload TEXT NOT NULL, inputs TEXT,
                PRIMARY KEY(shipment_id, revision))""")
            conn.execute("""INSERT OR IGNORE INTO revisions(shipment_id, revision, payload)
                            SELECT shipment_id, 1, payload FROM shipments""")

    def history(self, shipment_id):
        with self._connect() as conn:
            rows = conn.execute("SELECT revision, inputs, payload FROM revisions WHERE shipment_id=? ORDER BY revision", (shipment_id,)).fetchall()
        result = []
        previous = None
        for version, inputs, payload in rows:
            current = json.loads(inputs) if inputs else None
            changed = sorted(k for k in current if previous.get(k) != current[k]) if previous and current else []
            result.append(dict(revision=version, changed_inputs=changed, inputs=current, analysis=json.loads(payload)))
            previous = current
        return result

    def save(self,analysis:ShipmentAnalysis, inputs=None)->None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""INSERT INTO revisions SELECT ?,COALESCE(MAX(revision),0)+1,?,?
                            FROM revisions WHERE shipment_id=?""",
                         (analysis.shipment_id,analysis.model_dump_json(),inputs,analysis.shipment_id))
            conn.execute("INSERT OR REPLACE INTO shipments(shipment_id,created_at,payload) VALUES(?,?,?)",(analysis.shipment_id,analysis.created_at.isoformat(),analysis.model_dump_json()))
    def get(self,shipment_id:str)->ShipmentAnalysis|None:
        with self._connect() as conn: row=conn.execute("SELECT payload FROM shipments WHERE shipment_id=?",(shipment_id,)).fetchone()
        return ShipmentAnalysis.model_validate_json(row[0]) if row else None
