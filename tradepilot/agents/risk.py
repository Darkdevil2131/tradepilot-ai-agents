from ..models import ShipmentRequest, RiskComplianceOutput
from ..tools import CITY_COUNTRIES, contains_dangerous_goods_keyword


class RiskComplianceAgent:
    name = "risk_compliance"
    authorized_tools = ("local_rule_checks", "evaluate_user_disruptions")

    def run(self, shipment: ShipmentRequest, route_options: list[dict] | None = None) -> RiskComplianceOutput:
        hazardous = contains_dangerous_goods_keyword(shipment.cargo_description)
        flags = []
        if hazardous:
            flags.append({"code": "DANGEROUS_GOODS_REVIEW", "severity": "high", "reason": "Cargo description matches a hazardous-goods keyword; classification and required declarations need qualified human review."})
        if shipment.declared_value is None:
            flags.append({"code": "VALUE_NOT_PROVIDED", "severity": "medium", "reason": "Declared value is absent; insurance and landed-cost review are incomplete."})
        if shipment.incoterm is None:
            flags.append({"code": "INCOTERM_NOT_PROVIDED", "severity": "medium", "reason": "Incoterm is absent; allocation of freight, insurance, and customs responsibilities is unclear."})

        disruption_summary = []
        unaffected_plan_exists = any(option.get("feasible") for option in (route_options or []))
        for signal in shipment.disruptions:
            if not signal.active:
                continue
            summary = {
                "signal_id": signal.signal_id,
                "severity": signal.severity,
                "delay_hours": signal.delay_hours,
                "affected_modes": signal.affected_modes,
                "provenance": signal.provenance.value,
            }
            disruption_summary.append(summary)
            if signal.severity >= 0.75:
                flags.append({
                    "code": "HIGH_DISRUPTION_SIGNAL",
                    "severity": "high" if signal.severity >= 0.95 and not unaffected_plan_exists else "medium",
                    "reason": f"Active disruption {signal.signal_id} has severity {signal.severity:.2f}; affected routes require human confirmation.",
                })

        if route_options is not None:
            feasible = [option for option in route_options if option.get("feasible")]
            if not feasible:
                flags.append({"code": "NO_FEASIBLE_ROUTE", "severity": "high", "reason": "No route satisfies configured transport, capacity, dimension, deadline, and disruption constraints."})
            else:
                selected = feasible[0]
                if selected.get("risk_score_estimate", 0) >= 0.3:
                    flags.append({"code": "ELEVATED_ROUTE_RISK", "severity": "medium", "reason": "The leading path has an elevated deterministic risk score. Confirm schedule and handling controls before approval."})
                if selected.get("disruption_ids"):
                    flags.append({"code": "ROUTE_DISRUPTION_APPLIED", "severity": "medium", "reason": "The selected route was adjusted using user-provided disruption signals: " + ", ".join(selected["disruption_ids"])})
                if selected.get("provenance") == "SYNTHETIC":
                    flags.append({"code": "SYNTHETIC_ROUTE_EVIDENCE", "severity": "medium", "reason": "Route connectivity, capacity, duration, and cost derive from the synthetic network fixture."})

        if all(CITY_COUNTRIES.get(city.casefold()) == "india" for city in (shipment.origin, shipment.destination)):
            flags.append({"code": "DOMESTIC_DOCUMENT_REVIEW_REQUIRED", "severity": "medium",
                          "reason": "Confirm applicable domestic invoice, e-way bill and transport-document requirements. No live GST verification is configured."})
        else:
            flags.append({"code": "CUSTOMS_VERIFICATION_REQUIRED", "severity": "medium", "reason": "No live customs, sanctions, tariff, or jurisdiction screening is configured. Verify current requirements with authoritative sources."})
        return RiskComplianceOutput(
            flags=flags,
            human_review_required=bool(flags),
            checked_rules=[
                "hazard keyword screen",
                "declared value presence",
                "Incoterm presence",
                "route constraint outcome",
                "user-provided disruption severity",
                "synthetic route-risk score",
            ],
            provenance="USER_PROVIDED",
            live_screening=False,
            disruption_summary=disruption_summary,
        )
