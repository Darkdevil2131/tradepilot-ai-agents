from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from tradepilot.agents.route import RouteOptimizationAgent
from tradepilot.models import ShipmentRequest


@pytest.mark.parametrize(("origin", "destination"), [
    ("Cochin", "Madras"), ("Chennai", "Kochi"), ("Kochi", "Bengaluru"),
    ("Mumbai", "Ahmedabad"), ("Hyderabad", "Bangalore"),
    ("Kolkata", "Bhubaneswar"), ("Visakhapatnam", "Chennai"),
])
def test_connected_domestic_routes(origin, destination):
    shipment = ShipmentRequest(origin=origin, destination=destination, cargo_description="parts",
                               weight_kg=500, allowed_modes=["air", "sea", "road", "rail"])
    result = RouteOptimizationAgent().run(shipment)
    assert result.recommended_route is not None
    assert {"air", "road", "rail"} <= {route.mode for route in result.routes if route.feasible}
    for route in result.routes:
        assert route.provenance == ("ESTIMATED" if route.mode == "road" else "SYNTHETIC")
        assert route.cost_estimate_inr > 0
        assert route.legs[0]["from"] == shipment.origin.casefold()
        assert route.legs[-1]["to"] == shipment.destination.casefold()
        assert all(first["to"] == second["from"] for first, second in zip(route.legs, route.legs[1:]))


def test_kochi_end_to_end(client, headers):
    data = json.loads(Path("examples/intelligent_shipment.json").read_text())
    data.update(origin="Kochi", destination="Chennai", shipment_id="KOCHI-TEST", weight_kg=500,
                allowed_modes=["air", "sea", "road", "rail"],
                impact_scenario={"critical_weight_kg": 100, "target_days": 1.25, "daily_stoppage_inr": 80000})

    def analyze():
        response = client.post("/api/v1/shipments/analyze", json=data, headers=headers)
        assert response.status_code == 200, response.text
        agents = response.json()["agents"]
        assert len(agents) == 6 and all(agent["status"] == "completed" for agent in agents.values())
        decision = agents["logistics_orchestrator"]["data"]["decision"]
        assert decision["status"] == "ready_for_human_approval"
        return agents["route_optimization"]["data"]["impact_comparison"], decision["candidate"]

    comparison, plan = analyze()
    assert plan["option"] == "road-1/ocean"
    assert plan["freight_inr"] == 5555.3
    assert [portion["weight_kg"] for portion in comparison["options"][0]["portions"]] == [100, 400]
    assert all(item["carrier_id"] for item in plan["legs"])
    data["disruptions"] = [{"signal_id": "port-closure", "affected_modes": ["sea"],
                            "affected_locations": ["Chennai"], "severity": 1}]
    comparison, plan = analyze()
    assert plan["option"] == "road-1/rail"
    assert plan["freight_inr"] == 5955.3
    assert all("ocean" not in row["option"] for row in comparison["options"])
    assert any("disruption:port-closure" in row["reasons"] for row in comparison["rejected"])


def test_tight_deadline_and_air_capacity_exclude_infeasible_options():
    shipment = ShipmentRequest(origin="Kochi", destination="Chennai", cargo_description="parts",
                               weight_kg=2000, allowed_modes=["air", "sea", "road", "rail"],
                               deadline=datetime.now(timezone.utc) + timedelta(hours=54))
    result = RouteOptimizationAgent().run(shipment)
    assert {route.mode for route in result.routes if route.feasible} == {"road"}


@pytest.mark.parametrize(("origin", "destination"), [
    ("Delhi", "Kolkata"), ("Mumbai", "Bengaluru"), ("Srinagar", "Kanyakumari"),
    ("Porbandar", "Silchar"), ("Chennai", "Bhubaneswar"), ("Bhopal", "Hyderabad"),
])
def test_long_distance_road_routes_have_distinct_connected_alternatives(origin, destination):
    shipment = ShipmentRequest(origin=origin, destination=destination, cargo_description="parts",
                               weight_kg=500, allowed_modes=["road"])
    result = RouteOptimizationAgent().run(shipment)
    paths = [tuple(route.evidence_ids) for route in result.routes if route.feasible]
    assert len(paths) > 1 and len(paths) == len(set(paths))
    for route in result.routes:
        assert route.distance_km > 0 and route.source_references
        assert all(first["to"] == second["from"] for first, second in zip(route.legs, route.legs[1:]))


def test_specific_road_closure_switches_kochi_to_detour():
    data = dict(origin="Kochi", destination="Chennai", cargo_description="parts",
                weight_kg=500, allowed_modes=["road"])
    baseline = RouteOptimizationAgent().run(ShipmentRequest(**data)).recommended_route
    closed_leg = "IN-ROAD-SALEM-KRISHNAGIRI"
    assert closed_leg in baseline.evidence_ids
    data["disruptions"] = [dict(signal_id="road-closure", affected_leg_ids=[closed_leg], severity=1)]
    result = RouteOptimizationAgent().run(ShipmentRequest(**data))
    assert result.recommended_route is not None
    assert all(closed_leg not in option.evidence_ids for option in result.routes if option.feasible)
    assert any(leg["to"] == "madurai" for leg in result.recommended_route.legs)


def test_inland_carrier_offer_and_road_transport_document(client, headers):
    data = json.loads(Path("examples/intelligent_shipment.json").read_text())
    data.update(origin="Delhi", destination="Kolkata", weight_kg=500, allowed_modes=["road"],
                deadline=(datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
                carrier_services=[dict(carrier_id="SOURCED-01", name="Submitted carrier", mode="road",
                                       origin="Delhi", destination="Kolkata", capacity_kg=1000,
                                       source_reference="Operator lane reference TP-001")])
    route = RouteOptimizationAgent().run(ShipmentRequest(**data)).recommended_route
    quote = dict(carrier_id="SOURCED-01", mode="road", currency="INR", amount=25000,
                 source_reference="Operator bid TP-002", route_leg_ids=route.evidence_ids,
                 transit_hours=48, quoted_capacity_kg=700, door_to_door_total=True,
                 valid_until=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
    data["quotes"] = [
        dict(quote, amount=5000, transit_hours=200),
        dict(quote, amount=5500, route_leg_ids=["UNRELATED-ROAD-LEG"]),
        dict(quote, amount=6000, quoted_capacity_kg=100),
        dict(quote, amount=6500, valid_until=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat()),
        dict(quote, amount=7000, door_to_door_total=False),
        dict(carrier_id="DEMO-ROAD-01", mode="road", currency="INR", amount=1),
        quote,
    ]
    data["documents"][2] = {"document_type": "consignment_note",
                            "raw_text": "Consignment Number: DEMO-1\nConsignee: Buyer Ltd"}
    response = client.post("/api/v1/shipments/analyze", json=data, headers=headers)
    assert response.status_code == 200, response.text
    agents = response.json()["agents"]
    assert all(agent["status"] == "completed" for agent in agents.values())
    carrier = agents["carrier_research"]["data"]["recommended_carrier"]
    assert (carrier["carrier_id"], carrier["history_confidence"], carrier["contact"]) == ("SOURCED-01", "unknown", {})
    decision = agents["logistics_orchestrator"]["data"]["decision"]
    assert decision["status"] == "ready_for_human_approval"
    assert decision["commercial_evidence"] == "submitted"
    assert decision["candidate"]["amount"] == 25000
    assert decision["candidate"]["evidence"]["route_leg_ids"] == route.evidence_ids
    assessments = agents["market_procurement"]["data"]["quote_assessments"]
    assert [row["reasons"] for row in assessments] == [
        ["quoted_transit_misses_deadline"], ["quote_route_mismatch"],
        ["quote_capacity_unconfirmed_or_insufficient"], ["quote_expired"],
        ["door_to_door_scope_unconfirmed"], ["demo_carrier_excluded_from_submitted_offer_comparison"], [],
    ]
    data["disruptions"] = [dict(signal_id="required-leg-closed", severity=1,
                                affected_leg_ids=[route.evidence_ids[0]])]
    changed = client.post("/api/v1/shipments/analyze", json=data, headers=headers).json()["agents"]
    assert changed["route_optimization"]["data"]["recommended_route"] is not None
    assert changed["logistics_orchestrator"]["data"]["decision"]["candidate"] is None
    assert "quote_route_mismatch" in changed["market_procurement"]["data"]["quote_assessments"][-1]["reasons"]
    data.pop("disruptions")
    data.update(commercial_mode="submitted", carrier_services=[], quotes=[data["quotes"][-2]])
    without_evidence = client.post("/api/v1/shipments/analyze", json=data, headers=headers).json()["agents"]
    assert without_evidence["logistics_orchestrator"]["data"]["decision"]["commercial_evidence"] == "unavailable"
    assert without_evidence["market_procurement"]["data"]["eligible_quotes"] == 0
    data["documents"][0]["fields"] = {"total": 99999}
    conflict = client.post("/api/v1/documents/validate", json={"shipment": data}, headers=headers).json()
    assert not conflict["valid"] and any("differs between document text" in issue for issue in conflict["issues"])


def test_raw_text_validation_matches_shipment_workflow(client, headers):
    data = json.loads(Path("examples/intelligent_shipment.json").read_text())
    response = client.post("/api/v1/documents/validate", json={"shipment": data}, headers=headers)
    assert response.status_code == 200 and response.json()["valid"]
