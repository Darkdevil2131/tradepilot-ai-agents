from ..intelligence import rank_routes
from ..models import ShipmentRequest, RouteOptimizationOutput
from ..road_guidance import provider_road_guidance
from ..tools import calculate_routes, impact_options


class RouteOptimizationAgent:
    name = "route_optimization"
    authorized_tools = ("calculate_routes", "impact_options", "rank_routes", "provider_road_guidance")

    def run(self, shipment: ShipmentRequest) -> RouteOptimizationOutput:
        if shipment.road_guidance:
            return provider_road_guidance(shipment)
        if shipment.impact_scenario:
            comparison = impact_options(shipment)
            return RouteOptimizationOutput(
                routes=[],
                recommended_route=None,
                no_feasible_route=not comparison["options"],
                impact_comparison=comparison,
                provenance="ESTIMATED",
                ranking_method="minimum total business impact (freight plus modeled delay loss)",
                alternatives=comparison["options"],
            )
        routes = rank_routes(shipment, calculate_routes(shipment))
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
            alternatives=alternatives,
        )
