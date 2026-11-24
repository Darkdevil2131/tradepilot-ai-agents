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

def test_route_agent_rejects_cross_border_road_as_recommendation():
    result=RouteOptimizationAgent().run(shipment(allowed_modes=["road"]))
    assert result.recommended_route is None
    assert result.no_feasible_route is True
    assert result.routes[0].mode_feasible is False

def test_connected_route_has_leg_evidence():
    result=RouteOptimizationAgent().run(shipment(origin="Pune", destination="London", allowed_modes=["road", "air", "sea"]))
    assert result.recommended_route is not None
    assert result.recommended_route.legs[0]["from"] == "pune"
    assert result.recommended_route.legs[-1]["to"] == "london"
    assert result.recommended_route.evidence_ids

def test_carrier_agent_uses_synthetic_records_and_capacity():
    result=CarrierResearchAgent().run(shipment(weight_kg=40000),{"sea","air"})
    assert all(c.capacity_available for c in result.carriers)
    assert result.provenance=="SYNTHETIC" and result.excluded_capacity==1

def test_carrier_agent_does_not_claim_domestic_road_coverage_for_cross_border_route():
    result=CarrierResearchAgent().run(shipment(allowed_modes=["air","sea","road"]),{"air","sea","road"})
    assert all(c.mode!="road" for c in result.carriers)
    assert result.excluded_coverage==1

def test_carrier_agent_allows_demo_road_fixture_for_domestic_india_route():
    result=CarrierResearchAgent().run(shipment(origin="Mumbai",destination="Delhi",allowed_modes=["road"]),{"road"})
    assert [c.mode for c in result.carriers]==["road"]
    assert result.carriers[0].coverage_match

def test_risk_agent_flags_hazardous_cargo_and_missing_incoterm():
    result=RiskComplianceAgent().run(shipment(cargo_description="flammable chemical"))
    codes={flag.code for flag in result.flags}
    assert "DANGEROUS_GOODS_REVIEW" in codes and "INCOTERM_NOT_PROVIDED" in codes
    assert result.live_screening is False

def test_risk_agent_flags_lithium_ion_battery_variants():
    result=RiskComplianceAgent().run(shipment(cargo_description="Li-ion batteries"))
    assert "DANGEROUS_GOODS_REVIEW" in {flag.code for flag in result.flags}

def test_risk_agent_uses_route_feasibility_evidence():
    result=RiskComplianceAgent().run(shipment(), [{"feasible":False}])
    assert "NO_FEASIBLE_ROUTE" in {flag.code for flag in result.flags}

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

def test_procurement_does_not_compare_quote_amounts_across_currencies():
    s=shipment(quotes=[Quote(carrier_id="USD",mode="sea",amount=1000,currency="USD"),Quote(carrier_id="EUR",mode="sea",amount=10,currency="EUR")])
    result=MarketProcurementAgent().run(s,{"sea"})
    assert result.recommendation is None
    assert "multiple currencies" in result.recommendation_warning

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


def test_gemini_prompt_leads_with_blocked_decision(monkeypatch):
    from tradepilot.gemini import GeminiNarrator
    from google import genai

    captured = {}

    class Response:
        text = "blocked: documents must be supplied before approval."

    class Models:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return Response()

    class Client:
        def __init__(self, *args, **kwargs):
            self.models = Models()

    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "unit-test-only")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setattr(genai, "Client", Client)

    evidence = {
        "workflow_decision": {
            "status": "blocked",
            "gates": [{"gate": "documents", "status": "blocked"}],
        },
        "specialists": {"route_optimization": {"preferred": "air/ocean"}},
    }
    result = GeminiNarrator().narrate(evidence)

    assert result["status"] == "completed"
    assert result["model"] == "gemini-3.5-flash-lite"
    prompt = captured["contents"]
    assert "first sentence must start with the exact workflow decision status" in prompt
    assert prompt.index('"workflow_decision"') < prompt.index('"specialists"')
