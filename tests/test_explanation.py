import json
from pathlib import Path


def complete_documents():
    return [
        {"document_type":"commercial_invoice","fields":{"invoice_number":"INV-500","shipper":"Demo Dubai","consignee":"Demo Mumbai","currency":"INR","total":500000}},
        {"document_type":"packing_list","fields":{"package_count":10}},
        {"document_type":"bill_of_lading","fields":{"consignee":"Demo Mumbai"}},
    ]


def test_strategy_matches_api_evidence(client,headers):
    shipment=json.loads(Path("examples/impact_comparison.json").read_text())
    shipment.update(documents=complete_documents(), declared_value=500000, incoterm="CIF", dimensions_cm=[100, 80, 60])
    response=client.post("/api/v1/shipments/analyze",headers=headers,json=shipment).json()
    explanation=response["agents"]["logistics_orchestrator"]["data"]["explanation"]
    assert "78,000.00" in explanation["conclusion"]
    sea=next(row for row in explanation["comparisons"] if row["option"]=="ocean")
    assert sea["extra_impact_inr"]==927000
    threshold=next(row for row in explanation["sensitivity"] if row["alternative"]=="ocean")
    assert threshold["equal_impact_daily_loss_inr"]==2750
    for value,expected in [(2749,"ocean"),(2751,"air/ocean")]:
        shipment["impact_scenario"]["daily_stoppage_inr"]=value
        result=client.post("/api/v1/shipments/analyze",headers=headers,json=shipment).json()
        assert result["agents"]["market_procurement"]["data"]["sourced_plan"]["option"]==expected


def test_closure_and_failure_explanations(client,headers):
    shipment=json.loads(Path("examples/impact_comparison.json").read_text())
    shipment.update(documents=complete_documents(), declared_value=500000, incoterm="CIF", dimensions_cm=[100, 80, 60])
    shipment["impact_scenario"]["closed_leg_ids"]=["pickup"]
    response=client.post("/api/v1/shipments/analyze",headers=headers,json=shipment).json()
    explanation=response["agents"]["logistics_orchestrator"]["data"]["explanation"]
    assert "unavailable" in explanation["conclusion"]
    assert all("closed:pickup" in row["reasons"] for row in explanation["rejected_options"])
    shipment.pop("impact_scenario")
    shipment["destination"]="Munich"
    response=client.post("/api/v1/shipments/analyze",headers=headers,json=shipment).json()
    explanation=response["agents"]["logistics_orchestrator"]["data"]["explanation"]
    assert "incomplete" in explanation["conclusion"]
    assert any("route_optimization failure" in item["action"] for item in explanation["next_actions"])


def test_invoice_packet_controls_final_approval(client, headers):
    shipment = json.loads(Path("examples/impact_comparison.json").read_text())
    docs = complete_documents()
    docs[0]["fields"]["total"] = 200000
    second = dict(docs[0]["fields"], invoice_number="INV-501", total=300000)
    docs.append(dict(document_type="commercial_invoice", fields=second))
    shipment.update(documents=docs, declared_value=500000, declared_value_currency="INR")

    def decision():
        response = client.post("/api/v1/shipments/analyze", headers=headers, json=shipment)
        assert response.status_code == 200
        return response.json()["agents"]["logistics_orchestrator"]["data"]["decision"]

    assert decision()["candidate"]["option"] == "air/ocean"
    for first_amount, second_amount, currency, issue in [
        (500000, 500000, "INR", "Combined commercial invoice total"),
        (200000, 300000, "USD", "multiple currencies"),
    ]:
        docs[0]["fields"]["total"] = first_amount
        second.update(total=second_amount, currency=currency)
        result = decision()
        assert result["status"] == "blocked" and result["candidate"] is None
        assert any(issue in item for item in result["review_items"])

    docs[0]["fields"]["currency"] = "USD"
    assert any("declared value currency" in item for item in decision()["review_items"])
