"""
Payment Gateway Registry and Factory.
"""
from typing import Dict, Type
from app.payments.base import PaymentGateway
from app.payments.gateways import (
    ManualGateway,
    HBLPayGateway,
    EasypaisaGateway,
    JazzCashGateway,
)

GATEWAY_REGISTRY: Dict[str, Type[PaymentGateway]] = {
    "manual": ManualGateway,
    "hblpay": HBLPayGateway,
    "easypaisa": EasypaisaGateway,
    "jazzcash": JazzCashGateway,
}


def get_payment_gateway(gateway_name: str = "manual") -> PaymentGateway:
    """Factory returning an instance of the configured payment gateway."""
    cls = GATEWAY_REGISTRY.get(gateway_name.lower())
    if not cls:
        raise ValueError(f"Unknown payment gateway: '{gateway_name}'. Registered: {list(GATEWAY_REGISTRY.keys())}")
    return cls()
