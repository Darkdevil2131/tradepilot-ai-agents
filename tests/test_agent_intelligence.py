# Decision-quality regression tests for the six-agent workflow.
import pytest
from pydantic import ValidationError

from tradepilot.agents.carrier import CarrierResearchAgent
from tradepilot.agents.documents import DocumentIntelligenceAgent
from tradepilot.agents.procurement import MarketProcurementAgent
from tradepilot.agents.risk import RiskComplianceAgent
from tradepilot.agents.route import RouteOptimizationAgent
from tradepilot.models import ShipmentRequest


def shipment(**changes):
    payload = {
        "origin": "Mumbai",
        "destination": "Singapore",
        "cargo_description": "machine parts",
        "weight_kg": 1000,
        "allowed_modes": ["air", "sea"],
    }
    payload.update(changes)
    return ShipmentRequest(**payload)


def test_time_priority_changes_route_ranking():
    result = RouteOptimizationAgent().run(
        shipment(optimization={"cost_weight": 0, "time_weight": 1, "risk_weight": 0, "reliability_weight": 0})
    )
    assert result.recommended_route is not None
    assert result.recommended_route.mode == "air"
    assert result.recommended_route.decision_score == min(
        route.decision_score for route in result.routes if route.feasible
    )
    assert result.alternatives


def test_critical_disruption_blocks_affected_mode_and_changes_choice():
    result = RouteOptimizationAgent().run(
        shipment(
            optimization={"cost_weight": 0, "time_weight": 1, "risk_weight": 0, "reliability_weight": 0},
            disruptions=[{
                "signal_id": "AIRPORT-CLOSED",
                "affected_modes": ["air"],
                "affected_locations": ["Mumbai"],
                "severity": 0.99,
                "delay_hours": 48,
                "provenance": "USER_PROVIDED",
            }],
        )
    )
    assert result.recommended_route is not None
    assert result.recommended_route.mode == "sea"
    air = next(route for route in result.routes if route.mode == "air")
    assert not air.feasible
    assert air.disruption_ids == ["AIRPORT-CLOSED"]


def test_zero_optimization_weights_are_rejected():
    with pytest.raises(ValidationError):
        shipment(optimization={"cost_weight": 0, "time_weight": 0, "risk_weight": 0, "reliability_weight": 0})


def test_carrier_agent_returns_explainable_approval_candidate():
    result = CarrierResearchAgent().run(shipment(), {"air", "sea"})
    assert result.recommended_carrier is not None
    assert result.recommended_carrier["requires_human_approval"] is True
    assert result.recommended_carrier["provenance"] == "SYNTHETIC"
    assert result.recommended_carrier["contact"]["email"].endswith(".invalid")
    assert all(carrier.service_score is not None for carrier in result.carriers)


def test_supplier_ranking_enforces_quantity_and_certification():
    request = shipment(
        required_quantity=50,
        required_certifications=["ISO-9001"],
        supplier_offers=[
            {
                "supplier_id": "SUP-A",
                "name": "Synthetic Supplier A",
                "unit_price": 10,
                "currency": "USD",
                "lead_time_days": 5,
                "available_quantity": 100,
                "quality_score": 0.9,
                "origin": "Mumbai",
                "certifications": ["ISO-9001"],
                "provenance": "SYNTHETIC",
            },
            {
                "supplier_id": "SUP-B",
                "name": "Synthetic Supplier B",
                "unit_price": 1,
                "currency": "USD",
                "lead_time_days": 1,
                "available_quantity": 10,
                "quality_score": 1,
                "origin": "Delhi",
                "certifications": ["ISO-9001"],
                "provenance": "SYNTHETIC",
            },
        ],
    )
    result = MarketProcurementAgent().run(request, {"air", "sea"})
    assert result.supplier_recommendation["supplier_id"] == "SUP-A"
    assert result.supplier_recommendation["total_price"] == 500
    assert result.supplier_recommendation["requires_human_approval"] is True


def test_supplier_ranking_refuses_cross_currency_winner():
    request = shipment(
        supplier_offers=[
            {"supplier_id": "A", "name": "A", "unit_price": 10, "currency": "USD", "lead_time_days": 2, "available_quantity": 5, "quality_score": .9, "origin": "Mumbai"},
            {"supplier_id": "B", "name": "B", "unit_price": 1, "currency": "EUR", "lead_time_days": 2, "available_quantity": 5, "quality_score": .9, "origin": "Delhi"},
        ]
    )
    result = MarketProcurementAgent().run(request, {"air"})
    assert result.supplier_recommendation is None
    assert "multiple currencies" in result.supplier_warning


def test_document_agent_extracts_labelled_text_then_validates():
    request = shipment(
        declared_value=1000,
        declared_value_currency="USD",
        incoterm="CIF",
        documents=[
            {
                "document_type": "commercial_invoice",
                "raw_text": "Invoice Number: INV-7\nShipper: Seller Ltd\nConsignee: Buyer Ltd\nCurrency: USD\nTotal: 1000",
            },
            {"document_type": "packing_list", "raw_text": "Package Count: 4"},
            {"document_type": "bill_of_lading", "raw_text": "Consignee: Buyer Ltd"},
        ],
    )
    result = DocumentIntelligenceAgent().run(request)
    assert result.valid is True
    assert result.extracted_fields["commercial_invoice:0"]["invoice_number"] == "INV-7"
    assert result.extracted_fields["packing_list:1"]["package_count"] == 4


def test_high_disruption_is_exposed_by_risk_agent():
    request = shipment(disruptions=[{
        "signal_id": "PORT-STRIKE",
        "affected_modes": ["sea"],
        "severity": .85,
        "delay_hours": 72,
        "provenance": "USER_PROVIDED",
    }])
    routes = [route.model_dump() for route in RouteOptimizationAgent().run(request).routes]
    result = RiskComplianceAgent().run(request, routes)
    codes = {flag.code for flag in result.flags}
    assert "HIGH_DISRUPTION_SIGNAL" in codes
    assert result.disruption_summary[0]["signal_id"] == "PORT-STRIKE"
