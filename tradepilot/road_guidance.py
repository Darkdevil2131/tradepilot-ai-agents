"""Operator-configured OSRM routing and Open-Meteo hourly forecast adapters."""
from __future__ import annotations

from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import asin, ceil, cos, isfinite, radians, sin, sqrt
import json
import os
import re

import httpx

from .models import RouteOptimizationOutput, ShipmentRequest
from .tools import disruption_matches_leg
from .truck_routing import HERE_REFERENCE, fetch_truck_routes


ROUTING_REFERENCE = "https://project-osrm.org/docs/"
WEATHER_REFERENCE = "https://open-meteo.com/en/docs"
FIELDS = ("precipitation", "wind_gusts_10m", "visibility", "weather_code")
MAX_ROUTES = 3
MAX_SAMPLES = 12


def _km(a, b):
    lon1, lat1, lon2, lat2 = map(radians, (*a, *b))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * asin(sqrt(min(1, h)))


def _number(value, *, minimum=0, maximum=None):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
        raise ValueError("invalid provider measurement")
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError("provider measurement out of bounds")
    return value


def _shape(raw):
    geometry = raw["geometry"]
    if geometry["type"] != "LineString" or not 2 <= len(geometry["coordinates"]) <= 40000:
        raise ValueError("invalid route geometry")
    return [(_number(lon, minimum=-180, maximum=180), _number(lat, minimum=-90, maximum=90))
            for lon, lat in geometry["coordinates"]]


def _segment_clock(raw, shape):
    try:
        durations = [_number(value) for value in raw["legs"][0]["annotation"]["duration"]]
        if len(raw["legs"]) == 1 and len(durations) == len(shape) - 1 and sum(durations) > 0:
            cumulative = [0.0]
            for duration in durations:
                cumulative.append(cumulative[-1] + duration)
            return cumulative
    except (TypeError, ValueError, KeyError, IndexError):
        pass
    return None


def _sample_route(shape, distance_km, hours, departure, segment_clock):
    offsets = [0.0]
    for first, second in zip(shape, shape[1:]):
        offsets.append(offsets[-1] + _km(first, second))
    if offsets[-1] <= 0:
        raise ValueError("no route length")
    count = min(MAX_SAMPLES, max(3, ceil(distance_km / 100) + 1))
    samples = []
    for index in range(count):
        progress = index / (count - 1)
        along = offsets[-1] * progress
        end = min(len(shape) - 1, max(1, bisect_left(offsets, along)))
        length = offsets[end] - offsets[end - 1]
        fraction = (along - offsets[end - 1]) / length if length else 0
        lon, lat = shape[end - 1]
        next_lon, next_lat = shape[end]
        lon_delta = (next_lon - lon + 180) % 360 - 180
        eta_progress = progress
        if segment_clock and 0 < index < count - 1:
            start_seconds, end_seconds = segment_clock[end - 1], segment_clock[end]
            eta_progress = (start_seconds + fraction * (end_seconds - start_seconds)) / segment_clock[-1]
        samples.append(dict(
            latitude=round(lat + fraction * (next_lat - lat), 5),
            longitude=round((lon + fraction * lon_delta + 180) % 360 - 180, 5),
            route_km=round(distance_km * progress, 2),
            estimated_arrival_utc=departure + timedelta(hours=hours * eta_progress),
            flag="unavailable",
        ))
    return samples


def _annotate(sample, forecast):
    """Check the forecast hour ending at or immediately after arrival."""
    arrival = sample["estimated_arrival_utc"]
    hour = arrival.replace(minute=0, second=0, microsecond=0)
    if hour < arrival:
        hour += timedelta(hours=1)
    try:
        hourly = forecast["hourly"]
        units = forecast["hourly_units"]
        if (units["precipitation"], units["wind_gusts_10m"], units["visibility"]) != ("mm", "km/h", "m"):
            return
        index = hourly["time"].index(hour.strftime("%Y-%m-%dT%H:00"))
        rain, gust, visibility, code = (_number(hourly[field][index]) for field in FIELDS)
        if int(code) != code:
            return
    except (TypeError, ValueError, KeyError, IndexError, AttributeError):
        return
    # Planning flags only. They are neither official warnings nor road-closure data.
    if code in {95, 96, 99} or rain >= 15 or gust >= 70 or visibility <= 500:
        flag = "high"
    elif code in {45, 48, 65, 82} or rain >= 5 or gust >= 45 or visibility <= 1500:
        flag = "elevated"
    else:
        flag = "low"
    sample.update(forecast_hour_utc=hour, precipitation_mm=rain, wind_gust_kmh=gust,
                  visibility_m=visibility, weather_code=int(code), flag=flag)


def _forecast(client, endpoint, samples):
    if not endpoint or not samples:
        return None
    response = client.get(endpoint, params=dict(
        latitude=",".join(str(row["latitude"]) for row in samples),
        longitude=",".join(str(row["longitude"]) for row in samples),
        hourly=",".join(FIELDS), timezone="GMT", forecast_days=7,
        wind_speed_unit="kmh", precipitation_unit="mm",
    ))
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list) or len(data) != len(samples):
        return None
    for sample, row in zip(samples, data):
        _annotate(sample, row)
    return datetime.now(timezone.utc)


def _output(status, routes=()):
    routes = sorted(routes, key=lambda item: (
        not item["feasible"],
        {"low": 0, "elevated": 1, "high": 2, "unknown": 3}[item["weather"]["severity"]],
        (item.get("truck_evidence") or {}).get("access_status") == "review_required",
        item["transit_hours_estimate"],
        (item.get("truck_evidence") or {}).get("toll_inr") or 0,
        item["distance_km"],
    ))
    preferred = next((route for route in routes if route["feasible"]), None)
    return RouteOptimizationOutput(
        routes=routes, recommended_route=preferred, no_feasible_route=preferred is None,
        provenance="ESTIMATED", provider_status=status,
        ranking_method="Filter by evidence, deadlines, restrictions and any toll budget. Prefer lower weather flags, fewer truck notices, shorter estimated transit, then lower comparable tolls. Freight cost is unavailable.",
        alternatives=[dict(evidence_ids=row["evidence_ids"], feasible=row["feasible"],
                           distance_km=row["distance_km"], transit_hours_estimate=row["transit_hours_estimate"],
                           weather_status=row["weather"]["status"], weather_severity=row["weather"]["severity"],
                           toll_inr=(row.get("truck_evidence") or {}).get("toll_inr"),
                           truck_access=(row.get("truck_evidence") or {}).get("access_status"),
                           reason=row["feasibility_reason"]) for row in routes],
    )


def provider_road_guidance(shipment: ShipmentRequest) -> RouteOptimizationOutput:
    routing_base = os.getenv("TRADEPILOT_OSRM_URL", "").strip().rstrip("/")
    weather_url = os.getenv("TRADEPILOT_FORECAST_URL", "").strip()
    profile = os.getenv("TRADEPILOT_OSRM_PROFILE", "driving")
    request = shipment.road_guidance
    is_truck = request.truck is not None
    here_key = os.getenv("TRADEPILOT_HERE_API_KEY", "").strip()
    if is_truck:
        if os.getenv("TRADEPILOT_HERE_TRUCK_ENABLED", "").casefold() != "true" or not here_key:
            return _output("not_configured")
    elif not routing_base or not re.fullmatch(r"[A-Za-z0-9_-]+", profile):
        return _output("not_configured")
    start = (request.origin.longitude, request.origin.latitude)
    end = (request.destination.longitude, request.destination.latitude)
    coordinates = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in (start, end))
    routes = []
    try:
        provider_client = httpx.Client(timeout=httpx.Timeout(5, connect=3), follow_redirects=False)
    except (ImportError, ValueError):
        return _output("routing_unavailable")
    with provider_client as client:
        try:
            if is_truck:
                data = fetch_truck_routes(client, request, here_key)
                response_status = 200
            else:
                response = client.get(f"{routing_base}/route/v1/{profile}/{coordinates}",
                                      params=dict(alternatives=2, geometries="geojson", overview="full", annotations="duration"))
                if response.status_code not in {200, 400}:
                    response.raise_for_status()
                data = response.json()
                response_status = response.status_code
            if data.get("code") in {"NoRoute", "NoSegment"}:
                return _output("no_route")
            if response_status != 200 or data.get("code") != "Ok" or not isinstance(data.get("routes"), list):
                return _output("routing_unavailable")
            seen = set()
            for raw in data["routes"][:MAX_ROUTES]:
                try:
                    shape = _shape(raw)
                    if _km(start, shape[0]) > 2 or _km(end, shape[-1]) > 2:
                        continue
                    km = _number(raw["distance"], minimum=1, maximum=20_000_000) / 1000
                    hours = _number(raw["duration"], minimum=1, maximum=2_000_000) / 3600
                    prefix = "HERE-TRUCK-" if is_truck else "OSRM-"
                    route_profile = request.truck.model_dump() if is_truck else profile
                    key = prefix + sha256(json.dumps([route_profile, shape]).encode()).hexdigest()[:20]
                    if key in seen:
                        continue
                    signals = [signal for signal in shipment.disruptions
                               if disruption_matches_leg(signal, "road", shipment.origin, shipment.destination, key)]
                    hours += sum(signal.delay_hours for signal in signals)
                    clock = _segment_clock(raw, shape)
                    samples = _sample_route(shape, km, hours, request.departure_at, clock)
                    seen.add(key)
                    routes.append((key, shape, km, hours, samples, signals, clock is not None, raw.get("truck_evidence")))
                except (TypeError, ValueError, KeyError, IndexError):
                    continue
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError):
            return _output("routing_unavailable")
        if not routes:
            return _output("routing_unavailable" if data["routes"] else "no_route")
        samples = [sample for _, _, _, _, points, _, _, _ in routes for sample in points]
        try:
            fetched_at = _forecast(client, weather_url, samples)
        except (httpx.HTTPError, ValueError, TypeError):
            fetched_at = None
    options = []
    for key, shape, km, hours, points, signals, has_clock, truck in routes:
        coverage = sum(point["flag"] != "unavailable" for point in points)
        status = "complete" if coverage == len(points) else "partial" if coverage else "unavailable"
        severity = ("high" if any(point["flag"] == "high" for point in points) else
                    "unknown" if status != "complete" else
                    "elevated" if any(point["flag"] == "elevated" for point in points) else "low")
        deadline_ok = shipment.deadline is None or request.departure_at + timedelta(hours=hours) <= shipment.deadline
        reasons = []
        if status != "complete":
            reasons.append("Forecast coverage is incomplete")
        if severity == "high":
            reasons.append("An hourly forecast sample triggered a high weather flag")
        if not deadline_ok:
            reasons.append("Modeled arrival misses the deadline")
        if any(signal.severity >= .95 for signal in signals):
            reasons.append("Reported disruption blocks this route")
        if truck:
            if truck["access_status"] == "restriction_reported":
                reasons.append("Provider reported a truck restriction or critical notice")
            if truck["traffic_status"] != "time_aware":
                reasons.append("Provider did not return a comparable traffic baseline")
            if truck["toll_status"] != "priced":
                reasons.append("Provider did not return a complete INR toll price")
            elif request.max_toll_inr is not None and truck["toll_inr"] > request.max_toll_inr:
                reasons.append("Toll estimate exceeds the stated budget")
        options.append(dict(
            mode="road", distance_km=round(km, 2), transit_hours_estimate=round(hours, 3),
            mode_feasible=not truck or truck["access_status"] != "restriction_reported",
            deadline_feasible=deadline_ok, feasible=not reasons,
            feasibility_reason="; ".join(reasons) if reasons else
            ("Truck profile, time-aware duration, INR toll estimate and sampled forecasts are available." if truck else
             "Provider geometry and sampled forecast are available. Truck access and live traffic require confirmation."),
            risk_score_estimate={"low": .1, "elevated": .5, "high": .9, "unknown": .8}[severity],
            provenance="ESTIMATED", distance_method=("HERE truck geometry and time-aware duration estimate." if truck else
                                                     "OSRM road geometry and modeled drive time; no live traffic adjustment."),
            evidence_ids=[key], geometry_lon_lat=shape,
            source_references=[HERE_REFERENCE if truck else ROUTING_REFERENCE, WEATHER_REFERENCE],
            truck_evidence=truck,
            disruption_ids=[signal.signal_id for signal in signals],
            weather=dict(status=status, severity=severity, retrieved_at_utc=fetched_at, samples=points,
                         sample_eta_method="segment_duration" if has_clock else "distance_interpolation"),
        ))
    statuses = {option["weather"]["status"] for option in options}
    provider_status = "available" if statuses == {"complete"} else "weather_unavailable" if statuses == {"unavailable"} else "weather_partial"
    if is_truck and provider_status == "available" and any(
        row["truck_evidence"]["traffic_status"] != "time_aware" or row["truck_evidence"]["toll_status"] != "priced"
        for row in options
    ):
        provider_status = "truck_evidence_incomplete"
    return _output(provider_status, options)
