from tradepilot.models import ShipmentRequest, Quote
from tradepilot.agents.route import RouteOptimizationAgent
from tradepilot.agents.carrier import CarrierResearchAgent
from tradepilot.agents.risk import RiskComplianceAgent
from tradepilot.agents.documents import DocumentIntelligenceAgent
from tradepilot.agents.procurement import MarketProcurementAgent

def shipment(**kwargs):
    values=dict(origin="Mumbai",destination="Singapore",cargo_description="machine parts",weight_kg=1000,allowed_modes=["air","sea"])
    values.update(kwargs); return ShipmentRequest(**values)

def test_route_agent_returns_feasible_recommendation():
    result=RouteOptimizationAgent().run(shipment())
    assert result.recommended_route.mode in {"air","sea"}

def test_carrier_agent_uses_synthetic_records_and_capacity():
    result=CarrierResearchAgent().run(shipment(weight_kg=40000),{"sea","air"})
    assert all(c.capacity_available for c in result.carriers)
    assert result.provenance=="SYNTHETIC" and result.excluded_capacity==1

def test_risk_agent_flags_hazardous_cargo_and_missing_incoterm():
    result=RiskComplianceAgent().run(shipment(cargo_description="flammable chemical"))
    codes={flag.code for flag in result.flags}
    assert "DANGEROUS_GOODS_REVIEW" in codes and "INCOTERM_NOT_PROVIDED" in codes
    assert result.live_screening is False

def test_document_agent_marks_missing_documents():
    result=DocumentIntelligenceAgent().run(shipment())
    assert result.valid is False and result.documents_checked==0

def test_procurement_agent_needs_human_approval():
    s=shipment(quotes=[Quote(carrier_id="Q1",mode="sea",amount=100)])
    result=MarketProcurementAgent().run(s,{"sea"})
    assert result.recommendation.requires_human_approval


def test_procurement_with_no_feasible_mode_recommends_nothing():
    s=shipment(quotes=[Quote(carrier_id="Q1",mode="sea",amount=100)])
    result=MarketProcurementAgent().run(s,set())
    assert result.eligible_quotes==0 and result.recommendation is None

def test_gemini_quota_failure_falls_back_safely(monkeypatch):
    import os
    from tradepilot.gemini import GeminiNarrator
    from google import genai
    class ResourceExhausted(Exception): pass
    class BrokenClient:
        def __init__(self,*args,**kwargs): raise ResourceExhausted("quota details must not leak")
    monkeypatch.setenv("GEMINI_ENABLED","true")
    monkeypatch.setenv("GEMINI_API_KEY","unit-test-only")
    monkeypatch.setattr(genai,"Client",BrokenClient)
    result=GeminiNarrator().narrate({"route":{"cost":42}})
    assert result["status"]=="quota_or_rate_limited"
    assert "quota details" not in result["message"]
