from __future__ import annotations
from typing import TypedDict, Any
from uuid import uuid4
from datetime import datetime, timezone
from langgraph.graph import StateGraph, START, END
from ..models import AgentResult, Provenance, ShipmentRequest, ShipmentAnalysis, StrictModel
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
        def investigate():
            result = self.carrier.run(s["shipment"], modes)
            comparison = route.data.get("impact_comparison") if route else None
            if comparison:
                result.leg_plans = source_leg_plans(s["shipment"], comparison["options"])
            return result
        return self._safe(s,"carrier_research",investigate)
    def _risk(self,s): return self._safe(s,"risk_compliance",lambda:self.risk.run(s["shipment"]))
    def _documents(self,s): return self._safe(s,"document_intelligence",lambda:self.documents.run(s["shipment"]))
    def _procurement(self,s):
        route=s.get("results",{}).get("route_optimization")
        modes={r["mode"] for r in route.data.get("routes",[]) if r.get("feasible")} if route and route.status=="completed" else set()
        carrier = s["results"]["carrier_research"]
        supported = {(row["carrier_id"], row["mode"]) for row in carrier.data.get("carriers", [])}
        shipment = s["shipment"]
        matching = [q for q in shipment.quotes if (q.carrier_id, q.mode) in supported]
        filtered = shipment.model_copy(update={"quotes": matching})
        def compare_supported():
            decision = self.procurement.run(filtered, modes)
            candidates = []
            for plan in carrier.data.get("leg_plans", []):
                if all(leg["freight_inr"] is not None for leg in plan["legs"]):
                    total = round(sum(leg["freight_inr"] for leg in plan["legs"]), 2)
                    candidates.append(dict(**plan, freight_inr=total, total_impact_inr=round(total+plan["delay_loss_inr"],2),
                                           provenance="SYNTHETIC", requires_human_approval=True))
            decision.sourced_plan = min(candidates, key=lambda p:p["total_impact_inr"], default=None)
            decision.quotes_received = len(shipment.quotes)
            if len(matching) < len(shipment.quotes):
                decision.note += " Quotes without an eligible carrier/mode match were excluded."
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
        review = list(documents.get("issues", []))
        review.extend("Supply or review " + kind for kind in documents.get("missing_document_types", []))
        review.extend(flag["reason"] for flag in results["risk_compliance"].data.get("flags", []))
        if shipment.dimensions_cm is None:
            review.append("Supply largest package dimensions to verify service fit.")
        candidate = commercial.get("sourced_plan") or commercial.get("recommendation")
        results["logistics_orchestrator"].data["decision"] = {
            "status": "incomplete" if failed else "conditional_recommendation" if candidate else "no_supported_offer",
            "candidate": candidate if not failed else None,
            "review_items": review,
            "requires_human_approval": True,
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
