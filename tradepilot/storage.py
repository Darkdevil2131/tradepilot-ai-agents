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
    def save(self,analysis:ShipmentAnalysis)->None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO shipments(shipment_id,created_at,payload) VALUES(?,?,?)",(analysis.shipment_id,analysis.created_at.isoformat(),analysis.model_dump_json()))
    def get(self,shipment_id:str)->ShipmentAnalysis|None:
        with self._connect() as conn: row=conn.execute("SELECT payload FROM shipments WHERE shipment_id=?",(shipment_id,)).fetchone()
        return ShipmentAnalysis.model_validate_json(row[0]) if row else None
