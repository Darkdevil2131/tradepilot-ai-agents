from ..models import ShipmentRequest, RouteOptimizationOutput
from ..tools import calculate_routes, impact_options
class RouteOptimizationAgent:
    name="route_optimization"
    authorized_tools=("calculate_routes","impact_options")
    def run(self, shipment: ShipmentRequest) -> RouteOptimizationOutput:
        if shipment.impact_scenario:
            comparison = impact_options(shipment)
            return RouteOptimizationOutput(routes=[], recommended_route=None, no_feasible_route=not comparison["options"],
                                           impact_comparison=comparison, provenance="ESTIMATED")
        routes=calculate_routes(shipment)
        feasible=[r for r in routes if r["feasible"]]
        return RouteOptimizationOutput(routes=routes,recommended_route=feasible[0] if feasible else None,no_feasible_route=not bool(feasible),provenance="ESTIMATED")
