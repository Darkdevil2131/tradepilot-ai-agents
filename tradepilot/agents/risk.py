from ..models import ShipmentRequest, RiskComplianceOutput
from ..tools import contains_dangerous_goods_keyword
class RiskComplianceAgent:
    name="risk_compliance"
    authorized_tools=("local_rule_checks",)
    def run(self,shipment:ShipmentRequest)->RiskComplianceOutput:
        hazardous=contains_dangerous_goods_keyword(shipment.cargo_description)
        flags=[]
        if hazardous: flags.append({"code":"DANGEROUS_GOODS_REVIEW","severity":"high","reason":"Cargo description matches a hazardous-goods keyword; classification and required declarations need qualified human review."})
        if shipment.declared_value is None: flags.append({"code":"VALUE_NOT_PROVIDED","severity":"medium","reason":"Declared value is absent; insurance and landed-cost review are incomplete."})
        if shipment.incoterm is None: flags.append({"code":"INCOTERM_NOT_PROVIDED","severity":"medium","reason":"Incoterm is absent; allocation of freight, insurance, and customs responsibilities is unclear."})
        flags.append({"code":"CUSTOMS_VERIFICATION_REQUIRED","severity":"medium","reason":"No live customs, sanctions, tariff, or jurisdiction screening is configured. Verify current requirements with authoritative sources."})
        return RiskComplianceOutput(flags=flags,human_review_required=bool(flags),checked_rules=["hazard keyword screen","declared value presence","Incoterm presence"],provenance="USER_PROVIDED",live_screening=False)
