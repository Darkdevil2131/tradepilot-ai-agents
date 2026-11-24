from ..models import ShipmentRequest, CarrierResearchOutput
# Synthetic fixture data: deliberately fictional carriers and scores.
CARRIERS=[
 {"carrier_id":"DEMO-AIR-01","name":"Demo Air Freight One","mode":"air","regions":["india","global"],"capacity_kg":10000,"reliability_score":0.94,"provenance":"SYNTHETIC"},
 {"carrier_id":"DEMO-SEA-01","name":"Demo Ocean Line One","mode":"sea","regions":["india","global"],"capacity_kg":500000,"reliability_score":0.89,"provenance":"SYNTHETIC"},
 {"carrier_id":"DEMO-ROAD-01","name":"Demo Road Freight One","mode":"road","regions":["india"],"capacity_kg":30000,"reliability_score":0.91,"provenance":"SYNTHETIC"},
]
class CarrierResearchAgent:
    name="carrier_research"
    authorized_tools=("read_synthetic_carrier_fixture",)
    def run(self,shipment:ShipmentRequest, feasible_modes:set[str]|None=None)->CarrierResearchOutput:
        modes=feasible_modes if feasible_modes is not None else set(shipment.allowed_modes)
        rows=[{**c,"capacity_available":c["capacity_kg"]>=shipment.weight_kg,"coverage_match":True} for c in CARRIERS if c["mode"] in modes]
        eligible=[c for c in rows if c["capacity_available"]]
        return CarrierResearchOutput(carriers=sorted(eligible,key=lambda c:(-c["reliability_score"],c["carrier_id"])),excluded_capacity=len(rows)-len(eligible),provenance="SYNTHETIC",notice="Carrier identities, coverage, capacity, and reliability are synthetic demo fixtures.")
