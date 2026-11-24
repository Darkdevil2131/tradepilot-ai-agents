from ..intelligence import rank_supplier_offers
from ..models import ShipmentRequest, ProcurementOutput
from ..tools import compare_quotes


class MarketProcurementAgent:
    name = "market_procurement"
    authorized_tools = ("compare_quotes", "rank_supplier_offers")

    def run(self, shipment: ShipmentRequest, feasible_modes: set[str] | None = None) -> ProcurementOutput:
        result = compare_quotes(
            shipment.quotes,
            set(shipment.allowed_modes) if feasible_modes is None else feasible_modes,
        )
        suppliers = rank_supplier_offers(shipment)
        result["ranked_supplier_offers"] = suppliers["ranked"]
        result["supplier_recommendation"] = suppliers["recommendation"]
        result["supplier_warning"] = suppliers["warning"]
        return ProcurementOutput(**result)
