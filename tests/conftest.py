import os, tempfile
import pytest
from fastapi.testclient import TestClient
from tradepilot.main import app, store

@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("TRADEPILOT_API_KEY","test-secret-123")
    monkeypatch.setenv("GEMINI_ENABLED","false")

@pytest.fixture
def client(tmp_path,monkeypatch):
    from tradepilot import main
    main.store=main.ShipmentStore(str(tmp_path/"shipments.sqlite3"))
    with main._rate_lock:
        main._request_windows.clear()
    with TestClient(main.app) as test_client:
        yield test_client

@pytest.fixture
def headers(): return {"Authorization":"Bearer test-secret-123"}
