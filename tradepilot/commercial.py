from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import Field, model_validator

from .models import StrictModel
from .operations import OperationsStore


class RFQCreate(StrictModel):
    shipment_id: str = Field(min_length=3, max_length=80)
    carrier_ids: list[str] = Field(min_length=1, max_length=30)
    requirements: dict[str, Any] = Field(default_factory=dict)
    notification_emails: dict[str, str] = Field(default_factory=dict)
    response_deadline: datetime

    @model_validator(mode="after")
    def future_deadline(self):
        deadline = self.response_deadline
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if deadline <= datetime.now(timezone.utc):
            raise ValueError("response_deadline must be in the future")
        self.response_deadline = deadline
        return self


class CarrierResponseCreate(StrictModel):
    carrier_id: str = Field(min_length=1, max_length=80)
    amount: float = Field(gt=0, le=100_000_000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    mode: Literal["air", "sea", "road", "rail"]
    transit_hours: float = Field(gt=0, le=100_000)
    includes_insurance: bool = False
    terms: dict[str, Any] = Field(default_factory=dict)


class ApprovalCreate(StrictModel):
    response_id: str = Field(min_length=3, max_length=80)
    approved: bool
    comment: str = Field(default="", max_length=1000)


class BookingIntentCreate(StrictModel):
    approval_id: str = Field(min_length=3, max_length=80)
    execution_mode: Literal["dry_run", "external_adapter"] = "dry_run"


class PurchaseOrderCreate(StrictModel):
    supplier_id: str = Field(min_length=1, max_length=80)
    shipment_id: str = Field(min_length=3, max_length=80)
    amount: float = Field(gt=0, le=100_000_000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    approval_reference: str = Field(min_length=3, max_length=120)


class TrackingEventCreate(StrictModel):
    status: str = Field(min_length=2, max_length=80)
    location: str | None = Field(default=None, max_length=160)
    occurred_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)
    provenance: Literal["LIVE", "USER_PROVIDED", "SYNTHETIC"] = "USER_PROVIDED"


class CommercialWorkflow:
    """Approval-gated commercial workflow. It never invents or performs an external transaction."""

    def __init__(self, store: OperationsStore):
        self.store = store

    def create_rfq(self, request: RFQCreate, actor: str, role: str) -> dict[str, Any]:
        payload = request.model_dump(mode="json") | {"created_by": actor, "responses": [], "delivery": []}
        record = self.store.create_record("rfq", "draft", payload, "RFQ")
        self.store.log(actor, role, "rfq.create", record["id"], {"shipment_id": request.shipment_id})
        return record

    def dispatch_rfq(self, rfq_id: str, actor: str, role: str) -> dict[str, Any]:
        record = self._require(rfq_id, "rfq")
        if record["status"] != "draft":
            raise ValueError("Only a draft RFQ can be dispatched")
        payload = dict(record["payload"])
        delivery = []
        for carrier_id in payload["carrier_ids"]:
            email = payload.get("notification_emails", {}).get(carrier_id)
            notification = self.store.create_record("notification", "queued", {
                "recipient": email, "carrier_id": carrier_id, "rfq_id": rfq_id,
                "subject": f"TradePilot RFQ {rfq_id}",
                "body": "A request for quotation is available. Review it in the authorized TradePilot portal.",
                "external_send": False,
            }, "NTF")
            delivery.append({"carrier_id": carrier_id, "channel": "smtp" if email else "internal_outbox",
                             "status": "queued", "external_send": False, "notification_id": notification["id"]})
        payload["delivery"] = delivery
        updated = self.store.update_record(rfq_id, "issued", payload)
        self.store.log(actor, role, "rfq.dispatch", rfq_id, {"external_send": False})
        return updated or {}

    def submit_response(self, rfq_id: str, response: CarrierResponseCreate, actor: str, role: str) -> dict[str, Any]:
        rfq = self._require(rfq_id, "rfq")
        if rfq["status"] not in {"issued", "responses_received"}:
            raise ValueError("Carrier responses require an issued RFQ")
        if response.carrier_id not in rfq["payload"]["carrier_ids"]:
            raise ValueError("Carrier is not invited to this RFQ")
        payload = response.model_dump(mode="json") | {"rfq_id": rfq_id, "submitted_by": actor}
        result = self.store.create_record("carrier_response", "submitted", payload, "RSP")
        rfq_payload = dict(rfq["payload"])
        rfq_payload["responses"] = [*rfq_payload.get("responses", []), result["id"]]
        self.store.update_record(rfq_id, "responses_received", rfq_payload)
        self.store.log(actor, role, "carrier_response.submit", result["id"], {"rfq_id": rfq_id})
        return result

    def approve(self, request: ApprovalCreate, actor: str, role: str) -> dict[str, Any]:
        response = self._require(request.response_id, "carrier_response")
        payload = request.model_dump(mode="json") | {
            "approved_by": actor,
            "decision_time": datetime.now(timezone.utc).isoformat(),
            "response_snapshot": response["payload"],
        }
        result = self.store.create_record("approval", "approved" if request.approved else "rejected", payload, "APR")
        self.store.log(actor, role, "commercial.approve" if request.approved else "commercial.reject", result["id"], {"response_id": request.response_id})
        return result

    def create_booking_intent(self, request: BookingIntentCreate, actor: str, role: str) -> dict[str, Any]:
        approval = self._require(request.approval_id, "approval")
        if approval["status"] != "approved":
            raise ValueError("Booking requires an approved carrier response")
        if request.execution_mode == "external_adapter":
            raise ValueError("No authorized external booking adapter is configured")
        payload = request.model_dump(mode="json") | {
            "created_by": actor,
            "external_booking_performed": False,
            "approval_snapshot": approval["payload"],
            "tracking_events": [],
        }
        result = self.store.create_record("booking", "dry_run_created", payload, "BKG")
        self.store.log(actor, role, "booking.intent.create", result["id"], {"external_booking_performed": False})
        return result

    def create_purchase_order(self, request: PurchaseOrderCreate, actor: str, role: str) -> dict[str, Any]:
        payload = request.model_dump(mode="json") | {"created_by": actor, "external_post_performed": False}
        result = self.store.create_record("purchase_order", "draft", payload, "PO")
        self.store.log(actor, role, "purchase_order.create", result["id"], {"external_post_performed": False})
        return result

    def add_tracking_event(self, booking_id: str, event: TrackingEventCreate, actor: str, role: str) -> dict[str, Any]:
        booking = self._require(booking_id, "booking")
        payload = dict(booking["payload"])
        payload["tracking_events"] = [*payload.get("tracking_events", []), event.model_dump(mode="json")]
        updated = self.store.update_record(booking_id, "tracking", payload)
        self.store.log(actor, role, "tracking.event.add", booking_id, {"status": event.status, "provenance": event.provenance})
        return updated or {}

    def _require(self, record_id: str, kind: str) -> dict[str, Any]:
        record = self.store.get_record(record_id)
        if not record or record["kind"] != kind:
            raise KeyError(f"{kind} not found")
        return record
