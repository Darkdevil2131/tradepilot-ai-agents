import csv
from datetime import timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from ..models import ShipmentRequest, CarrierResearchOutput
from ..tools import CITY_COUNTRIES, effective_departure
from ..trade_corridors import PUBLISHED_SERVICES
# Synthetic fixture data: deliberately fictional carriers and scores.
CARRIERS=[
 {"carrier_id":"DEMO-RAIL-01","name":"Demo Rail Freight","mode":"rail","regions":["india"],"capacity_kg":120000,"provenance":"SYNTHETIC","contact":{"email":"rail@demo.invalid"},"certifications":["ISO-9001"]},
 {"carrier_id":"DEMO-AIR-01","name":"Demo Air Freight One","mode":"air","regions":["india","global"],"capacity_kg":10000,"provenance":"SYNTHETIC","contact":{"email":"air@demo.invalid"},"certifications":["IATA-DEMO","ISO-9001"]},
 {"carrier_id":"DEMO-SEA-01","name":"Demo Ocean Line One","mode":"sea","regions":["india","global"],"capacity_kg":500000,"provenance":"SYNTHETIC","contact":{"email":"sea@demo.invalid"},"certifications":["ISO-9001"]},
 {"carrier_id":"DEMO-ROAD-01","name":"Demo Road Freight One","mode":"road","regions":["india"],"capacity_kg":30000,"provenance":"SYNTHETIC","contact":{"email":"road@demo.invalid"},"certifications":["ISO-9001"]},
]
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
    authorized_tools=("read_synthetic_carrier_fixture", "read_published_service_catalog", "source_leg_plans")
    def run(self,shipment:ShipmentRequest, feasible_modes:set[str]|None=None)->CarrierResearchOutput:
        modes=feasible_modes if feasible_modes is not None else set(shipment.allowed_modes)
        domestic_india = all(CITY_COUNTRIES.get(city.casefold()) == "india"
                             for city in (shipment.origin, shipment.destination))
        required_region="india" if domestic_india else "global"
        rows=[{**c,"capacity_available":c["capacity_kg"]>=shipment.weight_kg,"coverage_match":required_region in c["regions"] or "global" in c["regions"]}
              for c in CARRIERS if c["mode"] in modes and not shipment.requires_submitted_offers]
        for row in rows:
            key = (row["carrier_id"], shipment.origin.casefold(), shipment.destination.casefold())
            total, on_time = load_history().get(key, (0, 0))
            row.update(history_count=total, on_time_count=on_time,
                       reliability_score=on_time / total if total else None,
                       history_reference="fixtures/history.csv#" + "/".join(key))
        supplied_ids = {(service.carrier_id, service.mode) for service in shipment.carrier_services}
        rows = [row for row in rows if (row["carrier_id"], row["mode"]) not in supplied_ids]
        for service in shipment.carrier_services:
            if service.mode not in modes or service.route_leg_ids:
                continue
            lane = tuple(ShipmentRequest.normalize_locations(place).casefold()
                         for place in (service.origin, service.destination))
            rows.append(dict(
                carrier_id=service.carrier_id, name=service.name, mode=service.mode, regions=[],
                capacity_kg=service.capacity_kg, capacity_available=service.capacity_kg >= shipment.weight_kg,
                coverage_match=lane == (shipment.origin.casefold(), shipment.destination.casefold()),
                contact={"email": service.contact_email} if service.contact_email else {},
                history_count=0, on_time_count=0, reliability_score=None, history_reference="",
                coverage_reference=service.source_reference, provenance="USER_PROVIDED",
            ))
        eligible=[c for c in rows if c["capacity_available"] and c["coverage_match"]]
        for carrier in eligible:
            reliability = carrier["reliability_score"]
            headroom = max(0.0, min(1.0, 1.0 - shipment.weight_kg / carrier["capacity_kg"]))
            carrier["service_score"] = round(0.8 * reliability + 0.2 * headroom, 6) if reliability is not None else None
            carrier["history_confidence"] = ("unknown" if not carrier["history_count"] else
                                             "limited_synthetic" if carrier["history_count"] < 20 else "synthetic_sample")
        eligible = sorted(eligible, key=lambda c: (c["provenance"] != "USER_PROVIDED", c["service_score"] is None, -(c["service_score"] or 0),
                                                   -c["capacity_kg"], c["carrier_id"]))
        recommended = None
        if eligible:
            best = eligible[0]
            recommended = {
                "carrier_id": best["carrier_id"],
                "name": best["name"],
                "mode": best["mode"],
                "service_score": best["service_score"],
                "reliability_score": best["reliability_score"],
                "history_confidence": best["history_confidence"],
                "contact": best["contact"],
                "requires_human_approval": True,
                "provenance": best["provenance"],
            }
        return CarrierResearchOutput(
            carriers=eligible, recommended_carrier=recommended,
            excluded_capacity=sum(not c["capacity_available"] for c in rows),
            excluded_coverage=sum(not c["coverage_match"] for c in rows),
            provenance="USER_PROVIDED" if shipment.carrier_services else "SYNTHETIC",
            notice="Demo carrier records are synthetic. Submitted carrier coverage and capacity refer to the supplied evidence and require confirmation.",
        )


def published_service_leads(route_data):
    sources = {source for route in route_data.get("routes", []) if route["feasible"]
               for source in route["source_references"]}
    sources.update(source for option in (route_data.get("impact_comparison") or {}).get("options", [])
                   for portion in option["portions"] for leg in portion["legs"]
                   for source in leg.get("source_references", []))
    return [
        dict(service=PUBLISHED_SERVICES[source], source_reference=source, evidence_type="published_service_catalog",
             provenance="HISTORICAL", availability="unverified", quote=None)
        for source in sorted(sources & PUBLISHED_SERVICES.keys())
    ]


def source_leg_plans(shipment, options):
    """Select one eligible carrier per leg for both portions' combined load."""
    if shipment.requires_submitted_offers:
        return submitted_leg_plans(shipment, options)
    with (Path(__file__).resolve().parents[1] / "fixtures/leg_tariffs.csv").open() as stream:
        tariffs = list(csv.DictReader(stream))
    legs = {leg["id"]: leg for option in options for portion in option["portions"] for leg in portion["legs"]}
    existing = {row["leg"] for row in tariffs}
    domestic = all(CITY_COUNTRIES.get(place.casefold()) == "india"
                   for place in (shipment.origin, shipment.destination))
    for leg_id, leg in legs.items():
        if leg_id in existing and not domestic:
            continue
        # Legacy impact IDs can overlap unrelated domestic main hauls.
        tariffs = [row for row in tariffs if row["leg"] != leg_id]
        tariffs.extend(
            dict(leg=leg_id, carrier_id=carrier["carrier_id"],
                 capacity_kg=min(leg["capacity_kg"], carrier["capacity_kg"]), inr_per_kg=str(leg["rate_inr_per_kg"]))
            for carrier in CARRIERS if carrier["mode"] == leg["mode"] and "india" in carrier["regions"]
        )
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
    return [priced_plan(plan, "SYNTHETIC") for plan in plans]


def priced_plan(plan, provenance):
    charges = [leg["freight_inr"] for leg in plan["legs"]]
    total = round(sum(Decimal(str(charge)) for charge in charges), 2) if all(
        charge is not None for charge in charges) else None
    return dict(**plan, freight_inr=float(total) if total is not None else None,
                total_impact_inr=float(round(total + Decimal(str(plan["delay_loss_inr"])), 2))
                if total is not None else None,
                provenance=provenance, requires_human_approval=True)


def submitted_leg_plans(shipment, options):
    departure = effective_departure(shipment)
    plans = []
    quotes_by_leg = {}
    for index, quote in enumerate(shipment.quotes):
        if quote.leg_total:
            quotes_by_leg.setdefault(quote.route_leg_ids[0], []).append((index, quote))
    for option in options:
        loads = {}
        for portion in option["portions"]:
            base_hours = sum(leg["base_transit_hours"] for leg in portion["legs"])
            elapsed = max(0, portion["arrival_days"] * 24 - base_hours)
            for leg in portion["legs"]:
                load = loads.setdefault(leg["id"], {"leg": leg, "weight": Decimal(0), "latest_start": departure})
                load["weight"] += Decimal(str(portion["weight_kg"]))
                load["latest_start"] = max(load["latest_start"], departure + timedelta(hours=elapsed))
                elapsed += leg["base_transit_hours"]
        awards = []
        for leg_id, load in loads.items():
            leg, weight = load["leg"], load["weight"]
            bids = []
            for index, quote in quotes_by_leg.get(leg_id, []):
                service = next((service for service in shipment.carrier_services
                                if service.carrier_id == quote.carrier_id and service.mode == quote.mode == leg["mode"]
                                and service.route_leg_ids == [leg_id] and service.capacity_kg >= weight
                                and service.origin.strip().casefold() == leg["origin"].casefold()
                                and service.destination.strip().casefold() == leg["destination"].casefold()), None)
                if (service and quote.provenance == "USER_PROVIDED" and quote.currency == "INR"
                        and quote.source_reference and quote.source_reference.strip() and quote.valid_until
                        and quote.valid_until.replace(tzinfo=quote.valid_until.tzinfo or timezone.utc) > load["latest_start"]
                        and quote.quoted_capacity_kg is not None and quote.quoted_capacity_kg >= weight
                        and quote.transit_hours is not None and quote.transit_hours <= leg["base_transit_hours"]):
                    bids.append((index, quote, service))
            comparable = len({quote.includes_insurance for _, quote, _ in bids}) <= 1
            best = min(bids, key=lambda bid: (bid[1].amount, bid[1].carrier_id), default=None) if comparable else None
            quote_index, quote, service = best or (None, None, None)
            awards.append(dict(
                leg=leg_id, weight_kg=float(weight), carrier_id=quote.carrier_id if quote else None,
                freight_inr=quote.amount if quote else None,
                status="submitted_quote" if quote else "quotation_required" if comparable else "insurance_terms_differ",
                quote_reference=quote.source_reference if quote else None,
                quote_index=quote_index,
                eligible_quote_indices=[index for index, _, _ in bids] if comparable else [],
                carrier_service_reference=service.source_reference if service else None,
                includes_insurance=quote.includes_insurance if quote else None,
            ))
        insurance = {award["includes_insurance"] for award in awards}
        scope = "included" if insurance == {True} else "excluded" if insurance == {False} else "partial_or_unknown"
        plans.append(priced_plan(dict(option=option["option"], legs=awards,
                                     delay_loss_inr=option["delay_loss_inr"], insurance_scope=scope), "USER_PROVIDED"))
    return plans
