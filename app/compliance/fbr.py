"""
FBR (Federal Board of Revenue) Tier-1 Retailer POS Integration Client Stub.
Reserved for future FBR online fiscalization integration.
Currently raises NotImplementedError when invoked.
"""
from typing import Dict, Any


class FbrClient:
    """
    Client for communicating with FBR's Fiscal Data Ingestion API.
    Enabled via FBR_ENABLED=true in settings/environment.
    """

    def __init__(self, pos_registration_no: str = "", base_url: str = ""):
        self.pos_registration_no = pos_registration_no
        self.base_url = base_url

    def submit_invoice(self, invoice_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sends invoice data to FBR Tier-1 POS endpoint and returns FBR Invoice Number & QR Code.
        Stubbed for future implementation.
        """
        raise NotImplementedError(
            "FBR Tier-1 integration is not enabled or implemented in this stage. "
            "See README.md for FBR integration instructions."
        )

    def check_status(self, fbr_invoice_no: str) -> Dict[str, Any]:
        """Queries status of previously submitted invoice."""
        raise NotImplementedError("FBR status query is not implemented.")
