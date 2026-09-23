"""
Payment Gateway Implementations:
- ManualGateway (Active default for cash, physical card, on-account, cheque)
- HBLPayGateway (Future integration stub)
- EasypaisaGateway (Future integration stub)
- JazzCashGateway (Future integration stub)
"""
from typing import Dict, Any
from decimal import Decimal
from app.payments.base import PaymentGateway


class ManualGateway(PaymentGateway):
    """Active default gateway for on-premise, physical, and manual payments."""

    def authorize(self, amount: Decimal, currency: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "success": True,
            "transaction_id": metadata.get("reference", "MANUAL-TXN"),
            "status": "completed",
            "amount": str(amount),
            "currency": currency,
        }

    def capture(self, transaction_id: str) -> Dict[str, Any]:
        return {"success": True, "status": "captured"}

    def refund(self, transaction_id: str, amount: Decimal) -> Dict[str, Any]:
        return {"success": True, "status": "refunded", "amount": str(amount)}

    def status(self, transaction_id: str) -> Dict[str, Any]:
        return {"success": True, "status": "completed"}


class HBLPayGateway(PaymentGateway):
    """HBL Pay Gateway Stub (Future integration)."""

    def authorize(self, amount: Decimal, currency: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError("HBL Pay Gateway is not yet implemented.")

    def capture(self, transaction_id: str) -> Dict[str, Any]:
        raise NotImplementedError("HBL Pay Gateway is not yet implemented.")

    def refund(self, transaction_id: str, amount: Decimal) -> Dict[str, Any]:
        raise NotImplementedError("HBL Pay Gateway is not yet implemented.")

    def status(self, transaction_id: str) -> Dict[str, Any]:
        raise NotImplementedError("HBL Pay Gateway is not yet implemented.")


class EasypaisaGateway(PaymentGateway):
    """Easypaisa Gateway Stub (Future integration)."""

    def authorize(self, amount: Decimal, currency: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError("Easypaisa Gateway is not yet implemented.")

    def capture(self, transaction_id: str) -> Dict[str, Any]:
        raise NotImplementedError("Easypaisa Gateway is not yet implemented.")

    def refund(self, transaction_id: str, amount: Decimal) -> Dict[str, Any]:
        raise NotImplementedError("Easypaisa Gateway is not yet implemented.")

    def status(self, transaction_id: str) -> Dict[str, Any]:
        raise NotImplementedError("Easypaisa Gateway is not yet implemented.")


class JazzCashGateway(PaymentGateway):
    """JazzCash Gateway Stub (Future integration)."""

    def authorize(self, amount: Decimal, currency: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError("JazzCash Gateway is not yet implemented.")

    def capture(self, transaction_id: str) -> Dict[str, Any]:
        raise NotImplementedError("JazzCash Gateway is not yet implemented.")

    def refund(self, transaction_id: str, amount: Decimal) -> Dict[str, Any]:
        raise NotImplementedError("JazzCash Gateway is not yet implemented.")

    def status(self, transaction_id: str) -> Dict[str, Any]:
        raise NotImplementedError("JazzCash Gateway is not yet implemented.")
