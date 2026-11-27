from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from .models import ShipmentRequest
from .tools import disruption_matches_leg, effective_departure


MODE_RELIABILITY = {"air": 0.88, "sea": 0.78, "road": 0.82, "rail": 0.84}


def _normalized(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def rank_routes(shipment: ShipmentRequest, routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply user-provided disruptions and deterministic multi-objective scoring."""
    if not routes:
        return []
    adjusted: list[dict[str, Any]] = []
    remaining_hours = None
    if shipment.deadline:
        remaining_hours = max(0.0, (shipment.deadline - effective_departure(shipment)).total_seconds() / 3600)

    for original in routes:
        route = dict(original)
        if route.get("weather"):
            adjusted.append(route)
            continue
        legs = route.get("legs") or [{"mode": route["mode"], "from": shipment.origin, "to": shipment.destination}]

        matched = []
        added_delay = 0.0
        added_risk = 0.0
        blocking = False
        for signal in shipment.disruptions:
            if any(disruption_matches_leg(signal, leg["mode"], leg["from"], leg["to"],
                                          leg.get("id"), leg.get("via", ())) for leg in legs):
                matched.append(signal.signal_id)
                added_delay += signal.delay_hours
                added_risk += signal.severity * 0.35
                blocking = blocking or signal.severity >= 0.95

        route["transit_hours_estimate"] = round(route["transit_hours_estimate"] + added_delay, 1)
        route["risk_score_estimate"] = round(min(1.0, route["risk_score_estimate"] + added_risk), 3)
        route["disruption_ids"] = matched
        if remaining_hours is not None:
            route["deadline_feasible"] = route["transit_hours_estimate"] <= remaining_hours
        if blocking:
            route["mode_feasible"] = False
            route["feasibility_reason"] = (
                route["feasibility_reason"] + " Blocked by critical disruption: " + ", ".join(matched)
            ).strip()
        elif matched:
            route["feasibility_reason"] = (
                route["feasibility_reason"] + " Adjusted for disruption: " + ", ".join(matched)
            ).strip()
        route["feasible"] = bool(route["mode_feasible"] and route["deadline_feasible"])
        adjusted.append(route)

    feasible = [route for route in adjusted if route["feasible"]]
    pool = feasible or adjusted
    costs = [route["cost_estimate_usd"] for route in pool]
    times = [route["transit_hours_estimate"] for route in pool]
    cost_low, cost_high = min(costs), max(costs)
    time_low, time_high = min(times), max(times)
    preferences = shipment.optimization
    total_weight = (
        preferences.cost_weight + preferences.time_weight +
        preferences.risk_weight + preferences.reliability_weight
    )

    for route in adjusted:
        cost_score = _normalized(route["cost_estimate_usd"], cost_low, cost_high)
        time_score = _normalized(route["transit_hours_estimate"], time_low, time_high)
        risk_score = route["risk_score_estimate"]
        reliability_penalty = 1.0 - MODE_RELIABILITY.get(route["mode"], 0.5)
        score = (
            preferences.cost_weight * cost_score +
            preferences.time_weight * time_score +
            preferences.risk_weight * risk_score +
            preferences.reliability_weight * reliability_penalty
        ) / total_weight
        if not route["feasible"]:
            score = min(1.0, score + 0.5)
        route["decision_score"] = round(score, 6)
        route["score_breakdown"] = {
            "normalized_cost": round(cost_score, 6),
            "normalized_time": round(time_score, 6),
            "risk": round(risk_score, 6),
            "reliability_penalty": round(reliability_penalty, 6),
        }

    return sorted(
        adjusted,
        key=lambda route: (
            not route["feasible"],
            route["decision_score"],
            route["cost_estimate_usd"],
            route["transit_hours_estimate"],
            route["mode"],
        ),
    )


def rank_supplier_offers(shipment: ShipmentRequest) -> dict[str, Any]:
    """Rank comparable supplier offers without inventing FX rates or availability."""
    required_quantity = shipment.required_quantity
    required = {item.strip().casefold() for item in shipment.required_certifications if item.strip()}
    eligible = []
    excluded = []
    for offer in shipment.supplier_offers:
        missing = sorted(required - {item.strip().casefold() for item in offer.certifications})
        reasons = []
        if required_quantity is not None and offer.available_quantity < required_quantity:
            reasons.append("insufficient_quantity")
        if shipment.requires_submitted_offers and offer.provenance != "USER_PROVIDED":
            reasons.append("submitted_supplier_evidence_required")
        if missing:
            reasons.append("missing_certifications:" + ",".join(missing))
        row = offer.model_dump(mode="json")
        row["total_price"] = (float(round(Decimal(str(offer.unit_price)) * Decimal(str(required_quantity)), 2))
                              if required_quantity is not None else None)
        row["exclusion_reasons"] = reasons
        (excluded if reasons else eligible).append(row)

    if not eligible:
        return {
            "ranked": [],
            "recommendation": None,
            "warning": "No supplier offer satisfies quantity and certification requirements." if shipment.supplier_offers else None,
            "excluded": excluded,
        }

    currencies = {row["currency"] for row in eligible}
    for currency in currencies:
        group = [row for row in eligible if row["currency"] == currency]
        prices = [row["unit_price"] for row in group]
        leads = [row["lead_time_days"] for row in group]
        for row in group:
            price = _normalized(row["unit_price"], min(prices), max(prices))
            lead = _normalized(row["lead_time_days"], min(leads), max(leads))
            quality_penalty = 1.0 - row["quality_score"]
            row["decision_score"] = round(0.50 * price + 0.25 * lead + 0.25 * quality_penalty, 6)
            row["score_breakdown"] = {
                "normalized_price": round(price, 6),
                "normalized_lead_time": round(lead, 6),
                "quality_penalty": round(quality_penalty, 6),
            }
    eligible.sort(key=lambda row: (row["currency"], row["decision_score"], row["supplier_id"]))
    recommendation = None
    warning = None
    if len(currencies) > 1:
        warning = "Supplier offers use multiple currencies; no FX conversion is configured, so no cross-currency winner is selected."
    elif required_quantity is None:
        warning = "Supply the required quantity before selecting a supplier."
    else:
        recommendation = {
            "supplier_id": eligible[0]["supplier_id"],
            "name": eligible[0]["name"],
            "total_price": eligible[0]["total_price"],
            "currency": eligible[0]["currency"],
            "lead_time_days": eligible[0]["lead_time_days"],
            "requires_human_approval": True,
            "provenance": eligible[0]["provenance"],
        }
    return {"ranked": eligible, "recommendation": recommendation, "warning": warning, "excluded": excluded}


_FIELD_PATTERNS = {
    "air_waybill_number": r"(?im)^[ \t]*(?:air[ \t]+waybill|awb)[ \t]*(?:number|no\.?|#)?[ \t]*[:\-][ \t]*(.+?)[ \t]*$",
    "consignment_number": r"(?im)^[ \t]*consignment[ \t]*(?:number|no\.?|#)[ \t]*[:\-][ \t]*(.+?)[ \t]*$",
    "invoice_number": r"(?im)^[ \t]*invoice[ \t]*(?:number|no\.?|#)[ \t]*[:\-][ \t]*(\S.*?)[ \t]*$",
    "shipper": r"(?im)^[ \t]*(?:shipper|exporter|seller)[ \t]*[:\-][ \t]*(\S.*?)[ \t]*$",
    "consignee": r"(?im)^[ \t]*(?:consignee|buyer)[ \t]*[:\-][ \t]*(\S.*?)[ \t]*$",
    "currency": r"(?im)^[ \t]*currency[ \t]*[:\-][ \t]*([A-Z]{3})[ \t]*$",
    "total": r"(?im)^[ \t]*(?:(?:invoice|grand)[ \t]*)?total[ \t]*[:\-][ \t]*(?:INR[ \t]*|₹[ \t]*)?([0-9][0-9,]*(?:\.[0-9]+)?)[ \t]*$",
    "package_count": r"(?im)^\s*(?:package\s*count|packages)\s*[:\-]\s*([0-9]+)\s*$",
}


def read_document_labels(raw_text: str | None) -> tuple[dict, list[str]]:
    """Return labelled fields and names of fields with inconsistent repetitions."""
    if not raw_text:
        return {}, []
    extracted = {}
    conflicts = []
    for field, pattern in _FIELD_PATTERNS.items():
        matches = [match.group(1).strip() for match in re.finditer(pattern, raw_text)]
        if not matches:
            continue
        values = matches
        if field == "total":
            values = [value.replace(",", "") if "," not in value or re.fullmatch(
                r"(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3})(?:\.\d+)?", value)
                      else value for value in values]
        normalized = (set(Decimal(value) if "," not in value else value for value in values)
                      if field in {"total", "package_count"} else
                      {" ".join(value.casefold().split()) for value in values})
        if len(normalized) > 1:
            conflicts.append(field)
        extracted[field] = (int(values[0]) if field == "package_count" and len(values[0]) <= 12 else
                            values[0].upper() if field == "currency" else values[0])
    return extracted, conflicts


def extract_document_fields(raw_text: str | None) -> dict[str, str | int | float]:
    return read_document_labels(raw_text)[0]
