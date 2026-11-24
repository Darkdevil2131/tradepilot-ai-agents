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

def calculate_routes(shipment: ShipmentRequest) -> list[dict]:
    distance = haversine_km(shipment.origin, shipment.destination)
    results = []
    tonnes = shipment.weight_kg / 1000
    origin=shipment.origin.casefold(); destination=shipment.destination.casefold()
    same_country=CITY_COUNTRIES.get(origin) is not None and CITY_COUNTRIES.get(origin)==CITY_COUNTRIES.get(destination)
    for mode in shipment.allowed_modes:
        if mode == "road":
            mode_feasible=same_country
            feasibility_reason="Sample cities share a country; this is only a demo eligibility check, not a road-network route." if mode_feasible else "Offline road mode is limited to sample city pairs in the same country; no cross-border road routing is modeled."
        elif mode == "sea":
            mode_feasible=origin in PORT_CITIES and destination in PORT_CITIES
            feasibility_reason="Both sample cities are treated as port nodes; a real maritime lane is not calculated." if mode_feasible else "Sea mode requires both sample cities to be in the built-in port list; inland transfers are not modeled."
        else:
            mode_feasible=True
            feasibility_reason="Both cities are in the offline sample table; airport availability, schedules, and actual service are not verified."
        # Detour factors only approximate network routing; distances are not road/sea network paths.
        factor = {"air": 1.08, "sea": 1.65, "road": 1.25}[mode]
        network_estimate = distance * factor
        transit_hours = network_estimate / MODE_KMH[mode] + {"air": 12, "sea": 48, "road": 8}[mode]
        cost = max(25.0, tonnes * network_estimate * MODE_RATE_USD_TON_KM[mode])
        deadline_ok = shipment.deadline is None or transit_hours <= max(0, (shipment.deadline - datetime.now(timezone.utc)).total_seconds()/3600)
        feasible=mode_feasible and deadline_ok
        results.append({"mode":mode,"distance_km":round(network_estimate,1),"transit_hours_estimate":round(transit_hours,1),"cost_estimate_usd":round(cost,2),"mode_feasible":mode_feasible,"deadline_feasible":deadline_ok,"feasible":feasible,"feasibility_reason":feasibility_reason,"risk_score_estimate": {"air":0.12,"sea":0.30,"road":0.22}[mode],"provenance":Provenance.ESTIMATED.value,"distance_method":"straight-line distance × fixed demo factor; not a navigable route"})
    return sorted(results,key=lambda r:(not r["feasible"],r["cost_estimate_usd"],r["transit_hours_estimate"]))

def validate_documents(shipment: ShipmentRequest) -> dict:
    by_type = {doc.document_type: doc for doc in shipment.documents}
    required = ["commercial_invoice", "packing_list", "bill_of_lading"]
    missing = [kind for kind in required if kind not in by_type]
    issues = []
    invoice = by_type.get("commercial_invoice")
    if invoice:
        for key in ("invoice_number", "shipper", "consignee", "currency", "total"):
            if not invoice.fields.get(key): issues.append(f"commercial_invoice is missing {key}")
        total = invoice.fields.get("total")
        if isinstance(total,(int,float)) and shipment.declared_value is not None and abs(float(total)-shipment.declared_value) > max(1.0,shipment.declared_value*0.01):
            issues.append("commercial_invoice total differs from shipment declared_value by more than 1%")
    bol = by_type.get("bill_of_lading")
    if bol and not bol.fields.get("consignee"): issues.append("bill_of_lading is missing consignee")
    packing = by_type.get("packing_list")
    if packing and not packing.fields.get("package_count"): issues.append("packing_list is missing package_count")
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
