from __future__ import annotations
import logging, os, secrets, time, threading
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
load_dotenv()
from .agents.orchestrator import LogisticsOrchestrator
from .gemini import GeminiNarrator
from .models import DocumentValidationRequest, DocumentValidationResponse, ShipmentAnalysis, ShipmentRequest
from .storage import ShipmentStore
from .tools import validate_documents

logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"))
logger=logging.getLogger("tradepilot")
store=ShipmentStore()
orchestrator=LogisticsOrchestrator()
narrator=GeminiNarrator()
_rate_lock=threading.Lock()
_request_windows=defaultdict(deque)

@asynccontextmanager
async def lifespan(app:FastAPI):
    logger.info("TradePilot API started; gemini_enabled=%s storage=%s",narrator.enabled,store.db_path)
    yield
app=FastAPI(title="TradePilot AI Multi-Agent Backend",version="1.0.0",description="Offline-capable shipment decision support. Logistics, carrier, compliance, and market data are simulated or estimated unless explicitly tagged otherwise.",lifespan=lifespan)

@app.middleware("http")
async def request_logging(request:Request,call_next):
    start=time.monotonic()
    # Per-process request quota protects the demo API; it is not a global multi-instance limiter.
    if request.url.path != "/api/v1/health":
        identity=request.client.host if request.client else "unknown"
        now=time.monotonic()
        limit=max(1,int(os.getenv("API_RATE_LIMIT_PER_MINUTE","60")))
        with _rate_lock:
            window=_request_windows[identity]
            while window and now-window[0]>=60: window.popleft()
            if len(window)>=limit:
                retry=max(1,int(60-(now-window[0])))
                return JSONResponse(status_code=429,content={"detail":"Request quota exceeded; retry later"},headers={"Retry-After":str(retry)})
            window.append(now)
    try:
        response=await call_next(request)
        logger.info("request method=%s path=%s status=%s duration_ms=%d",request.method,request.url.path,response.status_code,round((time.monotonic()-start)*1000))
        return response
    except Exception:
        logger.exception("unhandled request error path=%s",request.url.path)
        return JSONResponse(status_code=500,content={"detail":"Internal server error"})

def require_api_key(authorization:str|None=Header(default=None)):
    expected=os.getenv("TRADEPILOT_API_KEY")
    if not expected:
        raise HTTPException(status_code=503,detail="API authentication is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401,detail="Missing bearer API key",headers={"WWW-Authenticate":"Bearer"})
    supplied=authorization[7:].strip()
    if not secrets.compare_digest(supplied,expected):
        raise HTTPException(status_code=401,detail="Invalid API key",headers={"WWW-Authenticate":"Bearer"})

@app.get("/api/v1/health",tags=["health"])
async def health():
    return {"status":"ok","mode":"offline_deterministic","gemini_enabled":narrator.enabled,"storage":"sqlite_ephemeral_or_local","live_logistics_feeds":False}

@app.post("/api/v1/shipments/analyze",response_model=ShipmentAnalysis,tags=["shipments"],dependencies=[Depends(require_api_key)])
async def analyze_shipment(payload:ShipmentRequest):
    try:
        analysis=orchestrator.analyze(payload)
        # Narrative generation is optional and cannot alter calculated values or agent outputs.
        narrative=narrator.narrate({k:v.data for k,v in analysis.agents.items() if v.status=="completed"})
        analysis.agents["logistics_orchestrator"].data["gemini_narrative"]=narrative
        store.save(analysis)
        return analysis
    except Exception:
        logger.exception("shipment analysis failed")
        raise HTTPException(status_code=500,detail="Shipment analysis could not be completed")

@app.get("/api/v1/shipments/{shipment_id}",response_model=ShipmentAnalysis,tags=["shipments"],dependencies=[Depends(require_api_key)])
async def get_shipment(shipment_id:str):
    result=store.get(shipment_id)
    if not result: raise HTTPException(status_code=404,detail="Shipment analysis not found in this database instance")
    return result

@app.post("/api/v1/documents/validate",response_model=DocumentValidationResponse,tags=["documents"],dependencies=[Depends(require_api_key)])
async def validate_shipment_documents(payload:DocumentValidationRequest):
    result=validate_documents(payload.shipment)
    return DocumentValidationResponse(shipment_id=payload.shipment.shipment_id,**result)
