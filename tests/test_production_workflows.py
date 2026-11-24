from datetime import datetime, timedelta, timezone
from io import BytesIO

from pypdf import PdfWriter

from tradepilot.document_processing import process_document_bytes
from tradepilot.evaluation import EvaluationRequest, evaluate_decisions


def blank_pdf() -> bytes:
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(stream)
    return stream.getvalue()


def test_pdf_processing_hashes_and_never_claims_authenticity():
    result = process_document_bytes(blank_pdf(), "invoice.pdf", "application/pdf")
    assert result["pages"] == 1
    assert len(result["sha256"]) == 64
    assert result["authenticity"]["status"] == "not_verified"
    assert result["authenticity"]["cryptographic_validation_performed"] is False


def test_document_upload_and_background_job(client, headers):
    pdf = blank_pdf()
    response = client.post(
        "/api/v1/documents/extract", headers=headers,
        files={"file": ("invoice.pdf", pdf, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["sha256"]

    queued = client.post(
        "/api/v1/documents/process", headers=headers,
        files={"file": ("invoice.pdf", pdf, "application/pdf")},
    )
    assert queued.status_code == 202
    job = client.get(f"/api/v1/jobs/{queued.json()['id']}", headers=headers)
    assert job.status_code == 200
    assert job.json()["status"] == "completed"


def test_upload_rejects_unsupported_content(client, headers):
    response = client.post(
        "/api/v1/documents/extract", headers=headers,
        files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 422


def test_complete_approval_gated_commercial_workflow(client, headers):
    deadline = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    rfq = client.post("/api/v1/rfqs", headers=headers, json={
        "shipment_id": "TEST-100", "carrier_ids": ["DEMO-AIR-01"],
        "requirements": {"weight_kg": 1000}, "response_deadline": deadline,
        "notification_emails": {"DEMO-AIR-01": "carrier@example.com"},
    })
    assert rfq.status_code == 201, rfq.text
    rfq_id = rfq.json()["id"]
    dispatched = client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=headers)
    assert dispatched.status_code == 200
    assert dispatched.json()["payload"]["delivery"][0]["external_send"] is False
    notification_id = dispatched.json()["payload"]["delivery"][0]["notification_id"]
    email = client.post(f"/api/v1/notifications/{notification_id}/send", headers=headers)
    assert email.status_code == 503
    assert email.json()["detail"] == "SMTP delivery is disabled"

    response = client.post(f"/api/v1/rfqs/{rfq_id}/responses", headers=headers, json={
        "carrier_id": "DEMO-AIR-01", "amount": 900, "currency": "USD",
        "mode": "air", "transit_hours": 12, "includes_insurance": True,
    })
    assert response.status_code == 201
    approval = client.post("/api/v1/approvals", headers=headers, json={
        "response_id": response.json()["id"], "approved": True, "comment": "reviewed",
    })
    assert approval.status_code == 201
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "approval_id": approval.json()["id"], "execution_mode": "dry_run",
    })
    assert booking.status_code == 201
    assert booking.json()["payload"]["external_booking_performed"] is False

    tracked = client.post(f"/api/v1/bookings/{booking.json()['id']}/tracking", headers=headers, json={
        "status": "departed", "location": "Mumbai",
        "occurred_at": datetime.now(timezone.utc).isoformat(), "provenance": "USER_PROVIDED",
    })
    assert tracked.status_code == 200
    assert tracked.json()["payload"]["tracking_events"][0]["status"] == "departed"

    purchase_order = client.post("/api/v1/purchase-orders", headers=headers, json={
        "supplier_id": "SUP-01", "shipment_id": "TEST-100", "amount": 500,
        "currency": "USD", "approval_reference": approval.json()["id"],
    })
    assert purchase_order.status_code == 201
    assert purchase_order.json()["payload"]["external_post_performed"] is False


def test_external_booking_adapter_fails_closed(client, headers):
    deadline = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    rfq = client.post("/api/v1/rfqs", headers=headers, json={"shipment_id": "S-100", "carrier_ids": ["C1"], "response_deadline": deadline}).json()
    client.post(f"/api/v1/rfqs/{rfq['id']}/dispatch", headers=headers)
    response = client.post(f"/api/v1/rfqs/{rfq['id']}/responses", headers=headers, json={"carrier_id": "C1", "amount": 1, "currency": "USD", "mode": "air", "transit_hours": 2}).json()
    approval = client.post("/api/v1/approvals", headers=headers, json={"response_id": response["id"], "approved": True}).json()
    booking = client.post("/api/v1/bookings", headers=headers, json={"approval_id": approval["id"], "execution_mode": "external_adapter"})
    assert booking.status_code == 409
    assert "No authorized external booking adapter" in booking.json()["detail"]


def test_role_permissions_are_enforced(client, monkeypatch):
    monkeypatch.setenv("TRADEPILOT_ANALYST_KEY", "analyst-secret")
    analyst = {"Authorization": "Bearer analyst-secret"}
    deadline = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    rfq = client.post("/api/v1/rfqs", headers=analyst, json={"shipment_id": "S-200", "carrier_ids": ["C1"], "response_deadline": deadline})
    assert rfq.status_code == 201
    assert client.post(f"/api/v1/rfqs/{rfq.json()['id']}/dispatch", headers=analyst).status_code == 403


def test_audit_metrics_and_backup(client, headers):
    health = client.get("/api/v1/health")
    assert health.headers["x-content-type-options"] == "nosniff"
    assert health.headers["x-frame-options"] == "DENY"
    deadline = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    client.post("/api/v1/rfqs", headers=headers, json={"shipment_id": "AUD-100", "carrier_ids": ["C1"], "response_deadline": deadline})
    metrics = client.get("/api/v1/metrics", headers=headers)
    assert metrics.status_code == 200
    assert "tradepilot_http_requests_total" in metrics.text
    audit = client.get("/api/v1/audit", headers=headers)
    assert audit.status_code == 200
    assert any(event["action"] == "rfq.create" for event in audit.json()["events"])
    backup = client.post("/api/v1/admin/backups", headers=headers)
    assert backup.status_code == 200
    assert backup.json()["status"] == "created"


def test_decision_quality_metrics_and_endpoint(client, headers):
    records = [{
        "shipment_id": f"H-{index}", "recommended_mode": "air", "actual_mode": "air" if index < 4 else "sea",
        "predicted_cost": 100, "actual_cost": 110, "baseline_cost": 130,
        "predicted_hours": 10, "actual_hours": 12, "predicted_risk": .2,
        "disruption_occurred": index == 4, "provenance": "SYNTHETIC",
    } for index in range(5)]
    result = evaluate_decisions(EvaluationRequest(records=records).records)
    assert result["records"] == 5
    assert result["eta"]["mean_absolute_error_hours"] == 2
    assert result["expert_or_actual_mode_agreement"] == .8
    response = client.post("/api/v1/evaluations/decision-quality", headers=headers, json={"records": records})
    assert response.status_code == 200
    assert response.json()["risk_calibration"]["brier_score"] >= 0


def test_postgresql_store_is_selected_from_database_url(monkeypatch):
    from tradepilot import storage

    selected = {}
    monkeypatch.setenv("TRADEPILOT_DATABASE_URL", "postgresql://example.invalid/tradepilot")
    monkeypatch.setattr(storage, "PostgresShipmentStore", lambda url: selected.setdefault("url", url) or object())
    result = storage.create_shipment_store()
    assert result == "postgresql://example.invalid/tradepilot"
    assert selected["url"].startswith("postgresql://")
