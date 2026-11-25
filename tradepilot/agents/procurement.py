from datetime import datetime, timedelta, timezone

from ..intelligence import rank_supplier_offers
from ..models import ProcurementOutput, QuoteAssessment, ShipmentRequest
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
        result["excluded_supplier_offers"] = suppliers["excluded"]
        result["supplier_recommendation"] = suppliers["recommendation"]
        result["supplier_warning"] = suppliers["warning"]
        return ProcurementOutput(**result)

    def assess_shipment_offers(self, shipment: ShipmentRequest, routes: list[dict], carriers: list[dict]) -> ProcurementOutput:
        """Qualify commercial evidence against the actual route candidates."""
        now = datetime.now(timezone.utc)
        departure = max(now, shipment.road_guidance.departure_at) if shipment.road_guidance else now
        eligible_routes = [route for route in routes if route["feasible"]]
        carriers_by_service = {(row["carrier_id"], row["mode"]): row for row in carriers}
        assessments = []
        eligible_quotes = []
        evidenced_quotes = []
        evidence_by_quote = {}
        for index, quote in enumerate(shipment.quotes):
            reasons = []
            carrier = carriers_by_service.get((quote.carrier_id, quote.mode))
            if carrier is None:
                reasons.append("carrier_lane_or_capacity_unconfirmed")
            matching_routes = [route for route in eligible_routes if route["mode"] == quote.mode]
            if not matching_routes:
                reasons.append("no_feasible_route_for_mode")
            if quote.valid_until and quote.valid_until.replace(tzinfo=quote.valid_until.tzinfo or timezone.utc) <= now:
                reasons.append("quote_expired")
            supplied = carrier and carrier["provenance"] == "USER_PROVIDED"
            if supplied:
                if not quote.source_reference or not quote.source_reference.strip() or quote.valid_until is None or quote.provenance != "USER_PROVIDED":
                    reasons.append("submitted_quote_evidence_incomplete")
                if not quote.door_to_door_total:
                    reasons.append("door_to_door_scope_unconfirmed")
                if quote.quoted_capacity_kg is None or quote.quoted_capacity_kg < shipment.weight_kg:
                    reasons.append("quote_capacity_unconfirmed_or_insufficient")
                bound = next((route for route in matching_routes if quote.route_leg_ids and
                              quote.route_leg_ids == route["evidence_ids"]), None)
                if bound is None:
                    reasons.append("quote_route_mismatch")
                if quote.transit_hours is None:
                    reasons.append("quote_transit_unconfirmed")
                elif shipment.deadline and departure + timedelta(hours=quote.transit_hours) > shipment.deadline:
                    reasons.append("quoted_transit_misses_deadline")
                if not reasons:
                    evidenced_quotes.append(quote)
                    evidence_by_quote[id(quote)] = dict(
                        quote_index=index, source_reference=quote.source_reference,
                        carrier_service_reference=carrier["coverage_reference"],
                        route_leg_ids=quote.route_leg_ids, quoted_transit_hours=quote.transit_hours,
                        modeled_transit_hours=bound["transit_hours_estimate"],
                        quoted_capacity_kg=quote.quoted_capacity_kg,
                        valid_until=quote.valid_until.isoformat(), includes_insurance=quote.includes_insurance,
                        provenance="USER_PROVIDED",
                    )
            assessments.append(dict(quote_index=index, carrier_id=quote.carrier_id, eligible=not reasons, reasons=reasons))
            if not reasons:
                eligible_quotes.append(quote)

        if shipment.requires_submitted_offers:
            for assessment, quote in zip(assessments, shipment.quotes):
                if assessment["eligible"] and id(quote) not in evidence_by_quote:
                    assessment.update(eligible=False, reasons=["demo_carrier_excluded_from_submitted_offer_comparison"])
            eligible_quotes = evidenced_quotes
        filtered = shipment.model_copy(update={"quotes": eligible_quotes})
        decision = self.run(filtered, {route["mode"] for route in eligible_routes})
        decision.quotes_received = len(shipment.quotes)
        decision.quote_assessments = [QuoteAssessment(**assessment) for assessment in assessments]
        if decision.recommendation and evidenced_quotes:
            cheapest = min(evidenced_quotes, key=lambda quote: (quote.amount, quote.carrier_id))
            decision.recommendation.evidence = evidence_by_quote[id(cheapest)]
        return decision
