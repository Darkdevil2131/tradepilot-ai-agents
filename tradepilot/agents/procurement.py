from ..models import Quote, ShipmentRequest, ProcurementOutput
from ..tools import compare_quotes
class MarketProcurementAgent:
    name="market_procurement"
    authorized_tools=("compare_quotes",)
    def run(self,shipment:ShipmentRequest, feasible_modes:set[str]|None=None)->ProcurementOutput:
        return ProcurementOutput(**compare_quotes(shipment.quotes, set(shipment.allowed_modes) if feasible_modes is None else feasible_modes))
