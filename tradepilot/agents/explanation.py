"""Evidence-linked explanation of the coordinator's calculated decision."""
from ..models import StrictModel
from pydantic import Field


class StrategicExplanation(StrictModel):
    conclusion: str
    objective: str
    evidence_scope: str
    comparisons: list[dict] = Field(default_factory=list)
    sensitivity: list[dict] = Field(default_factory=list)
    next_actions: list[dict] = Field(default_factory=list)
    rejected_options: list[dict] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


def explain_decision(shipment, results, decision) -> StrategicExplanation:
    route = results["route_optimization"].data
    comparison = route.get("impact_comparison") or {}
    candidate = decision["candidate"]
    plan = candidate if candidate and "option" in candidate else None
    report = StrategicExplanation(
        conclusion="A supported commercial recommendation is unavailable. Resolve the outstanding evidence before choosing a provider.",
        objective="Minimize submitted eligible quote amounts within the requested constraints.",
        evidence_scope="Computed from supplied records and synthetic service fixtures.",
        rejected_options=comparison.get("rejected", []),
        assumptions=comparison.get("assumptions", []),
    )
    for item in decision["review_items"]:
        report.next_actions.append(dict(action=item, owner="shipment reviewer",
                                        reason="Required to resolve an outstanding shipment uncertainty."))
    for name, result in results.items():
        if result.status == "failed":
            report.next_actions.append(dict(action=f"Resolve {name} failure and rerun the analysis.",
                                            owner="operator", reason="This specialist produced no usable result."))
    procurement = results["market_procurement"].data
    if procurement.get("recommendation_warning"):
        report.next_actions.append(dict(action=procurement["recommendation_warning"],
                                        owner="procurement reviewer", reason="Commercial recommendation withheld."))
    if decision["status"] == "incomplete":
        report.conclusion = "The investigation is incomplete because a required specialist failed."
        return report
    if decision["status"] == "blocked":
        blocked = [gate["gate"] for gate in decision["gates"] if gate["status"] == "blocked"]
        if "route_feasibility" in blocked:
            report.conclusion = "A feasible route is unavailable under the configured constraints. Review the rejected options and route evidence before changing the shipment."
            return report
        if "commercial_offer" in blocked:
            report.conclusion = "A supported arrangement is unavailable because no eligible provider-backed plan or quote remains. Resolve the listed gates before rerunning the investigation."
        else:
            report.conclusion = "No arrangement can proceed to approval until these gates are resolved: " + ", ".join(blocked) + "."
        return report
    if plan:
        scenario = shipment.impact_scenario
        report.objective = "Minimize synthetic freight plus modeled production stoppage loss, subject to leg and deadline constraints."
        options = {o["option"]: o for o in comparison["options"]}
        selected = options[plan["option"]]
        portions = ", ".join(f'{p["weight_kg"]:g} kg arriving on day {p["arrival_days"]:g}' for p in selected["portions"])
        report.conclusion = (
            f'Conditionally select {plan["option"]}: {portions}. '
            f'Synthetic freight is INR {plan["freight_inr"]:,.2f}; modeled stoppage loss is '
            f'INR {plan["delay_loss_inr"]:,.2f}; total impact is INR {plan["total_impact_inr"]:,.2f}. '
            'This is the minimum modeled impact among fully sourced feasible candidates in this snapshot.'
        )
        selected_late = max(0, selected["portions"][0]["arrival_days"] - scenario.target_days)
        for index, other in enumerate(results["carrier_research"].data.get("leg_plans", [])):
            if any(leg["freight_inr"] is None for leg in other["legs"]):
                continue
            freight = round(sum(leg["freight_inr"] for leg in other["legs"]), 2)
            total = round(freight + other["delay_loss_inr"], 2)
            report.comparisons.append(dict(option=other["option"], freight_inr=freight,
                delay_loss_inr=other["delay_loss_inr"], total_impact_inr=total,
                extra_impact_inr=round(total-plan["total_impact_inr"],2),
                evidence=f"/agents/carrier_research/data/leg_plans/{index}", provenance="SYNTHETIC"))
            other_late = max(0, options[other["option"]]["portions"][0]["arrival_days"] - scenario.target_days)
            days_difference = other_late-selected_late
            if days_difference:
                threshold = (plan["freight_inr"]-freight)/days_difference
                if threshold >= 0:
                    report.sensitivity.append(dict(alternative=other["option"],
                        equal_impact_daily_loss_inr=round(threshold,2),
                        selected_better_when="daily loss exceeds threshold" if days_difference>0 else "daily loss is below threshold",
                        assumption="Fixed feasible paths, capacities, critical weight and freight rates."))
        report.next_actions.append(dict(action="Confirm that the critical portion alone can restart production.",
            owner="operations", reason="The stoppage model ends the entire daily loss when the critical portion arrives."))
    elif candidate:
        report.conclusion = (
            f'Conditionally select {candidate["carrier_id"]} by {candidate["mode"]} at '
            f'{candidate["currency"]} {candidate["amount"]:,.2f}, the lowest eligible submitted amount. '
            'Confirm omitted charges and service commitments before approval.'
        )
        report.comparisons = [dict(**quote, evidence=f"/agents/market_procurement/data/ranked_quotes/{index}")
                              for index, quote in enumerate(procurement.get("ranked_quotes", []))]
        if candidate.get("evidence"):
            report.conclusion += " The submitted quote covers the selected route and meets the configured commercial checks."
        else:
            report.conclusion += " The carrier match uses demo evidence."
    if candidate:
        report.next_actions.append(dict(action="Review evidence and approve the selected arrangement.",
                                        owner="authorized approver", reason="A recommendation requires human authorization."))
    return report
