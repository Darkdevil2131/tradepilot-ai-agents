from datetime import datetime, timedelta, timezone

from tradepilot.agents.orchestrator import LogisticsOrchestrator
from tradepilot.agents.route import RouteOptimizationAgent
from tradepilot.models import ShipmentRequest


def test_sea_catalog_does_not_invent_a_port_link_and_respects_via_closure():
    agent = RouteOptimizationAgent()
    shipment = ShipmentRequest(origin="Mumbai", destination="Rotterdam", allowed_modes=["sea"],
                               weight_kg=500, cargo_description="Machine parts")
    route = agent.run(shipment).recommended_route
    assert route is not None
    assert route.legs[0]["from"] == "mumbai" and route.legs[-1]["to"] == "rotterdam"
    assert any("Suez Canal" in leg["via"] for leg in route.legs)
    assert route.source_references and route.provenance == "SYNTHETIC"
    affected = shipment.model_dump()
    affected["disruptions"] = [dict(signal_id="canal-closure", affected_modes=["sea"],
                                    affected_locations=["Suez Canal"], severity=1)]
    assert agent.run(ShipmentRequest(**affected)).no_feasible_route
    no_catalog = shipment.model_copy(update={"destination": "Los Angeles"})
    assert agent.run(no_catalog).no_feasible_route


def test_published_carrier_lead_is_separate_from_a_quote():
    shipment = ShipmentRequest(origin="Kochi", destination="Colombo", allowed_modes=["sea"], weight_kg=500,
                               cargo_description="Machine parts", commercial_mode="submitted")
    agents = LogisticsOrchestrator().analyze(shipment).agents
    carrier = agents["carrier_research"].data
    assert carrier["carriers"] == [] and carrier["recommended_carrier"] is None
    assert carrier["published_services"][0]["availability"] == "unverified"
    assert carrier["published_services"][0]["quote"] is None
    assert agents["logistics_orchestrator"].data["decision"]["candidate"] is None


def test_submitted_leg_quotes_trigger_bounded_reconsideration():
    data = dict(origin="Kochi", destination="Chennai", weight_kg=500, cargo_description="Machine parts",
                allowed_modes=["road", "rail", "air"],
                impact_scenario=dict(critical_weight_kg=100, target_days=1.25, daily_stoppage_inr=80000),
                documents=[
                    dict(document_type="commercial_invoice", fields=dict(
                        invoice_number="INV-1", shipper="Seller", consignee="Buyer", currency="INR", total=20000)),
                    dict(document_type="packing_list", fields=dict(package_count=4)),
                    dict(document_type="air_waybill", fields=dict(air_waybill_number="DEMO-AWB", consignee="Buyer")),
                    dict(document_type="consignment_note", fields=dict(consignment_number="DEMO-CN", consignee="Buyer")),
                ])
    orchestrator = LogisticsOrchestrator()
    routes = orchestrator.route.run(ShipmentRequest(**data)).impact_comparison
    assert routes["preferred"] != "air"
    air = next(option for option in routes["options"] if option["option"] == "air")
    expiry = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    data.update(commercial_mode="submitted", carrier_services=[], quotes=[])
    for leg in air["portions"][0]["legs"]:
        leg_ids = [leg["id"]]
        data["carrier_services"].append(dict(carrier_id="SUBMITTED", name="Test Evidence Carrier", mode=leg["mode"],
            origin=leg["origin"], destination=leg["destination"], source_reference="Fixture lane contract",
            capacity_kg=600, route_leg_ids=leg_ids))
        data["quotes"].append(dict(carrier_id="SUBMITTED", mode=leg["mode"], currency="INR", amount=700,
            valid_until=expiry, source_reference="Fixture bid", route_leg_ids=leg_ids, leg_total=True,
            transit_hours=leg["base_transit_hours"], quoted_capacity_kg=600))
    analysis = orchestrator.analyze(ShipmentRequest(**data))
    result = analysis.agents["logistics_orchestrator"].data
    assert result["verification"]["reconsidered"] is True
    decision = result["decision"]
    assert analysis.agents["market_procurement"].data["eligible_quotes"] == 3
    assert all(item["eligible"] for item in analysis.agents["market_procurement"].data["quote_assessments"])
    assert decision["candidate"]["option"] == "air"
    assert decision["candidate"]["freight_inr"] == 2100
    assert decision["commercial_evidence"] == "submitted"
    assert decision["requires_human_approval"]
    data["documents"].pop(2)
    analysis = orchestrator.analyze(ShipmentRequest(**data))
    assert analysis.agents["logistics_orchestrator"].data["decision"]["candidate"] is None
    assert "air_waybill" in analysis.agents["document_intelligence"].data["missing_document_types"]

    rail = next(option for option in routes["options"] if option["option"] == "rail")
    leg = next(row for row in rail["portions"][0]["legs"] if row["mode"] == "rail")
    data["carrier_services"].append(dict(carrier_id="SUBMITTED", name="Test Evidence Carrier",
        mode="rail", origin=leg["origin"], destination=leg["destination"],
        source_reference="Fixture rail agreement", capacity_kg=600, route_leg_ids=[leg["id"]]))
    data["quotes"].append(dict(carrier_id="SUBMITTED", mode="rail", currency="INR", amount=2000,
        valid_until=expiry, source_reference="Fixture rail bid", route_leg_ids=[leg["id"]], leg_total=True,
        transit_hours=leg["base_transit_hours"], quoted_capacity_kg=600))
    result = orchestrator.analyze(ShipmentRequest(**data)).agents["logistics_orchestrator"].data
    assert result["verification"]["initial_missing_transport_documents"] == ["air_waybill"]
    assert result["decision"]["candidate"]["option"] == "rail"


def test_contradicting_invoice_lines_block_approval():
    shipment = ShipmentRequest(origin="Kochi", destination="Chennai", weight_kg=500,
        cargo_description="Machine parts", documents=[
            dict(document_type="commercial_invoice", raw_text=(
                "Invoice No: I-1\nShipper: Seller\nConsignee: Buyer\nCurrency: INR\nTotal: 1,20,000\nTotal: 2,20,000")),
            dict(document_type="packing_list", fields=dict(package_count=2)),
            dict(document_type="bill_of_lading", fields=dict(consignee="Buyer")),
        ])
    result = LogisticsOrchestrator().analyze(shipment).agents["document_intelligence"].data
    assert result["authenticity"] == "unverified"
    assert not result["valid"]
    assert any("contradictory labelled values for total" in issue for issue in result["issues"])
