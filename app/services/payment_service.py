"""
Payment Mode Management Service for Noventra POS.
Handles:
- Automatic seeding of required payment modes (Cash, Card, EasyPaisa, JazzCash, Bank Transfer / Raast)
- Super Admin administration: add, rename, reorder, toggle status
- Cash mode deactivation prevention
"""
from typing import List, Dict, Any, Optional
from app.extensions import db
from app.models.payment import PaymentMode
from app.utils.timezone import utc_now


DEFAULT_PAYMENT_MODES = [
    {"mode_code": "cash", "mode_name": "Cash", "requires_ref": 0, "sort_order": 1, "is_active": 1},
    {"mode_code": "card", "mode_name": "Card", "requires_ref": 0, "sort_order": 2, "is_active": 1},
    {"mode_code": "easypaisa", "mode_name": "EasyPaisa", "requires_ref": 1, "sort_order": 3, "is_active": 1},
    {"mode_code": "jazzcash", "mode_name": "JazzCash", "requires_ref": 1, "sort_order": 4, "is_active": 1},
    {"mode_code": "bank_transfer", "mode_name": "Bank Transfer / Raast", "requires_ref": 1, "sort_order": 5, "is_active": 1},
]


class PaymentService:
    @classmethod
    def ensure_seed_modes(cls):
        """Ensures the core payment modes exist and are correctly named."""
        for item in DEFAULT_PAYMENT_MODES:
            mode = PaymentMode.query.filter_by(mode_code=item["mode_code"]).first()
            if not mode:
                mode = PaymentMode(
                    mode_code=item["mode_code"],
                    mode_name=item["mode_name"],
                    requires_ref=item["requires_ref"],
                    sort_order=item["sort_order"],
                    is_active=item["is_active"],
                )
                db.session.add(mode)
            else:
                # Update legacy names if present
                if mode.mode_code == "card" and mode.mode_name == "Debit / Credit Card":
                    mode.mode_name = "Card"
                elif mode.mode_code == "bank_transfer" and mode.mode_name == "Bank Transfer":
                    mode.mode_name = "Bank Transfer / Raast"
                # Ensure cash is always active
                if mode.mode_code == "cash":
                    mode.is_active = 1
        db.session.commit()

    @classmethod
    def get_active_modes(cls) -> List[PaymentMode]:
        """Returns all currently active payment modes ordered by sort_order."""
        cls.ensure_seed_modes()
        return PaymentMode.query.filter_by(is_active=1).order_by(PaymentMode.sort_order.asc(), PaymentMode.id.asc()).all()

    @classmethod
    def get_all_modes(cls) -> List[PaymentMode]:
        """Returns all payment modes including inactive ones."""
        cls.ensure_seed_modes()
        return PaymentMode.query.order_by(PaymentMode.sort_order.asc(), PaymentMode.id.asc()).all()

    @classmethod
    def create_mode(cls, mode_code: str, mode_name: str, requires_ref: int = 0, sort_order: int = 0) -> PaymentMode:
        code = mode_code.strip().lower().replace(" ", "_")
        if not code or not mode_name.strip():
            raise ValueError("Payment mode code and name are required.")
        if PaymentMode.query.filter_by(mode_code=code).first():
            raise ValueError(f"Payment mode with code '{code}' already exists.")

        mode = PaymentMode(
            mode_code=code,
            mode_name=mode_name.strip(),
            requires_ref=1 if requires_ref else 0,
            sort_order=int(sort_order or 0),
            is_active=1,
        )
        db.session.add(mode)
        db.session.commit()
        return mode

    @classmethod
    def update_mode(cls, mode_id: int, mode_name: str, requires_ref: int = 0, sort_order: int = 0) -> PaymentMode:
        mode = db.session.get(PaymentMode, mode_id)
        if not mode:
            raise ValueError(f"Payment mode with ID {mode_id} not found.")
        if not mode_name.strip():
            raise ValueError("Payment mode name cannot be empty.")

        mode.mode_name = mode_name.strip()
        mode.requires_ref = 1 if requires_ref else 0
        mode.sort_order = int(sort_order or 0)
        mode.updated_at = utc_now()
        db.session.commit()
        return mode

    @classmethod
    def toggle_mode_active(cls, mode_id: int) -> PaymentMode:
        mode = db.session.get(PaymentMode, mode_id)
        if not mode:
            raise ValueError(f"Payment mode with ID {mode_id} not found.")
        if mode.mode_code == "cash":
            raise ValueError("Cash payment mode cannot be deactivated.")

        mode.is_active = 0 if mode.is_active == 1 else 1
        mode.updated_at = utc_now()
        db.session.commit()
        return mode
