from datetime import datetime,timezone,timedelta
from fastapi.testclient import TestClient
from tradepilot.main import app

BASE={"shipment_id":"TEST-100","origin":"Mumbai","destination":"Singapore","cargo_description":"parts","weight_kg":1200,"allowed_modes":["air","sea"]}

def test_health_is_public(client):
    response=client.get("/api/v1/health")
    assert response.status_code==200 and response.json()["live_logistics_feeds"] is False

def test_authentication_failure(client):
    assert client.post("/api/v1/shipments/analyze",json=BASE).status_code==401
    assert client.post("/api/v1/shipments/analyze",json=BASE,headers={"Authorization":"Bearer wrong"}).status_code==401

def test_analysis_and_persistent_retrieval_within_configured_sqlite(client,headers):
    response=client.post("/api/v1/shipments/analyze",json=BASE,headers=headers)
    assert response.status_code==200,response.text
    body=response.json()
    assert set(["route_optimization","carrier_research","risk_compliance","document_intelligence","market_procurement","logistics_orchestrator"]) <= set(body["agents"])
    assert body["agents"]["document_intelligence"]["status"]=="completed"
    assert "commercial_invoice" in body["agents"]["document_intelligence"]["data"]["missing_document_types"]
    get=client.get("/api/v1/shipments/TEST-100",headers=headers)
    assert get.status_code==200 and get.json()["shipment_id"]=="TEST-100"

def test_invalid_input_and_unrealistic_deadline(client,headers):
    bad={**BASE,"weight_kg":0}
    assert client.post("/api/v1/shipments/analyze",json=bad,headers=headers).status_code==422
    soon={**BASE,"shipment_id":"TEST-101","deadline":(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()}
    response=client.post("/api/v1/shipments/analyze",json=soon,headers=headers)
    assert response.status_code==200
    assert all(not r["deadline_feasible"] for r in response.json()["agents"]["route_optimization"]["data"]["routes"])

def test_document_endpoint_and_missing_documents(client,headers):
    response=client.post("/api/v1/documents/validate",json={"shipment":BASE},headers=headers)
    assert response.status_code==200
    assert response.json()["valid"] is False and "commercial_invoice" in response.json()["missing_document_types"]

def test_missing_shipment_returns_404(client,headers):
    assert client.get("/api/v1/shipments/absent",headers=headers).status_code==404

def test_model_offline_no_api_key_required(client,headers,monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY",raising=False)
    response=client.post("/api/v1/shipments/analyze",json=BASE,headers=headers)
    assert response.status_code==200
    assert response.json()["agents"]["logistics_orchestrator"]["data"]["gemini_narrative"]["status"]=="disabled"

def test_auth_missing_server_secret_fails_closed(client,headers,monkeypatch):
    monkeypatch.delenv("TRADEPILOT_API_KEY",raising=False)
    assert client.get("/api/v1/shipments/TEST-100",headers=headers).status_code==503

def test_openapi_and_docs(client):
    assert client.get("/openapi.json").status_code==200
    assert client.get("/docs").status_code==200


def test_request_quota_returns_429_and_retry_after(client,headers,monkeypatch):
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE","1")
    first=client.post("/api/v1/shipments/analyze",json=BASE,headers=headers)
    second=client.post("/api/v1/shipments/analyze",json=BASE,headers=headers)
    assert first.status_code==200 and second.status_code==429
    assert int(second.headers["Retry-After"])>0


def test_readiness_checks_auth_storage_and_bypasses_client_quota(client, monkeypatch):
    from tradepilot.main import store

    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "1")
    for _ in range(3):
        assert client.get("/api/v1/ready").status_code == 200
    with store._connect() as conn:
        conn.execute("DROP TABLE revisions")
    assert client.get("/api/v1/ready").status_code == 503
    assert client.get("/api/v1/health").status_code == 200


def test_example_key_cannot_activate_service(client, monkeypatch):
    placeholder = "replace-with-a-long-random-secret"
    monkeypatch.setenv("TRADEPILOT_API_KEY", placeholder)
    assert client.get("/api/v1/ready").status_code == 503
    response = client.get("/api/v1/shipments/unknown", headers={"Authorization": "Bearer " + placeholder})
    assert response.status_code == 503
