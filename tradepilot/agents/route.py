from ..models import ShipmentRequest, RouteOptimizationOutput
from ..tools import calculate_routes
class RouteOptimizationAgent:
    name="route_optimization"
    authorized_tools=("calculate_routes",)
    def run(self, shipment: ShipmentRequest) -> RouteOptimizationOutput:
        routes=calculate_routes(shipment)
        feasible=[r for r in routes if r["deadline_feasible"]]
        return RouteOptimizationOutput(routes=routes,recommended_route=(feasible or routes)[0] if routes else None,no_deadline_feasible_route=bool(routes and not feasible),provenance="ESTIMATED")
