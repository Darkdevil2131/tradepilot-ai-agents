from ..intelligence import rank_routes
from ..models import ShipmentRequest, RouteOptimizationOutput
from ..road_guidance import provider_road_guidance
from ..tools import CITY_COORDS, DEMO_INR_PER_USD, calculate_routes, fits_package, impact_options


class RouteOptimizationAgent:
    name = "route_optimization"
    authorized_tools = ("calculate_routes", "impact_options", "rank_routes", "provider_road_guidance")

    def run(self, shipment: ShipmentRequest) -> RouteOptimizationOutput:
        provider = None
        provider_routes = None
        if shipment.road_guidance:
            provider = provider_road_guidance(shipment)
            if shipment.allowed_modes == ["road"] and shipment.impact_scenario is None:
                return provider
            provider_routes = self._price_provider_routes(shipment, provider)
        if shipment.impact_scenario:
            comparison = impact_options(shipment, provider_routes)
            return RouteOptimizationOutput(
                routes=provider.routes if provider else [],
                recommended_route=None,
                no_feasible_route=not comparison["options"],
                impact_comparison=comparison,
                provenance="ESTIMATED",
                provider_status=provider.provider_status if provider else None,
                ranking_method="minimum total business impact (freight plus modeled delay loss)",
                alternatives=comparison["options"],
            )
        offline = ([] if provider is not None and any(place.casefold() not in CITY_COORDS
                                                      for place in (shipment.origin, shipment.destination))
                   else calculate_routes(shipment))
        if provider_routes is not None:
            offline = [route for route in offline if route["mode"] != "road"]
        routes = rank_routes(shipment, offline + (provider_routes or []))
        feasible = [route for route in routes if route["feasible"]]
        alternatives = [
            {
                "mode": route["mode"],
                "decision_score": route["decision_score"],
                "cost_estimate_usd": route["cost_estimate_usd"],
                "cost_estimate_inr": route.get("cost_estimate_inr"),
                "transit_hours_estimate": route["transit_hours_estimate"],
                "risk_score_estimate": route["risk_score_estimate"],
                "feasible": route["feasible"],
                "disruption_ids": route["disruption_ids"],
                "evidence_ids": route.get("evidence_ids", []),
                "provenance": route["provenance"],
                "reason": route["feasibility_reason"],
            }
            for route in routes
        ]
        return RouteOptimizationOutput(
            routes=routes,
            recommended_route=feasible[0] if feasible else None,
            no_feasible_route=not bool(feasible),
            provenance="ESTIMATED",
            provider_status=provider.provider_status if provider else None,
            alternatives=alternatives,
        )

    @staticmethod
    def _price_provider_routes(shipment: ShipmentRequest, provider: RouteOptimizationOutput) -> list[dict]:
        truck = shipment.road_guidance.truck
        capacity = truck.loaded_weight_kg - truck.tare_weight_kg if truck else 12000
        routes = []
        for option in provider.routes:
            row = option.model_dump(mode="json")
            rate = round(row["distance_km"] * .025, 3)
            row["modeled_freight_inr_per_kg"] = rate
            if not fits_package(shipment, "road"):
                row.update(feasible=False, mode_feasible=False,
                           feasibility_reason="Package exceeds the modeled road service dimensions.")
            row["capacity_kg"] = capacity
            if not shipment.impact_scenario and shipment.weight_kg > capacity:
                row.update(feasible=False, mode_feasible=False,
                           feasibility_reason="Cargo exceeds the modeled road payload.")
            row.update(
                cost_estimate_inr=round(rate * shipment.weight_kg, 2),
                cost_estimate_usd=round(rate * shipment.weight_kg / DEMO_INR_PER_USD, 2),
                legs=[{"id": row["evidence_ids"][0], "from": shipment.origin.casefold(),
                       "to": shipment.destination.casefold(), "mode": "road",
                       "hours": row["transit_hours_estimate"], "capacity_kg": capacity}],
            )
            routes.append(row)
        return routes
