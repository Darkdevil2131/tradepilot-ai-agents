import csv
from functools import lru_cache
from pathlib import Path
from ..models import ShipmentRequest, CarrierResearchOutput
# Synthetic fixture data: deliberately fictional carriers and scores.
CARRIERS=[
 {"carrier_id":"DEMO-RAIL-01","name":"Demo Rail Freight","mode":"rail","regions":["india"],"capacity_kg":120000,"provenance":"SYNTHETIC","contact":{"email":"rail@demo.invalid"},"certifications":["ISO-9001"]},
 {"carrier_id":"DEMO-AIR-01","name":"Demo Air Freight One","mode":"air","regions":["india","global"],"capacity_kg":10000,"provenance":"SYNTHETIC","contact":{"email":"air@demo.invalid"},"certifications":["IATA-DEMO","ISO-9001"]},
 {"carrier_id":"DEMO-SEA-01","name":"Demo Ocean Line One","mode":"sea","regions":["india","global"],"capacity_kg":500000,"provenance":"SYNTHETIC","contact":{"email":"sea@demo.invalid"},"certifications":["ISO-9001"]},
 {"carrier_id":"DEMO-ROAD-01","name":"Demo Road Freight One","mode":"road","regions":["india"],"capacity_kg":30000,"provenance":"SYNTHETIC","contact":{"email":"road@demo.invalid"},"certifications":["ISO-9001"]},
]
INDIA_CITIES={"mumbai","delhi","new delhi","chennai","kolkata","bengaluru","pune","hyderabad"}
@lru_cache(maxsize=1)
def load_history():
    counts = {}
    with (Path(__file__).resolve().parents[1] / "fixtures/history.csv").open() as stream:
        for row in csv.DictReader(stream):
            key = (row["carrier"], row["origin"], row["destination"])
            total, on_time = counts.get(key, (0, 0))
            counts[key] = (total + 1, on_time + (int(row["actual_hours"]) <= int(row["promised_hours"])))
    return counts


class CarrierResearchAgent:
    name="carrier_research"
    authorized_tools=("read_synthetic_carrier_fixture",)
    def run(self,shipment:ShipmentRequest, feasible_modes:set[str]|None=None)->CarrierResearchOutput:
        modes=feasible_modes if feasible_modes is not None else set(shipment.allowed_modes)
        domestic_india=(shipment.origin.casefold() in INDIA_CITIES and shipment.destination.casefold() in INDIA_CITIES)
        required_region="india" if domestic_india else "global"
        rows=[{**c,"capacity_available":c["capacity_kg"]>=shipment.weight_kg,"coverage_match":required_region in c["regions"] or "global" in c["regions"]} for c in CARRIERS if c["mode"] in modes]
        for row in rows:
            key = (row["carrier_id"], shipment.origin.casefold(), shipment.destination.casefold())
            total, on_time = load_history().get(key, (0, 0))
            row.update(history_count=total, on_time_count=on_time,
                       reliability_score=on_time / total if total else None,
                       history_reference="fixtures/history.csv#" + "/".join(key))
        eligible=[c for c in rows if c["capacity_available"] and c["coverage_match"]]
        for carrier in eligible:
            reliability = carrier["reliability_score"] if carrier["reliability_score"] is not None else 0.5
            headroom = max(0.0, min(1.0, 1.0 - shipment.weight_kg / carrier["capacity_kg"]))
            carrier["service_score"] = round(0.8 * reliability + 0.2 * headroom, 6)
        eligible = sorted(eligible, key=lambda c:(-c["service_score"], c["carrier_id"]))
        recommended = None
        if eligible:
            best = eligible[0]
            recommended = {
                "carrier_id": best["carrier_id"],
                "name": best["name"],
                "mode": best["mode"],
                "service_score": best["service_score"],
                "reliability_score": best["reliability_score"],
                "contact": best["contact"],
                "requires_human_approval": True,
                "provenance": "SYNTHETIC",
            }
        return CarrierResearchOutput(carriers=eligible,recommended_carrier=recommended,excluded_capacity=sum(not c["capacity_available"] for c in rows),excluded_coverage=sum(not c["coverage_match"] for c in rows),provenance="SYNTHETIC",notice="Carrier identities, contacts, certifications, coverage, capacity, and reliability are synthetic demo fixtures. They are decision-test data, not real booking contacts.")


def source_leg_plans(shipment, options):
    """Select one eligible synthetic tariff per leg for the combined load."""
    from decimal import Decimal
    with (Path(__file__).resolve().parents[1] / "fixtures/leg_tariffs.csv").open() as stream:
        tariffs = list(csv.DictReader(stream))
    plans = []
    for option in options:
        loads = {}
        for portion in option["portions"]:
            for leg in portion["legs"]:
                loads[leg["id"]] = loads.get(leg["id"], Decimal(0)) + Decimal(str(portion["weight_kg"]))
        awards = []
        for leg, weight in loads.items():
            bids = [row for row in tariffs if row["leg"] == leg and Decimal(row["capacity_kg"]) >= weight]
            best = min(bids, key=lambda row: Decimal(row["inr_per_kg"]), default=None)
            charge = None
            if best:
                charge = round(weight * Decimal(best["inr_per_kg"]) * Decimal(str(shipment.impact_scenario.freight_multiplier)), 2)
            awards.append(dict(leg=leg, weight_kg=float(weight), carrier_id=best["carrier_id"] if best else None,
                               freight_inr=float(charge) if charge is not None else None,
                               status="synthetic_tariff" if best else "quotation_required"))
        plans.append(dict(option=option["option"], legs=awards, delay_loss_inr=option["delay_loss_inr"]))
    return plans
