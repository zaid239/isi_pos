"""
Abstract Payment Gateway Interface (Section 18).
Defines standard payment operations across all future payment service providers.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any
from decimal import Decimal


class PaymentGateway(ABC):

    @abstractmethod
    def authorize(self, amount: Decimal, currency: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Initiates an authorization or payment request."""
        pass

    @abstractmethod
    def capture(self, transaction_id: str) -> Dict[str, Any]:
        """Captures an authorized transaction."""
        pass

    @abstractmethod
    def refund(self, transaction_id: str, amount: Decimal) -> Dict[str, Any]:
        """Refunds a previously captured payment."""
        pass

    @abstractmethod
    def status(self, transaction_id: str) -> Dict[str, Any]:
        """Retrieves transaction status from payment gateway."""
        pass
