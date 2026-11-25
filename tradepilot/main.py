from __future__ import annotations
import logging, os, time
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from dotenv import load_dotenv
load_dotenv()
from .agents.orchestrator import LogisticsOrchestrator
from .gemini import GeminiNarrator
from .commercial import (ApprovalCreate, BookingIntentCreate, CarrierResponseCreate,
                         CommercialWorkflow, PurchaseOrderCreate, RFQCreate,
                         TrackingEventCreate)
from .document_processing import DocumentProcessingError, MAX_DOCUMENT_BYTES, process_document_bytes
from .evaluation import EvaluationRequest, evaluate_decisions
from .models import DocumentValidationRequest, DocumentValidationResponse, ShipmentAnalysis, ShipmentRequest
from .notifications import NotificationConfigurationError, SMTPNotificationAdapter
from .operations import OperationsStore
from .platform import Metrics, Principal, RateLimiter, authenticate, configured_keys, role_allows
from .storage import ShipmentStore, create_shipment_store

logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"))
logger=logging.getLogger("tradepilot")
store=create_shipment_store()
operations=OperationsStore()
commercial=CommercialWorkflow(operations)
notifications=SMTPNotificationAdapter()
orchestrator=LogisticsOrchestrator()
narrator=GeminiNarrator()
rate_limiter=RateLimiter()
metrics=Metrics()
# Compatibility aliases used by the existing test fixtures.
_rate_lock=rate_limiter.lock
_request_windows=rate_limiter.windows

@asynccontextmanager
async def lifespan(app:FastAPI):
    logger.info("TradePilot API started; gemini_enabled=%s storage=%s",narrator.enabled,store.db_path)
    yield
app=FastAPI(title="TradePilot AI Multi-Agent Backend",version="1.0.0",description="Offline-capable shipment decision support. Logistics, carrier, compliance, and market data are simulated or estimated unless explicitly tagged otherwise.",lifespan=lifespan)

@app.middleware("http")
async def request_logging(request:Request,call_next):
    start=time.monotonic()
    if request.url.path not in {"/api/v1/health", "/api/v1/ready", "/api/v1/metrics"}:
        identity=request.client.host if request.client else "unknown"
        limit=max(1,int(os.getenv("API_RATE_LIMIT_PER_MINUTE","60")))
        allowed, retry = rate_limiter.allow(identity, limit)
        if not allowed:
            return JSONResponse(status_code=429,content={"detail":"Request quota exceeded; retry later"},headers={"Retry-After":str(retry)})
    try:
        response=await call_next(request)
        duration=time.monotonic()-start
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["X-Frame-Options"]="DENY"
        response.headers["Referrer-Policy"]="no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"]="no-store"
        metrics.observe(request.method, request.url.path, response.status_code, duration)
        logger.info("request method=%s path=%s status=%s duration_ms=%d",request.method,request.url.path,response.status_code,round(duration*1000))
        return response
    except Exception:
        logger.exception("unhandled request error path=%s",request.url.path)
        metrics.observe(request.method, request.url.path, 500, time.monotonic()-start)
        return JSONResponse(status_code=500,content={"detail":"Internal server error"})

def configured_api_key() -> str | None:
    keys = configured_keys()
    return keys[0][0] if keys else None

def require_api_key(authorization:str|None=Header(default=None)) -> Principal:
    expected=configured_api_key()
    if not expected:
        raise HTTPException(status_code=503,detail="API authentication is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401,detail="Missing bearer API key",headers={"WWW-Authenticate":"Bearer"})
    principal=authenticate(authorization[7:].strip())
    if not principal:
        raise HTTPException(status_code=401,detail="Invalid API key",headers={"WWW-Authenticate":"Bearer"})
    return principal

def require_role(role: str):
    def dependency(principal: Principal = Depends(require_api_key)) -> Principal:
        if not role_allows(principal.role, role):
            raise HTTPException(status_code=403, detail=f"{role} role required")
        return principal
    return dependency

@app.get("/api/v1/health",tags=["health"])
async def health():
    return {"status":"ok","mode":"offline_deterministic","gemini_enabled":narrator.enabled,
            "storage":store.persistence_label,"workflow_storage":operations.persistence_label,
            "rate_limiter":rate_limiter.mode,"live_logistics_feeds":False}

@app.get("/api/v1/ready",tags=["health"])
def readiness():
    if not configured_api_key() or not store.ready() or not operations.ready():
        raise HTTPException(status_code=503, detail="Service is not ready")
    return {"status": "ready"}

@app.post("/api/v1/shipments/analyze",response_model=ShipmentAnalysis,tags=["shipments"])
def analyze_shipment(payload:ShipmentRequest, principal:Principal=Depends(require_role("analyst"))):
    try:
        analysis=orchestrator.analyze(payload)
        analysis.persistence=store.persistence_label
        # Narrative generation is optional and cannot alter calculated values or agent outputs.
        completed={k:v.data for k,v in analysis.agents.items() if v.status=="completed"}
        coordinator=completed.get("logistics_orchestrator",{})
        # Put the authoritative workflow decision before specialist evidence so
        # a model summary cannot mistake a feasible route for an approved plan.
        narrative_evidence={
            "workflow_decision":coordinator.get("decision",{}),
            "strategic_explanation":coordinator.get("explanation",{}),
            "specialists":{k:v for k,v in completed.items() if k!="logistics_orchestrator"},
        }
        narrative=narrator.narrate(narrative_evidence)
        analysis.agents["logistics_orchestrator"].data["gemini_narrative"]=narrative
        store.save(analysis, payload.model_dump_json())
        operations.log(principal.subject, principal.role, "shipment.analyze", analysis.shipment_id, {"provenance":[p.value for p in analysis.data_provenance]})
        return analysis
    except Exception:
        logger.exception("shipment analysis failed")
        raise HTTPException(status_code=500,detail="Shipment analysis could not be completed")

@app.get("/api/v1/shipments/{shipment_id}",response_model=ShipmentAnalysis,tags=["shipments"],dependencies=[Depends(require_api_key)])
def get_shipment(shipment_id:str):
    result=store.get(shipment_id)
    if not result: raise HTTPException(status_code=404,detail="Shipment analysis not found in this database instance")
    return result

@app.post("/api/v1/documents/validate",response_model=DocumentValidationResponse,tags=["documents"],dependencies=[Depends(require_api_key)])
def validate_shipment_documents(payload:DocumentValidationRequest):
    result=orchestrator.documents.run(payload.shipment).model_dump(exclude={"extracted_fields"})
    return DocumentValidationResponse(shipment_id=payload.shipment.shipment_id,**result)


@app.get("/api/v1/shipments/{shipment_id}/history", dependencies=[Depends(require_api_key)])
def shipment_history(shipment_id: str):
    revisions = store.history(shipment_id)
    if not revisions:
        raise HTTPException(status_code=404, detail="No shipment history in this database")
    return {"shipment_id": shipment_id, "revisions": revisions}


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="Document exceeds the 10 MiB limit")
    return data


@app.post("/api/v1/documents/extract", tags=["documents"])
async def extract_document(file: UploadFile = File(...), principal: Principal = Depends(require_role("analyst"))):
    try:
        result = process_document_bytes(await _read_upload(file), file.filename or "document", file.content_type or "")
    except DocumentProcessingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    operations.log(principal.subject, principal.role, "document.extract", result["sha256"], {"filename": result["filename"], "authenticity": result["authenticity"]["status"]})
    return result


def _run_document_job(job_id: str, data: bytes, filename: str, content_type: str) -> None:
    try:
        operations.finish_job(job_id, result=process_document_bytes(data, filename, content_type))
    except Exception as exc:
        operations.finish_job(job_id, error=type(exc).__name__)


@app.post("/api/v1/documents/process", status_code=202, tags=["documents"])
async def queue_document(background_tasks: BackgroundTasks, file: UploadFile = File(...), principal: Principal = Depends(require_role("analyst"))):
    data = await _read_upload(file)
    job = operations.create_job("document_processing")
    background_tasks.add_task(_run_document_job, job["id"], data, file.filename or "document", file.content_type or "")
    operations.log(principal.subject, principal.role, "document.queue", job["id"], {"filename": file.filename})
    return job


@app.get("/api/v1/jobs/{job_id}", tags=["operations"])
def get_job(job_id: str, principal: Principal = Depends(require_role("viewer"))):
    job = operations.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _workflow_call(action):
    try:
        return action()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v1/rfqs", status_code=201, tags=["commercial"])
def create_rfq(payload: RFQCreate, principal: Principal = Depends(require_role("analyst"))):
    return commercial.create_rfq(payload, principal.subject, principal.role)


@app.post("/api/v1/rfqs/{rfq_id}/dispatch", tags=["commercial"])
def dispatch_rfq(rfq_id: str, principal: Principal = Depends(require_role("operator"))):
    return _workflow_call(lambda: commercial.dispatch_rfq(rfq_id, principal.subject, principal.role))


@app.post("/api/v1/rfqs/{rfq_id}/responses", status_code=201, tags=["commercial"])
def carrier_response(rfq_id: str, payload: CarrierResponseCreate, principal: Principal = Depends(require_role("operator"))):
    return _workflow_call(lambda: commercial.submit_response(rfq_id, payload, principal.subject, principal.role))


@app.post("/api/v1/approvals", status_code=201, tags=["commercial"])
def approve_commercial(payload: ApprovalCreate, principal: Principal = Depends(require_role("approver"))):
    return _workflow_call(lambda: commercial.approve(payload, principal.subject, principal.role))


@app.post("/api/v1/bookings", status_code=201, tags=["commercial"])
def booking_intent(payload: BookingIntentCreate, principal: Principal = Depends(require_role("operator"))):
    return _workflow_call(lambda: commercial.create_booking_intent(payload, principal.subject, principal.role))


@app.post("/api/v1/purchase-orders", status_code=201, tags=["commercial"])
def purchase_order(payload: PurchaseOrderCreate, principal: Principal = Depends(require_role("operator"))):
    return commercial.create_purchase_order(payload, principal.subject, principal.role)


@app.post("/api/v1/bookings/{booking_id}/tracking", tags=["commercial"])
def add_tracking(booking_id: str, payload: TrackingEventCreate, principal: Principal = Depends(require_role("operator"))):
    return _workflow_call(lambda: commercial.add_tracking_event(booking_id, payload, principal.subject, principal.role))


@app.get("/api/v1/commercial/{record_id}", tags=["commercial"])
def commercial_record(record_id: str, principal: Principal = Depends(require_role("viewer"))):
    record = operations.get_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Commercial record not found")
    return record


@app.post("/api/v1/notifications/{notification_id}/send", tags=["commercial"])
def send_notification(notification_id: str, principal: Principal = Depends(require_role("operator"))):
    record = operations.get_record(notification_id)
    if not record or record["kind"] != "notification":
        raise HTTPException(status_code=404, detail="Notification not found")
    if record["status"] != "queued":
        raise HTTPException(status_code=409, detail="Only queued notifications can be sent")
    payload = dict(record["payload"])
    if not payload.get("recipient"):
        raise HTTPException(status_code=409, detail="Notification has no configured email recipient")
    try:
        result = notifications.send(payload["recipient"], payload["subject"], payload["body"])
    except NotificationConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    payload["external_send"] = True
    payload["delivery_result"] = result
    updated = operations.update_record(notification_id, "sent", payload)
    operations.log(principal.subject, principal.role, "notification.send", notification_id, {"channel": "smtp"})
    return updated


@app.post("/api/v1/evaluations/decision-quality", tags=["evaluation"])
def decision_quality(payload: EvaluationRequest, principal: Principal = Depends(require_role("analyst"))):
    result = evaluate_decisions(payload.records)
    operations.log(principal.subject, principal.role, "evaluation.run", "decision-quality", {"records": len(payload.records), "provenance": result["provenance"]})
    return result


@app.get("/api/v1/audit", tags=["operations"])
def audit_log(limit: int = 100, principal: Principal = Depends(require_role("admin"))):
    return {"events": operations.audit_events(max(1, min(limit, 1000)))}


@app.get("/api/v1/metrics", response_class=PlainTextResponse, tags=["operations"])
def prometheus_metrics(principal: Principal = Depends(require_role("admin"))):
    return metrics.prometheus()


@app.post("/api/v1/admin/backups", tags=["operations"])
def create_backup(principal: Principal = Depends(require_role("admin"))):
    if not isinstance(store, ShipmentStore):
        raise HTTPException(status_code=501, detail="Use managed PostgreSQL backups or pg_dump for the configured backend")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = store.backup(f"./data/backups/tradepilot-{stamp}.sqlite3")
    operations.log(principal.subject, principal.role, "backup.create", path, {})
    return {"status": "created", "path": path, "backend": store.persistence_label}
