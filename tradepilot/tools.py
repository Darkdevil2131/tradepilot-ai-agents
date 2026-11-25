"""Deterministic tools. Outputs are estimates or synthetic demo records, never live logistics data."""
from __future__ import annotations
from math import radians, sin, cos, asin, sqrt
from datetime import datetime, timezone
from .models import Provenance, Quote, ShipmentRequest
from decimal import Decimal
from collections import defaultdict
from heapq import heappop, heappush
from .india_network import EXTRA_CITY_COORDS, MORTH_CORRIDORS, MORTH_HIGHWAY_MAP, ROAD_CHAINS

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
INDIA_COORDS = {
    "kochi": (9.9312, 76.2673), "coimbatore": (11.0168, 76.9558),
    "ahmedabad": (23.0225, 72.5714), "surat": (21.1702, 72.8311),
    "jaipur": (26.9124, 75.7873), "lucknow": (26.8467, 80.9462),
    "nagpur": (21.1458, 79.0882), "bhopal": (23.2599, 77.4126),
    "indore": (22.7196, 75.8577), "patna": (25.5941, 85.1376),
    "bhubaneswar": (20.2961, 85.8245), "guwahati": (26.1445, 91.7362),
    "visakhapatnam": (17.6868, 83.2185), "mangaluru": (12.9141, 74.8560),
    "thiruvananthapuram": (8.5241, 76.9366), "madurai": (9.9252, 78.1198),
}
CITY_COORDS.update(INDIA_COORDS)
CITY_COUNTRIES.update(dict.fromkeys(INDIA_COORDS, "india"))
CITY_COORDS.update(EXTRA_CITY_COORDS)
CITY_COUNTRIES.update(dict.fromkeys(EXTRA_CITY_COORDS, "india"))
PORT_CITIES={"mumbai","chennai","kolkata","singapore","dubai","rotterdam","shanghai","london","new york","los angeles"}
MODE_KMH = {"air": 750.0, "sea": 30.0, "road": 55.0}
MODE_RATE_USD_TON_KM = {"air": 0.42, "sea": 0.025, "road": 0.09}

# Bidirectional synthetic corridors: main-haul mode -> (days, INR/kg, kg capacity).
# Access and delivery legs are modeled separately. Rates are demo inputs.
INDIA_CORRIDORS = {
    frozenset(("kochi", "chennai")): {"road": (1.5, 8, 12000), "rail": (2, 6, 20000),
                                      "air": (.5, 45, 1500), "ocean": (5, 5, 20000)},
    frozenset(("kochi", "bengaluru")): {"road": (1.2, 8, 12000), "rail": (2, 6, 20000),
                                        "air": (.5, 42, 1500)},
    frozenset(("chennai", "bengaluru")): {"road": (1, 7, 12000), "rail": (1.5, 5, 20000),
                                          "air": (.5, 40, 1500)},
    frozenset(("mumbai", "pune")): {"road": (.5, 5, 12000), "rail": (1, 4, 20000)},
    frozenset(("mumbai", "delhi")): {"road": (3, 14, 12000), "rail": (3.5, 10, 20000),
                                     "air": (.75, 65, 1500)},
    frozenset(("mumbai", "ahmedabad")): {"road": (1.5, 8, 12000), "rail": (2, 6, 20000),
                                         "air": (.5, 42, 1500)},
    frozenset(("hyderabad", "bengaluru")): {"road": (1.5, 8, 12000), "rail": (2, 6, 20000),
                                            "air": (.5, 43, 1500)},
    frozenset(("kolkata", "bhubaneswar")): {"road": (1.5, 8, 12000), "rail": (2, 6, 20000),
                                            "air": (.5, 45, 1500)},
    frozenset(("chennai", "visakhapatnam")): {"road": (2, 10, 12000), "rail": (2.5, 7, 20000),
                                              "air": (.5, 48, 1500), "ocean": (5, 5, 20000)},
    frozenset(("delhi", "jaipur")): {"road": (1, 6, 12000), "rail": (1.5, 5, 20000)},
}
DEMO_INR_PER_USD = 85


def india_impact_paths(shipment):
    origin, destination = (place.casefold() for place in (shipment.origin, shipment.destination))
    origin, destination = ("delhi" if place == "new delhi" else place for place in (origin, destination))
    modes = INDIA_CORRIDORS.get(frozenset((origin, destination)), {})
    origin_hub, destination_hub = origin + " hub", destination + " hub"
    pickup = ("pickup", origin, origin_hub, "road", .25, 2, 20000)
    delivery = ("delivery", destination_hub, destination, "road", .25, 2, 20000)
    return [
        (pickup, (leg, origin_hub, destination_hub, "sea" if leg == "ocean" else leg,
                  days, rate, capacity), delivery)
        for leg, (days, rate, capacity) in modes.items()
    ]


def road_leg_km(start, end):
    return round(haversine_km(start, end) * 1.22, 1)


def india_road_paths(shipment, closed_ids=()):
    """Find up to four loop-free road paths through explicit graph links."""
    origin, destination = (city.casefold() for city in (shipment.origin, shipment.destination))
    graph = defaultdict(list)
    seen_edges = set()
    for _, cities in ROAD_CHAINS:
        for start, end in zip(cities, cities[1:]):
            edge = frozenset((start, end))
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            for a, b in ((start, end), (end, start)):
                leg_id = f"IN-ROAD-{a}-{b}".upper()
                km = road_leg_km(a, b)
                graph[a].append((leg_id, a, b, "road", round(km / 48 / 24, 6), round(km * .025, 3), 12000))
    if origin not in graph or destination not in graph:
        return []
    queue = [(0.0, (origin,), ())]
    paths = []
    closed = {item.casefold() for item in closed_ids}
    expansions = 0
    while queue and len(paths) < 4 and expansions < 4000:
        score, cities, legs = heappop(queue)
        expansions += 1
        if cities[-1] == destination:
            paths.append(legs)
            continue
        for leg in graph[cities[-1]]:
            leg_id, start, end, mode, days, *_ = leg
            if end in cities or leg_id.casefold() in closed:
                continue
            signals = [signal for signal in shipment.disruptions
                       if disruption_matches_leg(signal, mode, start, end, leg_id)]
            if any(signal.severity >= .95 for signal in signals):
                continue
            # Order candidates by modeled transit, including disruption delay.
            heappush(queue, (score + days + sum(signal.delay_hours for signal in signals) / 24,
                             cities + (end,), legs + (leg,)))
    return paths


def road_network_options(shipment):
    if "road" not in shipment.allowed_modes:
        return []
    paths = india_road_paths(shipment)
    # Keep baseline routes as diagnostics when the supplied disruption isolates a city.
    if not paths and shipment.disruptions:
        paths = india_road_paths(shipment.model_copy(update={"disruptions": []}))
    options = []
    remaining_hours = ((shipment.deadline - datetime.now(timezone.utc)).total_seconds() / 3600
                       if shipment.deadline else None)
    for path in paths:
        capacity = min(leg[6] for leg in path)
        mode_ok = shipment.weight_kg <= capacity and fits_package(shipment, "road")
        cost_inr = round(sum(leg[5] for leg in path) * shipment.weight_kg, 2)
        legs = [dict(id=leg_id, **{"from": start, "to": end}, mode=mode, hours=days * 24,
                     distance_km=road_leg_km(start, end), capacity_kg=limit)
                for leg_id, start, end, mode, days, _, limit in path]
        hours = round(sum(leg["hours"] for leg in legs), 2)
        deadline_ok = remaining_hours is None or hours <= remaining_hours
        options.append(dict(
            mode="road", distance_km=round(sum(leg["distance_km"] for leg in legs), 1),
            distance_method="Selected highway corridor and feeder waypoints; distances are great-circle estimates with a modeled detour.",
            transit_hours_estimate=hours,
            cost_estimate_usd=round(cost_inr / DEMO_INR_PER_USD, 2), cost_estimate_inr=cost_inr,
            mode_feasible=mode_ok, deadline_feasible=deadline_ok, feasible=mode_ok and deadline_ok,
            feasibility_reason=("Connected offline planning route. Transit, road access and tariffs require verification."
                                if mode_ok else "Cargo exceeds modeled road capacity or dimensions."),
            risk_score_estimate=.22, provenance="ESTIMATED", legs=legs,
            evidence_ids=[leg["id"] for leg in legs], capacity_kg=capacity,
            source_references=[MORTH_CORRIDORS, MORTH_HIGHWAY_MAP],
        ))
    return options


def domestic_route_options(shipment, paths):
    options = []
    now = datetime.now(timezone.utc)
    for path in paths:
        mode = path[1][3]
        if mode not in shipment.allowed_modes:
            continue
        terminal_only = "road" not in shipment.allowed_modes
        if terminal_only:
            _, _, _, leg_mode, days, rate, limit = path[1]
            path = ((path[1][0], path[0][1], path[-1][2], leg_mode, days, rate, limit),)
        hours = sum(edge[4] for edge in path) * 24
        capacity = min(edge[6] for edge in path)
        mode_ok = (shipment.weight_kg <= capacity and
                   all(edge[3] in shipment.allowed_modes and fits_package(shipment, edge[3]) for edge in path))
        deadline_ok = shipment.deadline is None or hours <= (shipment.deadline - now).total_seconds() / 3600
        cost_inr = round(shipment.weight_kg * sum(edge[5] for edge in path), 2)
        legs = [
            dict(id=f"SIM-IN-{shipment.origin.casefold()}-{shipment.destination.casefold()}-{leg}",
                 **{"from": start, "to": end}, mode=leg_mode, hours=days * 24, capacity_kg=limit)
            for leg, start, end, leg_mode, days, _, limit in path
        ]
        options.append(dict(
            mode=mode, distance_km=0, distance_method="Synthetic connected corridor; distance unavailable. USD uses fixed demo FX of INR 85 per USD.",
            transit_hours_estimate=hours, cost_estimate_usd=round(cost_inr / DEMO_INR_PER_USD, 2),
            cost_estimate_inr=cost_inr, mode_feasible=mode_ok, deadline_feasible=deadline_ok,
            feasible=mode_ok and deadline_ok,
            feasibility_reason=("Synthetic terminal-to-terminal corridor. Pickup and delivery excluded." if terminal_only else
                                "Connected synthetic corridor.") if mode_ok else "Cargo exceeds capacity or dimensions.",
            risk_score_estimate={"road": .22, "rail": .18, "air": .12, "sea": .30}[mode],
            provenance="SYNTHETIC", legs=legs, evidence_ids=[leg["id"] for leg in legs], capacity_kg=capacity,
        ))
    return options


def disruption_matches_leg(signal, mode, start, end, leg_id=None):
    if not signal.active or (signal.affected_modes and mode not in signal.affected_modes):
        return False
    if signal.affected_leg_ids and (leg_id is None or
            leg_id.casefold() not in {item.casefold().strip() for item in signal.affected_leg_ids}):
        return False
    locations = {location.casefold().strip() for location in signal.affected_locations}
    endpoints = {place.casefold().strip() for place in (start, end)}
    city_names = {place.split(" transfer hub")[0].removesuffix(" hub").removesuffix(" warehouse")
                  for place in endpoints}
    return not locations or bool(locations & (endpoints | city_names))

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
    domestic_paths = india_impact_paths(shipment)
    road_options = road_network_options(shipment)
    if domestic_paths:
        return road_options + domestic_route_options(
            shipment, [path for path in domestic_paths if not road_options or path[1][3] != "road"])
    connected_options = synthetic_paths(shipment)
    if any(all(leg["mode"] in shipment.allowed_modes for leg in option["legs"]) for option in connected_options):
        return road_options + connected_options
    distance = haversine_km(shipment.origin, shipment.destination)
    results = list(road_options)
    tonnes = shipment.weight_kg / 1000
    origin=shipment.origin.casefold(); destination=shipment.destination.casefold()
    same_country=CITY_COUNTRIES.get(origin) is not None and CITY_COUNTRIES.get(origin)==CITY_COUNTRIES.get(destination)
    for mode in shipment.allowed_modes:
        if mode == "road":
            if road_options:
                continue
            mode_feasible=same_country and CITY_COUNTRIES[origin] != "india"
            if same_country and CITY_COUNTRIES[origin] == "india":
                feasibility_reason = "No connected path is available in the offline India road graph."
            else:
                feasibility_reason="Sample cities share a country; this is only a demo eligibility check, not a road-network route." if mode_feasible else "Offline estimates provide no cross-border road routing."
        elif mode == "rail":
            mode_feasible = {origin, destination} == {"mumbai", "delhi"}
            feasibility_reason = "Synthetic rail corridor is limited to Mumbai–Delhi."
        elif mode == "sea":
            domestic_india = same_country and CITY_COUNTRIES[origin] == "india"
            mode_feasible=not domestic_india and origin in PORT_CITIES and destination in PORT_CITIES
            feasibility_reason="Both sample cities are treated as port nodes; a real maritime lane is not calculated." if mode_feasible else "Sea mode requires both sample cities to be in the built-in port list; inland transfers are not modeled."
            if domestic_india:
                feasibility_reason = "No explicit synthetic coastal shipping corridor is configured for this pair."
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
    surface_only = set(shipment.allowed_modes) <= {"road", "rail"}
    domestic = all(CITY_COUNTRIES.get(place.casefold()) == "india" for place in (shipment.origin, shipment.destination))
    transport_doc = "consignment_note" if surface_only and domestic else "bill_of_lading"
    required = ["commercial_invoice", "packing_list", transport_doc]
    missing = [kind for kind in required if kind not in by_type]
    issues = []
    from decimal import Decimal, InvalidOperation
    import json

    seen, invoices, consignees = set(), set(), set()
    invoice_amounts = []
    invoice_currencies = set()
    for doc in shipment.documents:
        fields = doc.fields
        fingerprint = (doc.document_type, json.dumps(fields, sort_keys=True))
        if fingerprint in seen:
            issues.append(f"Duplicate {doc.document_type}")
        seen.add(fingerprint)
        required_fields = {"commercial_invoice": ("invoice_number","shipper","consignee","currency","total"),
                           "packing_list": ("package_count",), "bill_of_lading": ("consignee",),
                           "consignment_note": ("consignee", "consignment_number")}
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
            currency = str(fields.get("currency", "")).strip().upper()
            if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
                issues.append("Commercial invoice currency requires a three-letter code")
            invoice_currencies.add(currency)
        numeric = {"commercial_invoice":"total","packing_list":"package_count"}.get(doc.document_type)
        if numeric and numeric in fields:
            try:
                value = Decimal(str(fields[numeric]))
                if not value.is_finite() or value <= 0 or (numeric == "package_count" and value != value.to_integral_value()):
                    raise ValueError
                if numeric == "total":
                    invoice_amounts.append(value)
            except (ValueError, InvalidOperation):
                issues.append(f"Invalid {doc.document_type} {numeric}")
    if shipment.declared_value is not None and invoice_amounts:
        declared = Decimal(str(shipment.declared_value))
        tolerance = max(Decimal(1), declared * Decimal("0.01"))
        if len(invoice_currencies) > 1:
            issues.append("Commercial invoices use multiple currencies. Supply a common valuation before reconciliation.")
        elif shipment.declared_value_currency and shipment.declared_value_currency not in invoice_currencies:
            issues.append("Invoice currency differs from declared value currency. Supply a common valuation.")
        elif any(value > declared + tolerance for value in invoice_amounts) or abs(sum(invoice_amounts) - declared) > tolerance:
            issues.append("Combined commercial invoice total differs from declared value")
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
    same_coverage = len({quote.includes_insurance for quote in eligible}) <= 1
    if ranked and same_currency and same_coverage:
        recommendation={"carrier_id":ranked[0].carrier_id,"mode":ranked[0].mode,"amount":ranked[0].amount,"currency":ranked[0].currency,"requires_human_approval":True}
    elif ranked and not same_currency:
        warning="Quotes use multiple currencies; no FX conversion is configured, so no single cheapest recommendation is made. Compare amounts within each currency and obtain human review."
    elif ranked:
        warning = "Insurance coverage differs across quotes. Obtain comparable insured or uninsured prices before selecting a carrier."
    return {"quotes_received":len(quotes),"eligible_quotes":len(eligible),"ranked_quotes":[{"carrier_id":q.carrier_id,"mode":q.mode,"amount":q.amount,"currency":q.currency,"includes_insurance":q.includes_insurance,"valid_until":q.valid_until.isoformat() if q.valid_until else None,"provenance":q.provenance.value} for q in ranked],"recommendation":recommendation,"recommendation_warning":warning,"note":"Quote ranking compares user-provided quotes only; no FX conversion, market-price lookup, or booking is performed."}


def impact_options(shipment: ShipmentRequest) -> dict:
    """Evaluate split allocations over explicitly modeled synthetic corridors."""
    import itertools

    scenario = shipment.impact_scenario
    weight = Decimal(str(shipment.weight_kg))
    critical = Decimal(str(scenario.critical_weight_kg))
    domestic_paths = india_impact_paths(shipment)
    # id, origin, destination, mode, duration in days, INR per kg, capacity kg
    edges = [
        ("pickup", "Dubai warehouse", "Dubai transfer hub", "road", .5, 5, 10000),
        ("air", "Dubai transfer hub", "Mumbai transfer hub", "air", 2, 410, 500),
        ("ocean", "Dubai transfer hub", "Mumbai transfer hub", "sea", 16, 80, 10000),
        ("delivery", "Mumbai transfer hub", "Mumbai warehouse", "road", .5, 5, 10000),
    ]
    legacy_lane = (shipment.origin.casefold(), shipment.destination.casefold()) == ("dubai", "mumbai")
    paths = domestic_paths or ([(edges[0], main, edges[3]) for main in edges[1:3]] if legacy_lane else [])
    road_paths = []
    closed_ids = {value.casefold().strip() for value in scenario.closed_leg_ids}
    if "road" in shipment.allowed_modes and "road" not in closed_ids:
        road_paths = india_road_paths(shipment, scenario.closed_leg_ids)
    # Explicit road segments supersede the one-hop regional approximations.
    if road_paths or india_road_paths(shipment.model_copy(update={"disruptions": []})):
        paths = [path for path in paths if path[1][3] != "road"]
    paths = [*paths, *road_paths]
    names = {path: (f"road-{road_paths.index(path) + 1}" if path in road_paths else path[1][0])
             for path in paths}
    # Each ordered pair assigns the critical and remaining portions respectively.
    options, rejected = [], []
    if not paths:
        rejected.append(dict(option=None, reasons=["no_connected_impact_corridor"]))
    remaining_days = ((shipment.deadline - datetime.now(timezone.utc)).total_seconds() / 86400
                      if shipment.deadline else None)
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
                if leg.casefold() in closed_ids:
                    reasons.add(f"closed:{leg}")
                if mode not in shipment.allowed_modes:
                    reasons.add(f"mode:{mode}")
                charge = mass * Decimal(str(rate)) * Decimal(str(scenario.freight_multiplier))
                freight += charge
                road_graph_leg = leg.startswith("IN-ROAD-")
                legs.append(dict(id=leg,origin=origin,destination=destination,mode=mode,freight_inr=float(charge),
                                 rate_inr_per_kg=rate, capacity_kg=capacity, base_transit_hours=days * 24,
                                 distance_km=road_leg_km(origin, destination) if road_graph_leg else None,
                                 provenance="ESTIMATED" if road_graph_leg else "SYNTHETIC"))
            signals = [
                signal for signal in shipment.disruptions
                if any(disruption_matches_leg(signal, mode, start, end, leg) for leg, start, end, mode, *_ in path)
            ]
            reasons.update(f"disruption:{signal.signal_id}" for signal in signals if signal.severity >= .95)
            eta = sum(edge[4] for edge in path) + sum(signal.delay_hours for signal in signals) / 24
            if remaining_days is not None and eta > remaining_days:
                reasons.add("hard_deadline")
            portions.append(dict(weight_kg=float(mass), arrival_days=eta, legs=legs,
                                 disruption_ids=[signal.signal_id for signal in signals]))
        name = "/".join(names[path] for path, _ in loads)
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
                source_references=[MORTH_CORRIDORS, MORTH_HIGHWAY_MAP] if road_paths else [],
                preferred=options[0]["option"] if options else None,
                assumptions=["Critical cargo arrives with the first portion and ends the modeled stoppage.",
                             "Rates, hubs, capacities and durations are synthetic. Taxes and insurance excluded."])
