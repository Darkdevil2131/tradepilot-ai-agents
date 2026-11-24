"""Deterministic tools. Outputs are estimates or synthetic demo records, never live logistics data."""
from __future__ import annotations
from math import radians, sin, cos, asin, sqrt
from datetime import datetime, timezone
from .models import Provenance, Quote, ShipmentRequest

# Coordinates are demo reference points, not routing or carrier integrations.
CITY_COORDS = {
 "mumbai": (19.0760,72.8777), "delhi": (28.6139,77.2090), "new delhi": (28.6139,77.2090),
 "chennai": (13.0827,80.2707), "kolkata": (22.5726,88.3639), "bengaluru": (12.9716,77.5946),
 "singapore": (1.3521,103.8198), "dubai": (25.2048,55.2708), "rotterdam": (51.9244,4.4777),
 "shanghai": (31.2304,121.4737), "london": (51.5072,-0.1276), "new york": (40.7128,-74.0060),
 "los angeles": (34.0522,-118.2437), "pune": (18.5204,73.8567), "hyderabad": (17.3850,78.4867),
}
CITY_COUNTRIES = {
 "mumbai":"india", "delhi":"india", "new delhi":"india", "chennai":"india", "kolkata":"india", "bengaluru":"india", "pune":"india", "hyderabad":"india",
 "singapore":"singapore", "dubai":"uae", "rotterdam":"netherlands", "shanghai":"china", "london":"uk", "new york":"usa", "los angeles":"usa",
}
PORT_CITIES={"mumbai","chennai","kolkata","singapore","dubai","rotterdam","shanghai","london","new york","los angeles"}
MODE_KMH = {"air": 750.0, "sea": 30.0, "road": 55.0}
MODE_RATE_USD_TON_KM = {"air": 0.42, "sea": 0.025, "road": 0.09}

# Fictional connected transport network. The route agent only recommends legs
# present in this fixture, which makes every synthetic recommendation traceable.
SYNTHETIC_LEGS = (
    {"id":"SIM-PUNE-BOM-ROAD","from":"pune","to":"mumbai","mode":"road","hours":8,"cost_per_kg":0.09,"capacity_kg":18000,"risk":0.08},
    {"id":"SIM-DEL-BOM-RAIL","from":"delhi","to":"mumbai","mode":"rail","hours":30,"cost_per_kg":0.06,"capacity_kg":120000,"risk":0.12},
    {"id":"SIM-BOM-SIN-AIR","from":"mumbai","to":"singapore","mode":"air","hours":18,"cost_per_kg":0.42,"capacity_kg":10000,"risk":0.12},
    {"id":"SIM-BOM-SIN-SEA","from":"mumbai","to":"singapore","mode":"sea","hours":264,"cost_per_kg":0.025,"capacity_kg":500000,"risk":0.30},
    {"id":"SIM-DXB-BOM-AIR","from":"dubai","to":"mumbai","mode":"air","hours":48,"cost_per_kg":0.11,"capacity_kg":500,"risk":0.12},
    {"id":"SIM-DXB-BOM-SEA","from":"dubai","to":"mumbai","mode":"sea","hours":384,"cost_per_kg":0.021,"capacity_kg":10000,"risk":0.28},
    {"id":"SIM-BOM-LON-AIR","from":"mumbai","to":"london","mode":"air","hours":30,"cost_per_kg":0.49,"capacity_kg":8500,"risk":0.14},
    {"id":"SIM-BOM-RTM-SEA","from":"mumbai","to":"rotterdam","mode":"sea","hours":504,"cost_per_kg":0.032,"capacity_kg":250000,"risk":0.33},
    {"id":"SIM-RTM-LON-ROAD","from":"rotterdam","to":"london","mode":"road","hours":22,"cost_per_kg":0.16,"capacity_kg":20000,"risk":0.20},
)
DANGEROUS_GOODS_KEYWORDS = (
    "chemical", "flammable", "lithium battery", "lithium batteries",
    "lithium-ion", "lithium ion", "li-ion", "li ion", "battery", "batteries",
    "explosive", "corrosive", "hazardous",
)

def contains_dangerous_goods_keyword(cargo_description: str) -> bool:
    text = " ".join(cargo_description.casefold().replace("–", "-").replace("—", "-").split())
    return any(term in text for term in DANGEROUS_GOODS_KEYWORDS)

def haversine_km(origin: str, destination: str) -> float:
    a, b = CITY_COORDS.get(origin.casefold()), CITY_COORDS.get(destination.casefold())
    if not a or not b:
        raise ValueError("Offline routing supports only the documented sample city list; no geocoding or live map lookup is used")
    lat1, lon1, lat2, lon2 = map(radians, (*a, *b))
    dlat, dlon = lat2-lat1, lon2-lon1
    h = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return 6371.0088 * 2 * asin(sqrt(h))

# Conservative axis-aligned package envelopes for the synthetic services, in cm.
ENVELOPES = {"road": (1200, 240, 240), "rail": (1200, 240, 240),
             "sea": (1200, 235, 235), "air": (300, 200, 150)}

def fits_package(shipment, mode):
    if shipment.dimensions_cm is None:
        return True
    return all(x <= limit for x, limit in zip(sorted(shipment.dimensions_cm), sorted(ENVELOPES[mode])))

def synthetic_paths(shipment: ShipmentRequest) -> list[dict]:
    """Enumerate connected, capacity-constrained paths in the synthetic network."""
    origin, destination = shipment.origin.casefold(), shipment.destination.casefold()
    paths = []
    def walk(node, legs, visited):
        if len(paths) >= 10 or len(legs) >= 4:
            return
        if node == destination:
            paths.append(legs)
            return
        for leg in SYNTHETIC_LEGS:
            if leg["from"] == node and leg["to"] not in visited:
                walk(leg["to"], legs + [leg], visited | {leg["to"]})
    walk(origin, [], {origin})
    options = []
    for legs in paths:
        reasons = []
        for leg in legs:
            if leg["mode"] not in shipment.allowed_modes:
                reasons.append("mode:" + leg["mode"])
            if shipment.weight_kg > leg["capacity_kg"]:
                reasons.append("capacity:" + leg["id"])
            if not fits_package(shipment, leg["mode"]):
                reasons.append("dimensions:" + leg["id"])
        hours = sum(leg["hours"] for leg in legs)
        deadline_ok = shipment.deadline is None or hours <= max(0, (shipment.deadline - datetime.now(timezone.utc)).total_seconds() / 3600)
        options.append({
            "mode": legs[-1]["mode"], "distance_km": 0, "transit_hours_estimate": round(hours, 1),
            "cost_estimate_usd": round(sum(shipment.weight_kg * leg["cost_per_kg"] for leg in legs), 2),
            "mode_feasible": not reasons, "deadline_feasible": deadline_ok, "feasible": not reasons and deadline_ok,
            "feasibility_reason": "Connected synthetic network path." if not reasons else "; ".join(sorted(set(reasons))),
            "risk_score_estimate": round(sum(leg["risk"] for leg in legs) / len(legs), 2),
            "provenance": Provenance.SYNTHETIC.value, "distance_method": "Synthetic connected-leg network.",
            "legs": [{"id":leg["id"],"from":leg["from"],"to":leg["to"],"mode":leg["mode"],"hours":leg["hours"],"capacity_kg":leg["capacity_kg"]} for leg in legs],
            "evidence_ids": [leg["id"] for leg in legs], "capacity_kg": min(leg["capacity_kg"] for leg in legs),
        })
    return sorted(options, key=lambda item:(not item["feasible"], item["cost_estimate_usd"], item["transit_hours_estimate"]))

def calculate_routes(shipment: ShipmentRequest) -> list[dict]:
    connected_options = synthetic_paths(shipment)
    if any(all(leg["mode"] in shipment.allowed_modes for leg in option["legs"]) for option in connected_options):
        return connected_options
    distance = haversine_km(shipment.origin, shipment.destination)
    results = []
    tonnes = shipment.weight_kg / 1000
    origin=shipment.origin.casefold(); destination=shipment.destination.casefold()
    same_country=CITY_COUNTRIES.get(origin) is not None and CITY_COUNTRIES.get(origin)==CITY_COUNTRIES.get(destination)
    for mode in shipment.allowed_modes:
        if mode == "road":
            mode_feasible=same_country
            feasibility_reason="Sample cities share a country; this is only a demo eligibility check, not a road-network route." if mode_feasible else "Offline road mode is limited to sample city pairs in the same country; no cross-border road routing is modeled."
        elif mode == "rail":
            mode_feasible = {origin, destination} == {"mumbai", "delhi"}
            feasibility_reason = "Synthetic rail corridor is limited to Mumbai–Delhi."
        elif mode == "sea":
            mode_feasible=origin in PORT_CITIES and destination in PORT_CITIES
            feasibility_reason="Both sample cities are treated as port nodes; a real maritime lane is not calculated." if mode_feasible else "Sea mode requires both sample cities to be in the built-in port list; inland transfers are not modeled."
        else:
            mode_feasible=True
            feasibility_reason="Both cities are in the offline sample table; airport availability, schedules, and actual service are not verified."
        # Detour factors only approximate network routing; distances are not road/sea network paths.
        if not fits_package(shipment, mode):
            mode_feasible = False
            feasibility_reason = "Package exceeds the synthetic service dimensions."
        factor = {"rail": 1.3, "air": 1.08, "sea": 1.65, "road": 1.25}[mode]
        network_estimate = distance * factor
        transit_hours = network_estimate / (48 if mode == "rail" else MODE_KMH[mode]) + {"rail": 14, "air": 12, "sea": 48, "road": 8}[mode]
        cost = max(25.0, tonnes * network_estimate * (0.06 if mode == "rail" else MODE_RATE_USD_TON_KM[mode]))
        deadline_ok = shipment.deadline is None or transit_hours <= max(0, (shipment.deadline - datetime.now(timezone.utc)).total_seconds()/3600)
        feasible=mode_feasible and deadline_ok
        results.append({"mode":mode,"distance_km":round(network_estimate,1),"transit_hours_estimate":round(transit_hours,1),"cost_estimate_usd":round(cost,2),"mode_feasible":mode_feasible,"deadline_feasible":deadline_ok,"feasible":feasible,"feasibility_reason":feasibility_reason,"risk_score_estimate": {"rail":0.18,"air":0.12,"sea":0.30,"road":0.22}[mode],"provenance":Provenance.ESTIMATED.value,"distance_method":"straight-line distance × fixed demo factor; not a navigable route"})
    return sorted(results,key=lambda r:(not r["feasible"],r["cost_estimate_usd"],r["transit_hours_estimate"]))

def validate_documents(shipment: ShipmentRequest) -> dict:
    by_type = {doc.document_type: doc for doc in shipment.documents}
    required = ["commercial_invoice", "packing_list", "bill_of_lading"]
    missing = [kind for kind in required if kind not in by_type]
    issues = []
    from decimal import Decimal, InvalidOperation
    import json

    seen, invoices, consignees = set(), set(), set()
    for doc in shipment.documents:
        fields = doc.fields
        fingerprint = (doc.document_type, json.dumps(fields, sort_keys=True))
        if fingerprint in seen:
            issues.append(f"Duplicate {doc.document_type}")
        seen.add(fingerprint)
        required_fields = {"commercial_invoice": ("invoice_number","shipper","consignee","currency","total"),
                           "packing_list": ("package_count",), "bill_of_lading": ("consignee",)}
        for key in required_fields.get(doc.document_type, ()):
            if not str(fields.get(key) if fields.get(key) is not None else "").strip():
                issues.append(f"{doc.document_type} is missing {key}")
        if fields.get("consignee"):
            consignees.add(" ".join(str(fields["consignee"]).casefold().split()))
        if doc.document_type == "commercial_invoice":
            identity = tuple(str(fields.get(k,"")).strip().casefold() for k in ("shipper","invoice_number"))
            if identity in invoices:
                issues.append("Repeated shipper and invoice number")
            invoices.add(identity)
        numeric = {"commercial_invoice":"total","packing_list":"package_count"}.get(doc.document_type)
        if numeric and numeric in fields:
            try:
                value = Decimal(str(fields[numeric]))
                if not value.is_finite() or value <= 0 or (numeric == "package_count" and value != value.to_integral_value()):
                    raise ValueError
                if numeric == "total" and shipment.declared_value is not None and abs(float(value)-shipment.declared_value)>max(1,shipment.declared_value*.01):
                    issues.append("commercial_invoice total differs from declared value")
            except (ValueError, InvalidOperation):
                issues.append(f"Invalid {doc.document_type} {numeric}")
    if len(consignees) > 1:
        issues.append("Consignee differs across documents")
    dangerous = contains_dangerous_goods_keyword(shipment.cargo_description)
    if dangerous and "dangerous_goods_declaration" not in by_type:
        missing.append("dangerous_goods_declaration")
    if shipment.incoterm and shipment.incoterm.upper() not in {"EXW","FCA","CPT","CIP","DAP","DPU","DDP","FAS","FOB","CFR","CIF"}:
        issues.append("incoterm is not in the built-in Incoterms® 2020 code list; verify manually")
    return {"valid":not missing and not issues,"documents_checked":len(shipment.documents),"missing_document_types":missing,"issues":issues,"provenance":Provenance.USER_PROVIDED.value}

def compare_quotes(quotes: list[Quote], feasible_modes: set[str]) -> dict:
    now = datetime.now(timezone.utc)
    eligible = [q for q in quotes if q.mode in feasible_modes and (q.valid_until is None or q.valid_until.replace(tzinfo=q.valid_until.tzinfo or timezone.utc) > now)]
    currencies={q.currency for q in eligible}
    same_currency=len(currencies)<=1
    ranked=sorted(eligible,key=(lambda q:(q.amount,q.carrier_id)) if same_currency else (lambda q:(q.currency,q.amount,q.carrier_id)))
    recommendation=None
    warning=None
    if ranked and same_currency:
        recommendation={"carrier_id":ranked[0].carrier_id,"mode":ranked[0].mode,"amount":ranked[0].amount,"currency":ranked[0].currency,"requires_human_approval":True}
    elif ranked:
        warning="Quotes use multiple currencies; no FX conversion is configured, so no single cheapest recommendation is made. Compare amounts within each currency and obtain human review."
    return {"quotes_received":len(quotes),"eligible_quotes":len(eligible),"ranked_quotes":[{"carrier_id":q.carrier_id,"mode":q.mode,"amount":q.amount,"currency":q.currency,"valid_until":q.valid_until.isoformat() if q.valid_until else None,"provenance":q.provenance.value} for q in ranked],"recommendation":recommendation,"recommendation_warning":warning,"note":"Quote ranking compares user-provided quotes only; no FX conversion, market-price lookup, or booking is performed."}


def impact_options(shipment: ShipmentRequest) -> dict:
    """Evaluate explicitly connected synthetic paths for the Dubai–Mumbai scenario."""
    import itertools
    from decimal import Decimal

    scenario = shipment.impact_scenario
    weight = Decimal(str(shipment.weight_kg))
    critical = Decimal(str(scenario.critical_weight_kg))
    # id, origin, destination, mode, duration in days, INR per kg, capacity kg
    edges = [
        ("pickup", "Dubai warehouse", "Dubai transfer hub", "road", .5, 5, 10000),
        ("air", "Dubai transfer hub", "Mumbai transfer hub", "air", 2, 410, 500),
        ("ocean", "Dubai transfer hub", "Mumbai transfer hub", "sea", 16, 80, 10000),
        ("delivery", "Mumbai transfer hub", "Mumbai warehouse", "road", .5, 5, 10000),
    ]
    paths = [(edges[0], main, edges[3]) for main in edges[1:3]]
    # Each ordered pair assigns the critical and remaining portions respectively.
    options, rejected = [], []
    for first, second in itertools.product(paths, repeat=2):
        if critical == weight and first != second:
            continue
        loads = [(first, weight)] if first == second else [(first, critical), (second, weight-critical)]
        quantities = {}
        portions = []
        reasons = set()
        freight = Decimal(0)
        for path, mass in loads:
            legs = []
            for leg, origin, destination, mode, days, rate, capacity in path:
                quantities[leg] = quantities.get(leg, Decimal(0)) + mass
                if quantities[leg] > capacity:
                    reasons.add(f"capacity:{leg}")
                if not fits_package(shipment, mode):
                    reasons.add(f"dimensions:{leg}")
                if leg in scenario.closed_leg_ids:
                    reasons.add(f"closed:{leg}")
                if mode not in shipment.allowed_modes:
                    reasons.add(f"mode:{mode}")
                charge = mass * rate * Decimal(str(scenario.freight_multiplier))
                freight += charge
                legs.append(dict(id=leg,origin=origin,destination=destination,mode=mode,freight_inr=float(charge)))
            eta = sum(edge[4] for edge in path)
            if shipment.deadline and eta * 86400 > (shipment.deadline-datetime.now(timezone.utc)).total_seconds():
                reasons.add("hard_deadline")
            portions.append(dict(weight_kg=float(mass), arrival_days=eta, legs=legs))
        name = "/".join(path[1][0] for path, _ in loads)
        if contains_dangerous_goods_keyword(shipment.cargo_description):
            reasons.add("cargo_acceptance_requires_review")
        if reasons:
            rejected.append(dict(option=name,reasons=sorted(reasons)))
            continue
        delay = max(0, portions[0]["arrival_days"]-scenario.target_days)*scenario.daily_stoppage_inr
        options.append(dict(option=name,portions=portions,freight_inr=round(float(freight),2),
                            delay_loss_inr=round(delay,2),total_impact_inr=round(float(freight)+delay,2)))
    options.sort(key=lambda o:(o["total_impact_inr"],len(o["portions"]),o["option"]))
    return dict(provenance="SYNTHETIC", options=options,rejected=rejected,
                preferred=options[0]["option"] if options else None,
                assumptions=["Critical cargo arrives with the first portion and ends the modeled stoppage.",
                             "Rates, hubs, capacities and durations are synthetic. Taxes and insurance excluded."])
