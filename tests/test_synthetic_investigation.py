import pytest
from tradepilot.agents.orchestrator import LogisticsOrchestrator
from tradepilot.models import ShipmentRequest

graph = LogisticsOrchestrator()

@pytest.mark.parametrize("weight", [50, 9999, 10000, 10001, 40000, 500000, 500001])
@pytest.mark.parametrize("cargo", ["parts", "Li-ion batteries"])
def test_provider_decision(weight, cargo):
    request = ShipmentRequest(origin="Mumbai", destination="Singapore", cargo_description=cargo,
        weight_kg=weight, quotes=[dict(carrier_id=c, mode=m, amount=a, provenance="SYNTHETIC")
        for c,m,a in [("UNKNOWN","air",1),("DEMO-AIR-01","air",100),("DEMO-SEA-01","sea",200)]])
    result = graph.analyze(request)
    assert all(agent.status == "completed" for agent in result.agents.values())
    offer = result.agents["market_procurement"].data["recommendation"]
    expected = None if cargo != "parts" or weight > 500000 else "DEMO-AIR-01" if weight <= 10000 else "DEMO-SEA-01"
    assert (offer["carrier_id"] if offer else None) == expected
    for carrier in result.agents["carrier_research"].data["carriers"]:
        assert carrier["history_count"] == 80
        assert carrier["reliability_score"] == carrier["on_time_count"] / 80

def test_unknown_history():
    result = graph.analyze(ShipmentRequest(origin="Singapore", destination="Mumbai", weight_kg=1,
                                           cargo_description="parts", allowed_modes=["air"]))
    carrier = result.agents["carrier_research"].data["carriers"][0]
    assert carrier["history_count"] == 0 and carrier["reliability_score"] is None
