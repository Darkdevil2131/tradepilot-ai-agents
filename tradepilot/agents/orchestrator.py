from __future__ import annotations
from typing import TypedDict, Any
from uuid import uuid4
from datetime import datetime, timezone
from langgraph.graph import StateGraph, START, END
from ..models import AgentResult, DecisionGate, Provenance, ShipmentRequest, ShipmentAnalysis, StrictModel
from .route import RouteOptimizationAgent
from .carrier import CarrierResearchAgent, source_leg_plans
from .risk import RiskComplianceAgent
from .documents import DocumentIntelligenceAgent
from .procurement import MarketProcurementAgent
from .explanation import explain_decision

class WorkflowState(TypedDict, total=False):
    shipment: ShipmentRequest
    shipment_id: str
    results: dict[str, AgentResult]
    errors: list[str]
    retry_counts: dict[str,int]
    created_at: datetime

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
        builder.add_node("document_checklist",self._documents)
        builder.add_node("market_procurement",self._procurement)
        builder.add_node("finalize",self._finalize)
        builder.add_edge(START,"route_optimization")
        builder.add_edge("route_optimization","carrier_research")
        builder.add_edge("carrier_research","risk_compliance")
        builder.add_conditional_edges("risk_compliance",self._document_branch,{"documents":"document_intelligence","checklist":"document_checklist"})
        builder.add_edge("document_intelligence","market_procurement")
        builder.add_edge("document_checklist","market_procurement")
        builder.add_edge("market_procurement","finalize")
        builder.add_edge("finalize",END)
        self.graph=builder.compile()
    @staticmethod
    def _document_branch(state:WorkflowState)->str: return "documents" if state["shipment"].documents else "checklist"
    def _safe(self,state:WorkflowState,key:str,fn):
        results=dict(state.get("results",{})); errors=list(state.get("errors",[])); retries=dict(state.get("retry_counts",{}))
        last=None
        for attempt in range(1,self.max_attempts+1):
            retries[key]=attempt
            try:
                data=fn()
                if isinstance(data,StrictModel): data=data.model_dump(mode="json")
                results[key]=AgentResult(agent=key,status="completed",provenance=self._provenance(data),data=data)
                return {"results":results,"errors":errors,"retry_counts":retries}
            except Exception as exc:
                last=f"{key} failed after {attempt} attempt(s): {type(exc).__name__}"
        errors.append(last or f"{key} failed")
        results[key]=AgentResult(agent=key,status="failed",warnings=[last or "Agent failed; no exception details exposed"])
        return {"results":results,"errors":errors,"retry_counts":retries}
    @staticmethod
    def _provenance(data:dict)->list[Provenance]:
        raw=data.get("provenance")
        if isinstance(raw,str):
            try:return [Provenance(raw)]
            except ValueError:return [Provenance.ESTIMATED]
        return [Provenance.ESTIMATED]
    def _route(self,s):
        update = self._safe(s,"route_optimization",lambda:self.route.run(s["shipment"]))
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
            if comparison:
                result.leg_plans = source_leg_plans(s["shipment"], comparison["options"])
            return result
        return self._safe(s,"carrier_research",investigate)
    def _risk(self,s):
        route = s.get("results", {}).get("route_optimization")
        options = route.data.get("routes") if route and route.status == "completed" else None
        if route and route.status == "completed" and route.data.get("impact_comparison"):
            comparison = route.data["impact_comparison"]
            options = [
                dict(feasible=True, provenance="SYNTHETIC",
                     disruption_ids=sorted({signal for portion in row["portions"] for signal in portion["disruption_ids"]}))
                for row in comparison["options"]
            ]
        return self._safe(s,"risk_compliance",lambda:self.risk.run(s["shipment"], options))
    def _documents(self,s): return self._safe(s,"document_intelligence",lambda:self.documents.run(s["shipment"]))
    def _procurement(self,s):
        route=s.get("results",{}).get("route_optimization")
        routes = route.data.get("routes", []) if route and route.status == "completed" else []
        carrier = s["results"]["carrier_research"]
        shipment = s["shipment"]
        def compare_supported():
            decision = self.procurement.assess_shipment_offers(shipment, routes, carrier.data.get("carriers", []))
            candidates = []
            for plan in carrier.data.get("leg_plans", []):
                if all(leg["freight_inr"] is not None for leg in plan["legs"]):
                    total = round(sum(leg["freight_inr"] for leg in plan["legs"]), 2)
                    candidates.append(dict(**plan, freight_inr=total, total_impact_inr=round(total+plan["delay_loss_inr"],2),
                                           provenance="SYNTHETIC", requires_human_approval=True))
            if not shipment.requires_submitted_offers:
                decision.sourced_plan = min(candidates, key=lambda p:p["total_impact_inr"], default=None)
            elif carrier.data.get("leg_plans"):
                decision.recommendation_warning = "Submitted shipment-wide offers do not cover split legs. Supply leg-level evidence before commercial approval."
            if any(flag["severity"] == "high" for flag in s["results"]["risk_compliance"].data.get("flags", [])) or s["results"]["risk_compliance"].status != "completed":
                decision.sourced_plan = None
                decision.recommendation = None
                decision.recommendation_warning = "Cargo acceptance needs human review."
            return decision
        return self._safe(s,"market_procurement",compare_supported)
    def _finalize(self,s): return {}
    def analyze(self,shipment:ShipmentRequest)->ShipmentAnalysis:
        sid=shipment.shipment_id or f"TP-{uuid4().hex[:12].upper()}"
        state=self.graph.invoke({"shipment":shipment,"shipment_id":sid,"results":{},"errors":[],"retry_counts":{},"created_at":datetime.now(timezone.utc)})
        results=state.get("results",{})
        if "document_intelligence" not in results:
            results["document_intelligence"]=AgentResult(agent="document_intelligence",status="failed",warnings=["Document checklist did not produce a result."])
        # The coordinator is represented explicitly in the API result.
        results["logistics_orchestrator"]=AgentResult(agent="logistics_orchestrator",status="partial" if state.get("errors") else "completed",provenance=[Provenance.USER_PROVIDED],data={"workflow":"LangGraph StateGraph","bounded_attempts":self.max_attempts,"errors":state.get("errors",[]),"retry_counts":state.get("retry_counts",{})})
        required=["route_optimization","carrier_research","risk_compliance","market_procurement","document_intelligence"]
        failed=any(results[k].status=="failed" for k in required)
        commercial = results["market_procurement"].data
        documents = results["document_intelligence"].data
        risk = results["risk_compliance"].data
        review = list(documents.get("issues", []))
        review.extend("Supply or review " + kind for kind in documents.get("missing_document_types", []))
        review.extend(flag["reason"] for flag in risk.get("flags", []))
        if shipment.dimensions_cm is None:
            review.append("Supply largest package dimensions to verify service fit.")
        if shipment.road_guidance:
            review.append("Confirm posted restrictions, toll payment terms, and current road conditions with the carrier before dispatch.")
        candidate = commercial.get("sourced_plan") or commercial.get("recommendation")
        gates = []
        route_data = results["route_optimization"].data
        impact_comparison = route_data.get("impact_comparison") or {}
        has_feasible_route = bool(
            [option for option in route_data.get("routes", []) if option.get("feasible")]
            or impact_comparison.get("options", [])
        )
        if results["route_optimization"].status != "completed":
            gates.append(DecisionGate(gate="route_feasibility", status="blocked", reason="Route analysis failed.", evidence_path="/agents/route_optimization"))
        elif not has_feasible_route:
            gates.append(DecisionGate(gate="route_feasibility", status="blocked", reason="No route satisfies the configured shipment constraints.", evidence_path="/agents/route_optimization/data"))
        else:
            reason = ("A road alternative has provider geometry and passed the configured weather and deadline checks."
                      if shipment.road_guidance else "At least one route passed the configured route and capacity checks.")
            gates.append(DecisionGate(gate="route_feasibility", status="passed", reason=reason, evidence_path="/agents/route_optimization/data"))
        if shipment.road_guidance:
            preferred = route_data.get("recommended_route") or {}
            quoted_ids = ((candidate or {}).get("evidence") or {}).get("route_leg_ids")
            if quoted_ids:
                preferred = next((option for option in route_data.get("routes", [])
                                  if option["evidence_ids"] == quoted_ids), preferred)
            weather = preferred.get("weather") or {}
            provider_status = route_data.get("provider_status") or "routing_unavailable"
            weather_status = "blocked" if not preferred else "review_required" if weather.get("severity") == "elevated" else "passed"
            gates.append(DecisionGate(
                gate="route_weather", status=weather_status,
                reason=f"Provider status: {provider_status}. Selected route forecast flag: {weather.get('severity', 'unavailable')}.",
                evidence_path="/agents/route_optimization/data",
            ))
            if shipment.road_guidance.truck:
                truck = preferred.get("truck_evidence") or {}
                truck_status = ("blocked" if not truck else "review_required"
                                if truck["access_status"] == "review_required" else "passed")
                gates.append(DecisionGate(
                    gate="truck_route_evidence", status=truck_status,
                    reason="Truck eligibility is based on provider notices and the supplied vehicle profile."
                    if truck else "No selectable alternative has complete truck route evidence within the stated constraints.",
                    evidence_path="/agents/route_optimization/data",
                ))
        if candidate is None:
            gates.append(DecisionGate(gate="commercial_offer", status="blocked", reason="No eligible provider-backed plan or quote is available.", evidence_path="/agents/market_procurement/data"))
        else:
            reason = ("The submitted quote matches a feasible leg chain and passed scope, capacity, timing and expiry checks."
                      if candidate.get("evidence") else "The modeled candidate uses demo carrier or tariff evidence.")
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
            "commercial_evidence": ("submitted" if candidate.get("evidence") else "demo") if candidate and not blocked and not failed else "unavailable",
            "review_items": review,
            "requires_human_approval": True,
            "gates": [gate.model_dump() for gate in gates],
        }
        explanation = explain_decision(shipment, results, results["logistics_orchestrator"].data["decision"])
        results["logistics_orchestrator"].data["explanation"] = explanation.model_dump()
        prov={p for result in results.values() for p in result.provenance}
        # Mark synthetic static carrier fixtures accurately.
        if results.get("carrier_research") and results["carrier_research"].status=="completed": prov.add(Provenance.SYNTHETIC)
        if results.get("document_intelligence") and results["document_intelligence"].status=="completed": prov.add(Provenance.USER_PROVIDED)
        summary="Analysis completed using deterministic offline tools. No live carrier, route, customs, or market feeds were queried."
        if shipment.impact_scenario:
            prov.add(Provenance.SYNTHETIC)
            summary = ("See impact_comparison for synthetic connected paths ranked by freight plus stoppage loss. "
                       "Carrier leg_plans and procurement sourced_plan identify synthetic tariffs and outstanding quotations.")
        if failed: summary="Analysis is partial because one or more agents failed; inspect each agent status and warning."
        summary = explanation.conclusion
        return ShipmentAnalysis(shipment_id=sid,status="partial" if failed else "completed",created_at=state["created_at"],persistence="sqlite_ephemeral_or_local",data_provenance=sorted(prov,key=lambda x:x.value),agents=results,summary=summary)
