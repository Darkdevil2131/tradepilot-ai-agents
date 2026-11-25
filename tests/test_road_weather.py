from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from tradepilot import road_guidance


@pytest.fixture
def road_trip():
    departure = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return dict(
        origin="Kochi", destination="Chennai", cargo_description="Machine parts",
        weight_kg=500, allowed_modes=["road"],
        road_guidance=dict(origin=dict(latitude=9.9312, longitude=76.2673),
                           destination=dict(latitude=13.0827, longitude=80.2707),
                           departure_at=departure.isoformat()),
    )


def install_provider(monkeypatch, road_trip, weather="storm"):
    calls = []
    departure = datetime.fromisoformat(road_trip["road_guidance"]["departure_at"])
    timestamps = [(departure + timedelta(hours=index)).strftime("%Y-%m-%dT%H:00") for index in range(48)]
    endpoints = [[76.2673, 9.9312], [80.2707, 13.0827]]

    def handler(request):
        calls.append(request)
        if request.url.host == "routes.example":
            return httpx.Response(200, json={"code": "Ok", "routes": [
                dict(distance=700000, duration=36000, geometry=dict(
                    type="LineString", coordinates=[endpoints[0], [77.90, 10.8], endpoints[1]])),
                dict(distance=760000, duration=43200, geometry=dict(
                    type="LineString", coordinates=[endpoints[0], [77.4, 12.4], endpoints[1]]),
                    legs=[{"annotation": {"duration": [32400, 10800]}}]),
            ]})
        if weather == "down":
            return httpx.Response(503)
        params = request.url.params
        count = len(params["latitude"].split(","))
        assert count == len(params["longitude"].split(",")) == 17
        assert params["timezone"] == "GMT"
        rows = []
        for index in range(count):
            severe = weather == "storm" and index == 3
            missing = weather == "missing" and index == 1
            rows.append(dict(
                hourly_units=dict(precipitation="mm", wind_gusts_10m="km/h", visibility="m"),
                hourly=dict(time=timestamps, precipitation=[20 if severe else 0] * 48,
                            wind_gusts_10m=[None if missing else 10] * 48,
                            visibility=[10000] * 48, weather_code=[95 if severe else 0] * 48),
            ))
        return httpx.Response(200, json=rows)

    monkeypatch.setenv("TRADEPILOT_OSRM_URL", "https://routes.example")
    monkeypatch.setenv("TRADEPILOT_FORECAST_URL", "https://weather.example/v1/forecast")
    monkeypatch.setattr(road_guidance, "httpx", SimpleNamespace(
        Client=lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler), **kwargs),
        Timeout=httpx.Timeout, HTTPError=httpx.HTTPError,
    ))
    return calls


def analyze(client, headers, data):
    response = client.post("/api/v1/shipments/analyze", headers=headers, json=data)
    assert response.status_code == 200, response.text
    return response.json()["agents"]


def test_weather_can_select_slower_detour(client, headers, monkeypatch, road_trip):
    calls = install_provider(monkeypatch, road_trip)
    agents = analyze(client, headers, road_trip)
    assert all(agent["status"] == "completed" for agent in agents.values())
    routes = agents["route_optimization"]["data"]
    assert routes["provider_status"] == "available"
    preferred = routes["recommended_route"]
    assert preferred["distance_km"] == 760
    assert preferred["cost_estimate_usd"] is None
    assert preferred["weather"]["severity"] == "low"
    assert preferred["weather"]["sample_eta_method"] == "segment_duration"
    halfway = datetime.fromisoformat(preferred["weather"]["samples"][4]["estimated_arrival_utc"])
    assert halfway > datetime.fromisoformat(road_trip["road_guidance"]["departure_at"]) + timedelta(hours=6)
    assert preferred["weather"]["samples"][-1]["estimated_arrival_utc"] == (
        datetime.fromisoformat(road_trip["road_guidance"]["departure_at"]) + timedelta(hours=12)
    ).isoformat().replace("+00:00", "Z")
    storm = next(route for route in routes["routes"] if route["distance_km"] == 700)
    assert not storm["feasible"] and storm["weather"]["severity"] == "high"
    assert storm["weather"]["sample_eta_method"] == "distance_interpolation"
    assert storm["weather"]["samples"][3]["precipitation_mm"] == 20
    assert len(calls) == 2


def test_missing_weather_blocks_commercial_fallback(client, headers, monkeypatch, road_trip):
    install_provider(monkeypatch, road_trip, "down")
    agents = analyze(client, headers, road_trip)
    routes = agents["route_optimization"]["data"]
    assert routes["provider_status"] == "weather_unavailable"
    assert routes["recommended_route"] is None
    assert all(route["weather"]["severity"] == "unknown" for route in routes["routes"])
    assert all(route["geometry_lon_lat"] for route in routes["routes"])
    decision = agents["logistics_orchestrator"]["data"]["decision"]
    assert decision["candidate"] is None
    assert next(gate for gate in decision["gates"] if gate["gate"] == "route_weather")["status"] == "blocked"


def test_partial_forecast_and_delayed_departure(client, headers, monkeypatch, road_trip):
    install_provider(monkeypatch, road_trip, "missing")
    road_trip["disruptions"] = [dict(signal_id="traffic-report", affected_modes=["road"], severity=.4, delay_hours=3)]
    agents = analyze(client, headers, road_trip)
    routes = agents["route_optimization"]["data"]
    assert routes["provider_status"] == "weather_partial"
    assert routes["recommended_route"]["transit_hours_estimate"] == 15
    assert routes["recommended_route"]["weather"]["samples"][-1]["estimated_arrival_utc"] == (
        datetime.fromisoformat(road_trip["road_guidance"]["departure_at"]) + timedelta(hours=15)
    ).isoformat().replace("+00:00", "Z")


def test_unconfigured_provider_does_not_return_offline_route(client, headers, monkeypatch, road_trip):
    monkeypatch.delenv("TRADEPILOT_OSRM_URL", raising=False)
    routes = analyze(client, headers, road_trip)["route_optimization"]["data"]
    assert routes["provider_status"] == "not_configured"
    assert routes["routes"] == []


def test_transport_setup_failure_returns_provider_status(client, headers, monkeypatch, road_trip):
    def misconfigured_client(**kwargs):
        raise ImportError("provider transport could not initialize")

    monkeypatch.setenv("TRADEPILOT_OSRM_URL", "https://routes.example")
    monkeypatch.setattr(road_guidance, "httpx", SimpleNamespace(Client=misconfigured_client, Timeout=httpx.Timeout))
    agents = analyze(client, headers, road_trip)
    assert agents["route_optimization"]["status"] == "completed"
    assert agents["route_optimization"]["data"]["provider_status"] == "routing_unavailable"
    assert agents["logistics_orchestrator"]["data"]["decision"]["candidate"] is None


def setup_truck(monkeypatch, road_trip, include_tolls=True):
    from flexpolyline import encode

    road_trip["road_guidance"]["truck"] = dict(
        height_cm=350, width_cm=240, length_cm=850, tare_weight_kg=4200,
        loaded_weight_kg=7000, gross_weight_rating_kg=9000, max_axle_weight_kg=4000,
        axle_count=2,
    )
    departure = datetime.fromisoformat(road_trip["road_guidance"]["departure_at"])
    timestamps = [(departure + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(48)]
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.host == "router.hereapi.com":
            assert request.url.params["transportMode"] == "truck"
            assert request.url.params["vehicle[currentWeight]"] == "7000"
            assert request.url.params["vehicle[height]"] == "350"
            assert request.url.params["departureTime"] == departure.isoformat()
            assert request.url.params["tolls[summaries]"] == "total"
            routes = []
            for index, (duration, toll) in enumerate(((36000, 300), (43200, 1200), (46800, 700))):
                shape = encode([(9.9312, 76.2673), (10.8 + index, 77.9), (13.0827, 80.2707)])
                summary = dict(length=700000, duration=duration, baseDuration=duration - 3600)
                if include_tolls or index != 2:
                    summary["tolls"] = {"total": {"type": "value", "currency": "INR", "value": toll}}
                section = dict(summary=summary, polyline=shape)
                if index == 0:
                    section["notices"] = [{"code": "violatedVehicleRestriction", "severity": "critical"}]
                routes.append({"sections": [section]})
            return httpx.Response(200, json={"routes": routes})
        points = len(request.url.params["latitude"].split(","))
        return httpx.Response(200, json=[
            dict(hourly_units=dict(precipitation="mm", wind_gusts_10m="km/h", visibility="m"),
                 hourly=dict(time=timestamps, precipitation=[0] * 48, wind_gusts_10m=[10] * 48,
                             visibility=[10000] * 48, weather_code=[0] * 48))
            for _ in range(points)
        ])
    monkeypatch.setenv("TRADEPILOT_HERE_TRUCK_ENABLED", "true")
    monkeypatch.setenv("TRADEPILOT_HERE_API_KEY", "unit-test-only")
    monkeypatch.setenv("TRADEPILOT_FORECAST_URL", "https://weather.example/v1/forecast")
    monkeypatch.setattr(road_guidance, "httpx", SimpleNamespace(
        Client=lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler), **kwargs),
        Timeout=httpx.Timeout, HTTPError=httpx.HTTPError,
    ))
    return calls


def test_truck_chooses_within_toll_budget_and_excludes_restriction(client, headers, monkeypatch, road_trip):
    calls = setup_truck(monkeypatch, road_trip)
    road_trip["road_guidance"]["max_toll_inr"] = 800
    result = analyze(client, headers, road_trip)["route_optimization"]["data"]
    preferred = result["recommended_route"]
    assert preferred["transit_hours_estimate"] == 13
    assert preferred["truck_evidence"]["base_duration_hours"] == 12
    assert preferred["truck_evidence"]["time_aware_delta_hours"] == 1
    assert preferred["truck_evidence"]["toll_inr"] == 700
    assert preferred["truck_evidence"]["access_status"] == "no_reported_violation"
    assert any("truck restriction" in route["feasibility_reason"] for route in result["routes"] if not route["feasible"])
    assert any("Toll estimate exceeds" in route["feasibility_reason"] for route in result["routes"] if not route["feasible"])
    assert len(calls) == 2


def test_missing_toll_is_not_zero_and_truck_mode_never_falls_back(client, headers, monkeypatch, road_trip):
    calls = setup_truck(monkeypatch, road_trip, include_tolls=False)
    road_trip["road_guidance"]["max_toll_inr"] = 800
    result = analyze(client, headers, road_trip)["route_optimization"]["data"]
    assert result["recommended_route"] is None
    assert result["provider_status"] == "truck_evidence_incomplete"
    unknown = next(route for route in result["routes"] if route["truck_evidence"]["toll_status"] == "unavailable")
    assert unknown["truck_evidence"]["toll_inr"] is None
    monkeypatch.setenv("TRADEPILOT_OSRM_URL", "https://routes.example")
    monkeypatch.setenv("TRADEPILOT_HERE_TRUCK_ENABLED", "false")
    disabled = analyze(client, headers, road_trip)["route_optimization"]["data"]
    assert disabled["provider_status"] == "not_configured" and disabled["routes"] == []
    assert len(calls) == 2
