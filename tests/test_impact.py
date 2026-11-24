from tradepilot.agents.orchestrator import LogisticsOrchestrator
from tradepilot.models import ShipmentRequest


def comparison(**changes):
    assumptions = dict(critical_weight_kg=100, target_days=5, daily_stoppage_inr=80000)
    assumptions.update(changes)
    shipment = ShipmentRequest(origin="Dubai", destination="Mumbai", cargo_description="parts",
                               weight_kg=500, impact_scenario=assumptions)
    return LogisticsOrchestrator().analyze(shipment).agents["route_optimization"].data["impact_comparison"]


def test_split_and_cost_of_delay():
    result = comparison()
    first = result["options"][0]
    assert first["option"] == "air/ocean"
    assert [p["weight_kg"] for p in first["portions"]] == [100,400]
    assert first["total_impact_inr"] == 78000
    assert comparison(daily_stoppage_inr=0)["preferred"] == "ocean"
    assert comparison(freight_multiplier=1.2)["options"][0]["freight_inr"] == 93600


def test_closed_legs_and_shared_pickup():
    assert comparison(closed_leg_ids=["ocean"])["preferred"] == "air"
    assert comparison(closed_leg_ids=["pickup"])["options"] == []
