from ..intelligence import extract_document_fields
from ..models import ShipmentRequest, DocumentValidationOutput
from ..tools import validate_documents


class DocumentIntelligenceAgent:
    name = "document_intelligence"
    authorized_tools = ("extract_document_fields", "validate_documents")

    def run(self, shipment: ShipmentRequest) -> DocumentValidationOutput:
        documents = []
        extracted_by_type = {}
        for index, document in enumerate(shipment.documents):
            extracted = extract_document_fields(document.raw_text)
            merged = {**extracted, **document.fields}
            documents.append(document.model_copy(update={"fields": merged}))
            if extracted:
                extracted_by_type[f"{document.document_type}:{index}"] = extracted
        enriched = shipment.model_copy(update={"documents": documents})
        result = validate_documents(enriched)
        result["extracted_fields"] = extracted_by_type
        return DocumentValidationOutput(**result)
