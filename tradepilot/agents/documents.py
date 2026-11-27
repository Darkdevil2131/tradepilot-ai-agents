from decimal import Decimal, InvalidOperation

from ..intelligence import read_document_labels
from ..models import ShipmentRequest, DocumentValidationOutput
from ..tools import validate_documents


class DocumentIntelligenceAgent:
    name = "document_intelligence"
    authorized_tools = ("read_document_labels", "validate_documents")

    def run(self, shipment: ShipmentRequest, selected_modes: set[str] | None = None) -> DocumentValidationOutput:
        documents = []
        extracted_by_type = {}
        conflicts = []
        for index, document in enumerate(shipment.documents):
            extracted, repeated_conflicts = read_document_labels(document.raw_text)
            conflicts.extend(f"{document.document_type} has contradictory labelled values for {field}"
                             for field in repeated_conflicts)
            supplied = {key: value for key, value in document.fields.items() if value is not None and str(value).strip()}
            for field in extracted.keys() & supplied.keys():
                left, right = extracted[field], supplied[field]
                if field in {"total", "package_count"}:
                    try:
                        agrees = Decimal(str(left)) == Decimal(str(right))
                    except InvalidOperation:
                        agrees = False
                else:
                    agrees = " ".join(str(left).casefold().split()) == " ".join(str(right).casefold().split())
                if not agrees:
                    conflicts.append(f"{document.document_type} {field} differs between document text and supplied fields")
            merged = {**extracted, **supplied}
            documents.append(document.model_copy(update={"fields": merged}))
            if extracted:
                extracted_by_type[f"{document.document_type}:{index}"] = extracted
        enriched = shipment.model_copy(update={"documents": documents})
        result = validate_documents(enriched, selected_modes)
        if conflicts:
            result["issues"].extend(sorted(conflicts))
            result["valid"] = False
        result["extracted_fields"] = extracted_by_type
        return DocumentValidationOutput(**result)
