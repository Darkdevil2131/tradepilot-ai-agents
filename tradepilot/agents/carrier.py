from ..models import ShipmentRequest, CarrierResearchOutput
# Synthetic fixture data: deliberately fictional carriers and scores.
CARRIERS=[
 {"carrier_id":"DEMO-AIR-01","name":"Demo Air Freight One","mode":"air","regions":["india","global"],"capacity_kg":10000,"reliability_score":0.94,"provenance":"SYNTHETIC"},
 {"carrier_id":"DEMO-SEA-01","name":"Demo Ocean Line One","mode":"sea","regions":["india","global"],"capacity_kg":500000,"reliability_score":0.89,"provenance":"SYNTHETIC"},
 {"carrier_id":"DEMO-ROAD-01","name":"Demo Road Freight One","mode":"road","regions":["india"],"capacity_kg":30000,"reliability_score":0.91,"provenance":"SYNTHETIC"},
]
INDIA_CITIES={"mumbai","delhi","new delhi","chennai","kolkata","bengaluru","pune","hyderabad"}
class CarrierResearchAgent:
    name="carrier_research"
    authorized_tools=("read_synthetic_carrier_fixture",)
    def run(self,shipment:ShipmentRequest, feasible_modes:set[str]|None=None)->CarrierResearchOutput:
        modes=feasible_modes if feasible_modes is not None else set(shipment.allowed_modes)
        domestic_india=(shipment.origin.casefold() in INDIA_CITIES and shipment.destination.casefold() in INDIA_CITIES)
        required_region="india" if domestic_india else "global"
        rows=[{**c,"capacity_available":c["capacity_kg"]>=shipment.weight_kg,"coverage_match":required_region in c["regions"] or "global" in c["regions"]} for c in CARRIERS if c["mode"] in modes]
        eligible=[c for c in rows if c["capacity_available"] and c["coverage_match"]]
        return CarrierResearchOutput(carriers=sorted(eligible,key=lambda c:(-c["reliability_score"],c["carrier_id"])),excluded_capacity=sum(not c["capacity_available"] for c in rows),excluded_coverage=sum(not c["coverage_match"] for c in rows),provenance="SYNTHETIC",notice="Carrier identities, coverage, capacity, and reliability are synthetic demo fixtures. Coverage is checked against the sample origin/destination region labels.")
