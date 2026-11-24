import json
from pathlib import Path
from tradepilot.models import ShipmentRequest
from tradepilot.tools import calculate_routes


def test_packet(client, headers):
    invoice = dict(document_type="commercial_invoice",fields=dict(shipper="A",consignee="B",invoice_number="1",currency="USD",total="invalid"))
    docs = [invoice, invoice,dict(document_type="packing_list",fields=dict(package_count=-3)),
            dict(document_type="bill_of_lading",fields=dict(consignee="C"))]
    response = client.post("/api/v1/documents/validate", headers=headers,json={"shipment":dict(origin="Mumbai",destination="Delhi",weight_kg=1,cargo_description="parts",documents=docs)})
    result = response.json()
    assert not result["valid"]
    for text in ("Duplicate", "total", "package_count", "Consignee"):
        assert any(text in item for item in result["issues"])
    analysis = client.post("/api/v1/shipments/analyze", headers=headers, json={"origin":"Mumbai","destination":"Singapore","weight_kg":1,"cargo_description":"parts","quotes":[{"carrier_id":"DEMO-AIR-01","mode":"air","amount":100}],"documents":docs}).json()
    decision = analysis["agents"]["logistics_orchestrator"]["data"]["decision"]
    assert decision["status"] == "blocked"
    assert decision["candidate"] is None
    assert next(gate for gate in decision["gates"] if gate["gate"] == "documents")["status"] == "blocked"


def test_sourced_revision(client,headers):
    s=json.loads(Path("examples/impact_comparison.json").read_text())
    s["shipment_id"]="REV-001"
    for closed, expected in [([], "air/ocean"), (["ocean"], "air")]:
        s["impact_scenario"]["closed_leg_ids"]=closed
        response=client.post("/api/v1/shipments/analyze",headers=headers,json=s)
        plan=response.json()["agents"]["market_procurement"]["data"]["sourced_plan"]
        assert plan["option"]==expected and all(leg["carrier_id"] for leg in plan["legs"])
        assert plan["freight_inr"] == (78000 if not closed else 210000)
    revisions=client.get("/api/v1/shipments/REV-001/history",headers=headers).json()["revisions"]
    assert [r["revision"] for r in revisions]==[1,2]
    assert revisions[-1]["changed_inputs"]==["impact_scenario"]


def test_rail_dimensions():
    s=ShipmentRequest(origin="Mumbai",destination="Delhi",weight_kg=500,cargo_description="parts",allowed_modes=["rail"])
    assert calculate_routes(s)[0]["feasible"]
    assert not calculate_routes(s.model_copy(update={"dimensions_cm":(5000,200,150)}))[0]["feasible"]
