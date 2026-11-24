from ..models import ShipmentRequest, DocumentValidationOutput
from ..tools import validate_documents
class DocumentIntelligenceAgent:
    name="document_intelligence"
    authorized_tools=("validate_documents",)
    def run(self,shipment:ShipmentRequest)->DocumentValidationOutput:
        return DocumentValidationOutput(**validate_documents(shipment))
