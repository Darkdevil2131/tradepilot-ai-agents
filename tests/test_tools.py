from datetime import datetime,timezone,timedelta
import pytest
from tradepilot.models import Quote, ShipmentRequest, ShippingDocument
from tradepilot.tools import calculate_routes, compare_quotes, haversine_km, validate_documents

def test_route_tool_estimates_and_ranks_modes():
    s=ShipmentRequest(origin="Mumbai",destination="Singapore",cargo_description="parts",weight_kg=500,allowed_modes=["sea","air"])
    out=calculate_routes(s)
    assert {x["mode"] for x in out}=={"air","sea"}
    assert all(x["provenance"]=="SYNTHETIC" and x["legs"] for x in out)
    assert all(x["evidence_ids"] for x in out)

def test_route_unknown_city_fails_closed():
    with pytest.raises(ValueError,match="sample city list"):
        haversine_km("Bhopal","Tokyo")

def test_cross_border_road_is_not_reported_feasible():
    s=ShipmentRequest(origin="Mumbai",destination="Singapore",cargo_description="parts",weight_kg=500,allowed_modes=["road"])
    route=calculate_routes(s)[0]
    assert route["mode_feasible"] is False
    assert route["feasible"] is False
    assert "no cross-border road routing" in route["feasibility_reason"]

def test_connected_synthetic_network_rejects_missing_mode_and_capacity():
    s=ShipmentRequest(origin="Pune",destination="London",cargo_description="parts",weight_kg=9000,allowed_modes=["road","air","sea"])
    routes=calculate_routes(s)
    assert routes
    assert all(route["legs"][0]["from"]=="pune" for route in routes)
    assert all(route["legs"][-1]["to"]=="london" for route in routes)
    assert any("capacity:SIM-BOM-LON-AIR" in route["feasibility_reason"] for route in routes)

def test_domestic_road_has_explicit_estimated_corridor():
    s=ShipmentRequest(origin="Mumbai",destination="Delhi",cargo_description="parts",weight_kg=500,allowed_modes=["road"])
    route=calculate_routes(s)[0]
    assert route["mode_feasible"] is True
    assert route["provenance"] == "ESTIMATED"
    assert route["legs"][0]["from"] == "mumbai"
    assert route["legs"][-1]["to"] == "delhi"
    assert route["cost_estimate_inr"] > 0
    assert len(route["legs"]) > 1 and route["source_references"]

def test_deadline_marks_slow_routes_infeasible():
    s=ShipmentRequest(origin="Mumbai",destination="Singapore",cargo_description="parts",weight_kg=500,deadline=datetime.now(timezone.utc)+timedelta(hours=1))
    assert all(not r["deadline_feasible"] for r in calculate_routes(s))

def test_document_validation_incomplete_and_consistent():
    s=ShipmentRequest(origin="Mumbai",destination="Singapore",cargo_description="parts",weight_kg=100,declared_value=500,documents=[ShippingDocument(document_type="commercial_invoice",fields={"invoice_number":"I-1","shipper":"A","consignee":"B","currency":"USD","total":510})])
    result=validate_documents(s)
    assert "packing_list" in result["missing_document_types"]
    assert any("differs" in issue for issue in result["issues"])

def test_dangerous_goods_document_required():
    s=ShipmentRequest(origin="Mumbai",destination="Singapore",cargo_description="lithium battery packs",weight_kg=5)
    assert "dangerous_goods_declaration" in validate_documents(s)["missing_document_types"]

def test_dangerous_goods_document_required_for_lithium_ion_batteries():
    s=ShipmentRequest(origin="Mumbai",destination="Singapore",cargo_description="Li-ion batteries",weight_kg=5)
    assert "dangerous_goods_declaration" in validate_documents(s)["missing_document_types"]

def test_quote_comparison_only_keeps_live_user_quote_modes():
    quotes=[Quote(carrier_id="A",mode="sea",amount=500),Quote(carrier_id="B",mode="air",amount=600),Quote(carrier_id="OLD",mode="sea",amount=1,valid_until=datetime.now(timezone.utc)-timedelta(days=1))]
    result=compare_quotes(quotes,{"sea"})
    assert result["eligible_quotes"]==1 and result["recommendation"]["carrier_id"]=="A"
    assert result["recommendation"]["requires_human_approval"] is True


def test_infeasible_deadline_does_not_admit_quotes():
    quotes=[Quote(carrier_id="Q1",mode="sea",amount=1)]
    result=compare_quotes(quotes,set())
    assert result["eligible_quotes"]==0 and result["recommendation"] is None

def test_quotes_in_different_currencies_do_not_get_a_false_cheapest_pick():
    quotes=[Quote(carrier_id="USD-QUOTE",mode="sea",amount=1000,currency="USD"),Quote(carrier_id="EUR-QUOTE",mode="sea",amount=10,currency="EUR")]
    result=compare_quotes(quotes,{"sea"})
    assert result["eligible_quotes"]==2
    assert result["recommendation"] is None
    assert "multiple currencies" in result["recommendation_warning"]


def test_different_insurance_terms_need_comparable_prices():
    bare = Quote(carrier_id="A", mode="air", amount=95)
    insured = Quote(carrier_id="B", mode="air", amount=100, includes_insurance=True)
    result = compare_quotes([insured, bare], {"air"})
    assert result["eligible_quotes"] == 2
    assert result["recommendation"] is None
    assert "Insurance coverage differs" in result["recommendation_warning"]
    assert [row["includes_insurance"] for row in result["ranked_quotes"]] == [False, True]
    comparable = compare_quotes([insured, bare.model_copy(update={"includes_insurance": True})], {"air"})
    assert comparable["recommendation"]["carrier_id"] == "A"
