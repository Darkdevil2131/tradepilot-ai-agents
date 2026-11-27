from __future__ import annotations
from typing import Annotated, TypedDict
from operator import or_
from math import isclose
from decimal import Decimal
from uuid import uuid4
from datetime import datetime, timezone
from langgraph.graph import StateGraph, START, END
from ..models import AgentResult, DecisionGate, Provenance, ShipmentRequest, ShipmentAnalysis, StrictModel
from ..tools import TRANSPORT_DOCUMENTS
from .route import RouteOptimizationAgent
from .carrier import CarrierResearchAgent, published_service_leads, source_leg_plans
from .risk import RiskComplianceAgent
from .documents import DocumentIntelligenceAgent
from .procurement import MarketProcurementAgent
from .explanation import explain_decision

class WorkflowState(TypedDict, total=False):
    shipment: ShipmentRequest
    shipment_id: str
    results: Annotated[dict[str, AgentResult], or_]
    retry_counts: Annotated[dict[str, int], or_]
    created_at: datetime
    verification: dict

class LogisticsOrchestrator:
    """LangGraph workflow; tool-backed deterministic agents run without an LLM."""
    def __init__(self, max_attempts:int=2):
        self.max_attempts=max(1,min(max_attempts,3))
        self.route=RouteOptimizationAgent(); self.carrier=CarrierResearchAgent(); self.risk=RiskComplianceAgent(); self.documents=DocumentIntelligenceAgent(); self.procurement=MarketProcurementAgent()
        builder=StateGraph(WorkflowState)
        builder.add_node("route_optimization",self._route)
        builder.add_node("carrier_research",self._carrier)
        builder.add_node("risk_compliance",self._risk)
        builder.add_node("document_intelligence",self._documents)
        builder.add_node("market_procurement",self._procurement)
        builder.add_node("verify", self._verify)
        builder.add_node("reconsider", self._reconsider)
        builder.add_edge(START,"route_optimization")
        builder.add_edge(START,"document_intelligence")
        builder.add_edge("route_optimization","carrier_research")
        builder.add_edge("route_optimization","risk_compliance")
        builder.add_edge(["document_intelligence", "carrier_research", "risk_compliance"], "market_procurement")
        builder.add_edge("market_procurement", "verify")
        builder.add_conditional_edges("verify", self._verification_branch, {"reconsider": "reconsider", "finish": END})
        builder.add_edge("reconsider", END)
        self.graph=builder.compile()

    def _safe(self,key:str,fn):
        last=None
        for attempt in range(1,self.max_attempts+1):
            try:
                data=fn()
                if isinstance(data,StrictModel): data=data.model_dump(mode="json")
                result = AgentResult(agent=key,status="completed",provenance=self._provenance(data),data=data)
                return {"results":{key:result},"retry_counts":{key:attempt}}
            except Exception as exc:
                last=f"{key} failed after {attempt} attempt(s): {type(exc).__name__}"
        result = AgentResult(agent=key,status="failed",warnings=[last or "Agent failed; no exception details exposed"])
        return {"results":{key:result},"retry_counts":{key:attempt}}
    @staticmethod
    def _provenance(data:dict)->list[Provenance]:
        raw=data.get("provenance")
        if isinstance(raw,str):
            try:return [Provenance(raw)]
            except ValueError:return [Provenance.ESTIMATED]
        return [Provenance.ESTIMATED]
    def _route(self,s):
        update = self._safe("route_optimization",lambda:self.route.run(s["shipment"]))
        result = update["results"]["route_optimization"]
        from ..tools import CITY_COORDS
        unknown = [city for city in (s["shipment"].origin, s["shipment"].destination) if city.casefold() not in CITY_COORDS]
        if unknown and result.status == "failed":
            result.warnings.append("No offline route evidence for: " + ", ".join(unknown))
        return update
    def _carrier(self,s):
        route=s.get("results",{}).get("route_optimization")
        modes={r["mode"] for r in route.data.get("routes",[]) if r.get("feasible")} if route and route.status=="completed" else set()
        if route and route.status == "completed" and route.data.get("impact_comparison"):
            modes = {leg["mode"] for option in route.data["impact_comparison"]["options"]
                     for portion in option["portions"] for leg in portion["legs"]}
        def investigate():
            result = self.carrier.run(s["shipment"], modes)
            comparison = route.data.get("impact_comparison") if route else None
            result.published_services = published_service_leads(route.data) if route else []
            if comparison:
                result.leg_plans = source_leg_plans(s["shipment"], comparison["options"])
            return result
        return self._safe("carrier_research",investigate)
    def _risk(self,s):
        route = s.get("results", {}).get("route_optimization")
        options = route.data.get("routes") if route and route.status == "completed" else None
        if route and route.status == "completed" and route.data.get("impact_comparison"):
            comparison = route.data["impact_comparison"]
            options = [
                dict(feasible=True, provenance="SYNTHETIC",
                     legs=[leg for portion in row["portions"] for leg in portion["legs"]],
                     disruption_ids=sorted({signal for portion in row["portions"] for signal in portion["disruption_ids"]}))
                for row in comparison["options"]
            ]
        return self._safe("risk_compliance",lambda:self.risk.run(s["shipment"], options))
    def _documents(self,s): return self._safe("document_intelligence",lambda:self.documents.run(s["shipment"]))
    def _procurement(self,s):
        route=s.get("results",{}).get("route_optimization")
        routes = route.data.get("routes", []) if route and route.status == "completed" else []
        carrier = s["results"]["carrier_research"]
        shipment = s["shipment"]
        def compare_supported():
            plans = carrier.data.get("leg_plans", [])
            decision = (self.procurement.assess_split_offers(shipment, plans) if shipment.impact_scenario else
                        self.procurement.assess_shipment_offers(shipment, routes, carrier.data.get("carriers", [])))
            if shipment.impact_scenario:
                decision.plan_candidates = sorted(
                    [plan for plan in plans if all(leg["freight_inr"] is not None for leg in plan["legs"])],
                    key=lambda plan: (plan["total_impact_inr"], len(plan["legs"]), plan["option"]),
                )
                scopes = {plan.get("insurance_scope") for plan in decision.plan_candidates}
                if shipment.requires_submitted_offers and (len(scopes) > 1 or "partial_or_unknown" in scopes):
                    decision.plan_candidates = []
                    decision.recommendation_warning = "Normalize insurance coverage across the submitted leg quotes before choosing a plan."
                decision.sourced_plan = next(iter(decision.plan_candidates), None)
                if not decision.sourced_plan and shipment.requires_submitted_offers and len(scopes) == 0:
                    decision.recommendation_warning = "Supply a qualifying INR quote and service record for each required leg."
            if any(flag["severity"] == "high" for flag in s["results"]["risk_compliance"].data.get("flags", [])) or s["results"]["risk_compliance"].status != "completed":
                decision.sourced_plan = None
                decision.plan_candidates = []
                decision.recommendation = None
                decision.recommendation_warning = "Cargo acceptance needs human review."
            return decision
        return self._safe("market_procurement",compare_supported)

    @staticmethod
    def _candidate_errors(state, candidate):
        routes = state["results"]["route_optimization"].data
        shipment = state["shipment"]
        if "option" not in candidate:
            ids = (candidate.get("evidence") or {}).get("route_leg_ids")
            if not any(row["feasible"] and row["mode"] == candidate["mode"]
                       and (ids is None or ids == row["evidence_ids"]) for row in routes.get("routes", [])):
                return ["Quote does not point to a feasible route."]
            return []
        option = next((row for row in (routes.get("impact_comparison") or {}).get("options", [])
                       if row["option"] == candidate["option"]), None)
        if option is None:
            return ["Commercial plan does not identify a feasible route option."]
        portions = option["portions"]
        if (not isclose(sum(portion["weight_kg"] for portion in portions), shipment.weight_kg)
                or portions[0]["weight_kg"] < shipment.impact_scenario.critical_weight_kg):
            return ["Plan does not preserve the full shipment and its critical cargo."]
        loads = {}
        for portion in portions:
            legs = portion["legs"]
            if (not legs or legs[0]["origin"].casefold().removesuffix(" warehouse") != shipment.origin.casefold()
                    or legs[-1]["destination"].casefold().removesuffix(" warehouse") != shipment.destination.casefold()
                    or any(a["destination"] != b["origin"] for a, b in zip(legs, legs[1:]))):
                return ["A portion has disconnected route legs or incorrect endpoints."]
            for leg in legs:
                loads[leg["id"]] = loads.get(leg["id"], 0) + portion["weight_kg"]
                if loads[leg["id"]] > leg["capacity_kg"] + 1e-6:
                    return ["Aggregated load exceeds modeled leg capacity."]
        awards = {leg["leg"]: leg for leg in candidate["legs"]}
        if (len(awards) != len(candidate["legs"]) or awards.keys() != loads.keys()
                or any(not award["carrier_id"] or not isclose(award["weight_kg"], loads[key])
                       for key, award in awards.items())):
            return ["Commercial leg awards do not cover the route allocation."]
        freight = round(sum(Decimal(str(award["freight_inr"])) for award in awards.values()), 2)
        if (freight != Decimal(str(candidate["freight_inr"]))
                or round(freight + Decimal(str(option["delay_loss_inr"])), 2) != Decimal(str(candidate["total_impact_inr"]))):
            return ["The quoted leg totals and modeled loss do not reconcile."]
        return []

    def _validate_candidate(self, state, candidate):
        try:
            return self._candidate_errors(state, candidate)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            return ["The candidate contains incomplete or invalid verification evidence."]

    @staticmethod
    def _transport_modes(state, candidate):
        route_data = state["results"]["route_optimization"].data
        if "option" in candidate:
            selected = next(row for row in route_data["impact_comparison"]["options"]
                            if row["option"] == candidate["option"])
            return {leg["mode"] for portion in selected["portions"] for leg in portion["legs"]}
        quote_ids = (candidate.get("evidence") or {}).get("route_leg_ids")
        route = next((row for row in route_data.get("routes", []) if row["evidence_ids"] == quote_ids), {})
        return {leg["mode"] for leg in route.get("legs", [])} or {candidate["mode"]}

    def _missing_transport_documents(self, state, candidate):
        shipment = state["shipment"]
        if not shipment.requires_submitted_offers:
            return []
        required = {TRANSPORT_DOCUMENTS[mode] for mode in self._transport_modes(state, candidate)}
        return sorted(required - {doc.document_type for doc in shipment.documents})

    def _verify(self, state):
        commercial = state["results"]["market_procurement"].data
        candidate = commercial.get("sourced_plan") or commercial.get("recommendation")
        errors = self._validate_candidate(state, candidate) if candidate else []
        missing = self._missing_transport_documents(state, candidate) if candidate and not errors else []
        return {"verification": {"candidate": candidate if not errors else None, "initial_candidate_errors": errors,
                                 "initial_missing_transport_documents": missing, "reconsidered": False}}

    @staticmethod
    def _verification_branch(state):
        verdict = state["verification"]
        commercial = state["results"]["market_procurement"].data
        alternatives = commercial.get("plan_candidates", [])
        comparison = state["results"]["route_optimization"].data.get("impact_comparison") or {}
        preferred = comparison.get("preferred")
        chosen = (verdict["candidate"] or {}).get("option")
        if alternatives and (verdict["initial_candidate_errors"] or verdict["initial_missing_transport_documents"]
                             or (preferred and chosen != preferred)):
            return "reconsider"
        return "finish"

    def _reconsider(self, state):
        verdict = state["verification"]
        alternatives = state["results"]["market_procurement"].data["plan_candidates"]
        valid = [plan for plan in alternatives if not self._validate_candidate(state, plan)]
        candidate = next((plan for plan in valid if not self._missing_transport_documents(state, plan)),
                         next(iter(valid), None))
        return {"verification": {**verdict, "candidate": candidate, "reconsidered": True,
                                 "reason": "Rechecked the alternatives against route, commercial and document evidence."}}

    def analyze(self,shipment:ShipmentRequest)->ShipmentAnalysis:
        sid=shipment.shipment_id or f"TP-{uuid4().hex[:12].upper()}"
        state=self.graph.invoke({"shipment":shipment,"shipment_id":sid,"results":{},"retry_counts":{},"created_at":datetime.now(timezone.utc)})
        results=state.get("results",{})
        if "document_intelligence" not in results:
            results["document_intelligence"]=AgentResult(agent="document_intelligence",status="failed",warnings=["Document checklist did not produce a result."])
        # The coordinator is represented explicitly in the API result.
        errors = [warning for result in results.values() if result.status == "failed" for warning in result.warnings]
        verification = state["verification"]
        results["logistics_orchestrator"]=AgentResult(agent="logistics_orchestrator",status="partial" if errors else "completed",provenance=[Provenance.USER_PROVIDED],data={"workflow":"LangGraph StateGraph","bounded_attempts":self.max_attempts,"errors":errors,"retry_counts":state.get("retry_counts",{}),"verification":{k:v for k,v in verification.items() if k != "candidate"}})
        required=["route_optimization","carrier_research","risk_compliance","market_procurement","document_intelligence"]
        failed=any(results[k].status=="failed" for k in required)
        documents = results["document_intelligence"].data
        risk = results["risk_compliance"].data
        candidate = verification["candidate"]
        route_data = results["route_optimization"].data
        impact_comparison = route_data.get("impact_comparison") or {}
        if candidate and shipment.requires_submitted_offers and results["document_intelligence"].status == "completed":
            missing = self._missing_transport_documents(state, candidate)
            documents["checked_transport_modes"] = sorted(self._transport_modes(state, candidate))
            if missing:
                documents["missing_document_types"] = sorted(set(documents["missing_document_types"]).union(missing))
                documents["valid"] = False
        review = list(documents.get("issues", []))
        review.extend("Supply or review " + kind for kind in documents.get("missing_document_types", []))
        review.extend(flag["reason"] for flag in risk.get("flags", []))
        if shipment.dimensions_cm is None:
            review.append("Supply largest package dimensions to verify service fit.")
        if shipment.road_guidance:
            review.append("Confirm posted restrictions, toll payment terms, and current road conditions with the carrier before dispatch.")
        gates = []
        has_feasible_route = (bool(impact_comparison.get("options")) if shipment.impact_scenario else
                              any(option.get("feasible") for option in route_data.get("routes", [])))
        if results["route_optimization"].status != "completed":
            gates.append(DecisionGate(gate="route_feasibility", status="blocked", reason="Route analysis failed.", evidence_path="/agents/route_optimization"))
        elif not has_feasible_route:
            gates.append(DecisionGate(gate="route_feasibility", status="blocked", reason="No route satisfies the configured shipment constraints.", evidence_path="/agents/route_optimization/data"))
        else:
            reason = "At least one alternative passed the configured route and capacity checks."
            gates.append(DecisionGate(gate="route_feasibility", status="passed", reason=reason, evidence_path="/agents/route_optimization/data"))
        if shipment.road_guidance:
            route_rows = route_data.get("routes", [])
            chosen_ids = {leg["leg"] for leg in (candidate or {}).get("legs", [])}
            selected_roads = [option for option in route_rows if option["weather"]
                              and set(option["evidence_ids"]) & chosen_ids]
            preferred = route_data.get("recommended_route") or {}
            quoted_ids = ((candidate or {}).get("evidence") or {}).get("route_leg_ids")
            if quoted_ids:
                preferred = next((option for option in route_data.get("routes", [])
                                  if option["evidence_ids"] == quoted_ids), preferred)
            if preferred.get("weather") and ((candidate or {}).get("mode") == "road" or not candidate):
                selected_roads = [preferred]
            if not candidate and not impact_comparison:
                selected_roads = [preferred] if preferred.get("weather") else []
            weather_rows = [row["weather"] for row in selected_roads]
            required_road = shipment.allowed_modes == ["road"] or bool(selected_roads)
            provider_status = route_data.get("provider_status") or "routing_unavailable"
            weather_status = ("blocked" if required_road and not weather_rows else
                              "review_required" if not weather_rows or any(w["severity"] == "elevated" for w in weather_rows)
                              else "passed")
            gates.append(DecisionGate(
                gate="route_weather", status=weather_status,
                reason=f"Provider status: {provider_status}. Selected provider road forecasts: "
                       + (", ".join(w["severity"] for w in weather_rows) if weather_rows else "none."),
                evidence_path="/agents/route_optimization/data",
            ))
            if shipment.road_guidance.truck and required_road:
                truck_rows = [r["truck_evidence"] for r in selected_roads if r["truck_evidence"]]
                truck_status = ("blocked" if not truck_rows else "review_required"
                                if any(r["access_status"] == "review_required" for r in truck_rows) else "passed")
                gates.append(DecisionGate(
                    gate="truck_route_evidence", status=truck_status,
                    reason="Truck eligibility is based on provider notices and the supplied vehicle profile."
                    if truck_rows else "No selectable alternative has complete truck route evidence within the stated constraints.",
                    evidence_path="/agents/route_optimization/data",
                ))
        if candidate is None:
            reason = ("; ".join(verification["initial_candidate_errors"]) if verification["initial_candidate_errors"]
                      else "No eligible provider-backed plan or quote is available.")
            gates.append(DecisionGate(gate="commercial_offer", status="blocked", reason=reason, evidence_path="/agents/market_procurement/data"))
        else:
            reason = ("Operator-submitted quotes passed route, coverage, capacity, timing and scope checks. Confirm them with the carrier."
                      if candidate.get("evidence") or candidate.get("provenance") == "USER_PROVIDED"
                      else "The modeled candidate uses demo carrier or tariff evidence.")
            gates.append(DecisionGate(gate="commercial_offer", status="passed", reason=reason, evidence_path="/agents/market_procurement/data"))
        if not documents.get("valid", False):
            gates.append(DecisionGate(gate="documents", status="blocked", reason="Required documents are missing or contain unresolved inconsistencies.", evidence_path="/agents/document_intelligence/data"))
        else:
            gates.append(DecisionGate(gate="documents", status="passed", reason="Submitted document fields passed configured validation checks.", evidence_path="/agents/document_intelligence/data"))
        if any(flag["severity"] == "high" for flag in risk.get("flags", [])):
            gates.append(DecisionGate(gate="compliance", status="blocked", reason="A high-severity cargo or compliance review is required.", evidence_path="/agents/risk_compliance/data/flags"))
        elif risk.get("human_review_required"):
            gates.append(DecisionGate(gate="compliance", status="review_required", reason="Compliance context needs human review before approval.", evidence_path="/agents/risk_compliance/data/flags"))
        else:
            gates.append(DecisionGate(gate="compliance", status="passed", reason="No configured compliance review rule fired.", evidence_path="/agents/risk_compliance/data"))
        blocked = any(gate.status == "blocked" for gate in gates)
        results["logistics_orchestrator"].data["decision"] = {
            "status": "incomplete" if failed else "blocked" if blocked else "ready_for_human_approval" if candidate else "no_supported_offer",
            "candidate": candidate if not failed and not blocked else None,
            "commercial_evidence": ("submitted" if candidate.get("evidence") or candidate.get("provenance") == "USER_PROVIDED"
                                    else "demo") if candidate and not blocked and not failed else "unavailable",
            "review_items": review,
            "requires_human_approval": True,
            "gates": [gate.model_dump() for gate in gates],
        }
        explanation = explain_decision(shipment, results, results["logistics_orchestrator"].data["decision"])
        results["logistics_orchestrator"].data["explanation"] = explanation.model_dump()
        prov={p for result in results.values() for p in result.provenance}
        declared = [r.get("provenance") for r in route_data.get("routes", [])]
        declared.extend(plan.get("provenance") for plan in results["carrier_research"].data.get("leg_plans", []))
        declared.append(impact_comparison.get("provenance"))
        declared.extend(lead["provenance"] for lead in results["carrier_research"].data.get("published_services", []))
        prov.update(Provenance(value) for value in declared if value)
        return ShipmentAnalysis(shipment_id=sid,status="partial" if failed else "completed",created_at=state["created_at"],persistence="sqlite_ephemeral_or_local",data_provenance=sorted(prov,key=lambda x:x.value),agents=results,summary=explanation.conclusion)
