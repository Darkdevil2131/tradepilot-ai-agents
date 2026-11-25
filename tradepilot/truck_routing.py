"""HERE truck-route input and response normalization."""
from datetime import datetime, timezone
from decimal import Decimal
from itertools import islice
from math import isfinite

from flexpolyline import iter_decode

HERE_URL = "https://router.hereapi.com/v8/routes"
HERE_REFERENCE = "https://docs.here.com/routing/docs/routing-v8-truck-routing"


def _valid_measurement(value):
    return type(value) in (int, float) and isfinite(value) and value >= 0


def _sum_if_complete(values):
    return sum(values) if values and all(_valid_measurement(value) for value in values) else None


def _toll_amount(summary):
    total = (summary.get("tolls") or {}).get("total") or {}
    # A missing, unsupported, or different-currency price stays unknown.
    value = total.get("value")
    if total.get("type") == "value" and total.get("currency") == "INR" and _valid_measurement(value):
        return Decimal(str(value))
    return None


def _normalize(raw, top_notices, retrieved_at):
    sections = raw["sections"]
    if not 1 <= len(sections) <= 64:
        raise ValueError("Invalid section count")
    summaries = [section["summary"] for section in sections]
    distance = _sum_if_complete([row.get("length") for row in summaries])
    duration = _sum_if_complete([row.get("duration") for row in summaries])
    if not distance or not duration:
        raise ValueError("Missing route length or duration")
    base = _sum_if_complete([row.get("baseDuration") for row in summaries])
    amounts = [_toll_amount(row) for row in summaries]
    toll = float(sum(amounts)) if all(amount is not None for amount in amounts) else None
    notices = [*top_notices, *raw.get("notices", []),
               *(notice for section in sections for notice in section.get("notices", []))]
    codes = sorted({str(notice.get("code") or "unknown_notice")[:120] for notice in notices})
    violated = any(code.casefold().startswith("violated") for code in codes)
    critical = any(notice.get("severity") == "critical" for notice in notices)
    coordinates = []
    for section in sections:
        encoded = section["polyline"]
        if not isinstance(encoded, str) or len(encoded) > 500000:
            raise ValueError("Invalid truck route geometry")
        points = [(lon, lat) for lat, lon, *_ in islice(iter_decode(encoded), 40001)]
        if len(points) < 2:
            raise ValueError("Missing truck route geometry")
        # Join sections only at a shared endpoint.
        if coordinates and coordinates[-1] != points[0]:
            raise ValueError("Truck route sections do not connect")
        coordinates.extend(points[1:] if coordinates else points)
        if len(coordinates) > 40000:
            raise ValueError("Truck route geometry exceeds limit")
    return dict(
        distance=distance, duration=duration,
        geometry=dict(type="LineString", coordinates=coordinates),
        truck_evidence=dict(
            retrieved_at_utc=retrieved_at, provider_duration_hours=round(duration / 3600, 3),
            traffic_status="time_aware" if base is not None else "baseline_unavailable",
            base_duration_hours=round(base / 3600, 3) if base is not None else None,
            time_aware_delta_hours=round((duration - base) / 3600, 3) if base is not None else None,
            toll_status="priced" if toll is not None else "unavailable", toll_inr=toll,
            access_status="restriction_reported" if violated or critical else
                          "review_required" if notices else "no_reported_violation",
            notices=codes,
        ),
    )


def fetch_truck_routes(client, request, api_key):
    truck = request.truck
    params = {
        "apiKey": api_key, "transportMode": "truck", "routingMode": "fast",
        "origin": f"{request.origin.latitude},{request.origin.longitude}",
        "destination": f"{request.destination.latitude},{request.destination.longitude}",
        "alternatives": "2", "return": "polyline,summary,tolls", "currency": "INR",
        "tolls[summaries]": "total",
        "vehicle[height]": truck.height_cm, "vehicle[width]": truck.width_cm,
        "vehicle[length]": truck.length_cm, "vehicle[currentWeight]": truck.loaded_weight_kg,
        "vehicle[grossWeight]": truck.gross_weight_rating_kg,
        "vehicle[weightPerAxle]": truck.max_axle_weight_kg, "vehicle[axleCount]": truck.axle_count,
    }
    if request.departure_at > datetime.now(timezone.utc):
        params["departureTime"] = request.departure_at.isoformat()
    response = client.get(HERE_URL, params=params)
    response.raise_for_status()
    data = response.json()
    routes = data["routes"]
    if not isinstance(routes, list):
        raise ValueError("Invalid truck response")
    retrieved_at = datetime.now(timezone.utc)
    normalized = []
    for raw in routes[:3]:
        try:
            normalized.append(_normalize(raw, data.get("notices", []), retrieved_at))
        except (ValueError, TypeError, IndexError, KeyError, AttributeError, OverflowError):
            continue
    if routes and not normalized:
        raise ValueError("No usable truck route response")
    return {"code": "Ok", "routes": normalized}
