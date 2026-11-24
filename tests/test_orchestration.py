from tradepilot.models import ShipmentRequest
from tradepilot.agents.orchestrator import LogisticsOrchestrator

def make_shipment(): return ShipmentRequest(origin="Mumbai",destination="Singapore",cargo_description="parts",weight_kg=300,allowed_modes=["air","sea"])

def test_full_graph_runs_all_six_and_checks_missing_documents():
    result=LogisticsOrchestrator().analyze(make_shipment())
    assert result.status=="completed"
    assert result.agents["route_optimization"].status=="completed"
    assert result.agents["document_intelligence"].status=="completed"
    assert result.agents["document_intelligence"].data["missing_document_types"]==["commercial_invoice","packing_list","bill_of_lading"]
    assert result.agents["logistics_orchestrator"].data["workflow"]=="LangGraph StateGraph"

def test_graph_retries_bounded_and_returns_safe_partial_result(monkeypatch):
    orchestrator=LogisticsOrchestrator(max_attempts=2)
    calls={"count":0}
    def broken(_):
        calls["count"]+=1
        raise RuntimeError("sensitive internal details")
    monkeypatch.setattr(orchestrator.route,"run",broken)
    result=orchestrator.analyze(make_shipment())
    assert calls["count"]==2
    assert result.status=="partial"
    error=result.agents["route_optimization"].warnings[0]
    assert "sensitive internal details" not in error
    assert result.agents["logistics_orchestrator"].data["retry_counts"]["route_optimization"]==2


def test_unrealistic_deadline_does_not_recommend_a_quote():
    from datetime import datetime,timezone,timedelta
    from tradepilot.models import Quote
    s=make_shipment().model_copy(update={"deadline":datetime.now(timezone.utc)+timedelta(minutes=5),"quotes":[Quote(carrier_id="Q1",mode="sea",amount=100)]})
    result=LogisticsOrchestrator().analyze(s)
    assert result.agents["market_procurement"].data["eligible_quotes"]==0
    assert result.agents["market_procurement"].data["recommendation"] is None
    assert result.agents["carrier_research"].data["carriers"]==[]
