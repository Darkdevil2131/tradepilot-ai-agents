from ..models import ShipmentRequest, RouteOptimizationOutput
from ..tools import calculate_routes
class RouteOptimizationAgent:
    name="route_optimization"
    authorized_tools=("calculate_routes",)
    def run(self, shipment: ShipmentRequest) -> RouteOptimizationOutput:
        routes=calculate_routes(shipment)
        feasible=[r for r in routes if r["feasible"]]
        return RouteOptimizationOutput(routes=routes,recommended_route=feasible[0] if feasible else None,no_feasible_route=not bool(feasible),provenance="ESTIMATED")
